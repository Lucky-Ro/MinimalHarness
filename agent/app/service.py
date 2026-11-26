from contextlib import nullcontext
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

from agent.domain.context import Context, estimate_context_tokens, split_for_compaction
from agent.domain.roles import Assistant, CompactionSummary, ContextCompactor, Decision
from agent.domain.session import AssistantMessage, Message, Session, ToolCall, ToolResult, UserMessage
from agent.domain.tool import ToolContext

from .ports import Effort, ModelClient, ModelSettings, SessionNotFoundError, SessionStore, ToolReviewer, TraceLogger


@dataclass
class AgentService:
    model_client: ModelClient
    session_store: SessionStore
    assistant: Assistant
    reviewer: ToolReviewer
    compactor: ContextCompactor
    assistant_model_settings: ModelSettings
    compactor_model_settings: ModelSettings
    max_iterations: int = 8
    compaction_threshold: float = 0.7
    keep_recent_messages: int = 4
    tracer: TraceLogger | None = None

    def __post_init__(self):
        if self.max_iterations < 1 or self.keep_recent_messages < 0 or not 0 < self.compaction_threshold <= 1:
            raise ValueError("Invalid iteration limit or compaction settings")
        self._tools = {tool.name: tool for tool in self.assistant.tools}
        if len(self._tools) != len(self.assistant.tools):
            raise ValueError("Registered tool names must be unique")

    def _log(self, event: str, session: Session, **fields: Any) -> None:
        if self.tracer is not None:
            self.tracer.log(event, {"session_id": session.id, **fields})

    def _lock(self, session_id: str):
        lock = getattr(self.session_store, "lock", None)
        return lock(session_id) if lock is not None else nullcontext()

    def new_session(self, session_id: str | None = None) -> Session:
        session = Session() if session_id is None else Session(id=session_id)
        with self._lock(session.id):
            try:
                self.session_store.load(session.id)
            except SessionNotFoundError:
                self.session_store.save(session)
            else:
                raise ValueError(f"Session already exists: {session.id}")
        self._log("session_created", session)
        return session

    def list_sessions(self) -> list[Session]:
        return self.session_store.list()

    def get_session(self, session_id: str) -> Session:
        return self.session_store.load(session_id)

    def get_effort(self) -> Effort:
        return self.assistant_model_settings.effort

    def set_effort(self, effort: Effort) -> ModelSettings:
        if not isinstance(effort, Effort):
            raise ValueError("effort must be an Effort instance")
        previous = self.assistant_model_settings.effort
        self.assistant_model_settings = replace(self.assistant_model_settings, effort=effort)
        if self.tracer is not None:
            self.tracer.log("effort_changed", {
                "previous_effort": previous.value,
                "effort": effort.value,
                "model": self.assistant_model_settings.model_id,
            })
        return self.assistant_model_settings

    def _append(self, session: Session, message: Message) -> None:
        session.append(message)
        self.session_store.save(session)

    def _close_interrupted_batch(self, session: Session) -> None:
        """Complete a persisted partial exchange without replaying possible side effects."""
        completed = set()
        for previous in reversed(session.messages):
            if isinstance(previous, ToolResult):
                completed.add(previous.tool_call_id)
                continue
            if isinstance(previous, AssistantMessage):
                for call in previous.tool_calls:
                    if call.id not in completed:
                        result = ToolResult(call.id, "上次调用中断，执行结果未知，请先核实。", is_error=True)
                        self._append(session, result)
                        self._log("tool_error", session, tool_call_id=call.id, tool_name=call.tool_name,
                                  error=result.content)
            break

    def chat(self, session_id: str, user_message: str) -> list[Message]:
        with self._lock(session_id):
            session = self.get_session(session_id)
            start = len(session.messages)
            self._close_interrupted_batch(session)
            self._append(session, UserMessage(user_message))
            self._log("user_message", session)
            self._run_turn(session)
            return session.messages[start:]

    def _run_turn(self, session: Session) -> None:
        attempted = False
        limit = int(self.assistant_model_settings.context_window * self.compaction_threshold)
        for iteration in range(1, self.max_iterations + 1):
            settings = self.assistant_model_settings
            context = self.assistant.context(session)
            if not attempted and estimate_context_tokens(context) >= limit:
                attempted = True
                context = self._compact(session, context)

            try:
                reply = self.model_client.generate(context, settings)
                if not isinstance(reply, AssistantMessage):
                    raise TypeError("Expected AssistantMessage from the main model")
            except Exception as exc:
                self._log("model_error", session, iteration=iteration, error=str(exc))
                raise
            self._append(session, reply)
            self._log("model_called", session, iteration=iteration, model=settings.model_id,
                      effort=settings.effort.value, has_thinking=bool(reply.thinking),
                      tool_call_count=len(reply.tool_calls))
            if not reply.tool_calls:
                return
            for call in reply.tool_calls:
                self._append(session, self._review_and_execute(session, call))

        self._log("max_iterations_reached", session, max_iterations=self.max_iterations)
        self._append(session, AssistantMessage("已达到最大工具调用轮次上限，请尝试拆分任务或补充信息后继续。"))

    def _compact(self, session: Session, original: Context[AssistantMessage]) -> Context[AssistantMessage]:
        # The current user input or tool batch must remain available to complete the turn.
        keep = max(1, self.keep_recent_messages)
        messages, delta = split_for_compaction(session.active_messages(), keep)
        if not delta:
            self._log("compaction_skipped", session, reason="no older messages")
            return original
        self._log("compaction_triggered", session, message_count=delta, model=self.compactor_model_settings.model_id)
        try:
            context = self.compactor.context(session.summary, messages)
            result = self.model_client.generate(context, self.compactor_model_settings)
            if not isinstance(result, CompactionSummary) or not result.summary.strip():
                raise ValueError("Compactor returned an empty or invalid summary")
        except Exception as exc:
            self._log("compaction_failed", session, error=str(exc))
            return original
        session.summary = result.summary
        session.compacted_until += delta
        self.session_store.save(session)
        self._log("compaction_succeeded", session, compacted_until=session.compacted_until)
        return self.assistant.context(session)

    def _review_and_execute(self, session: Session, call: ToolCall) -> ToolResult:
        fields = {"tool_name": call.tool_name, "tool_call_id": call.id}
        try:
            decision = self.reviewer.review(session, call)
            if not isinstance(decision, Decision) or type(decision.should_allow) is not bool:
                raise ValueError("Invalid review decision")
        except Exception as exc:
            decision = Decision(False, f"Reviewer error ({type(exc).__name__}): {exc}")
        self._log("tool_reviewed", session, **fields, should_allow=decision.should_allow, reason=decision.reason)
        if not decision.should_allow:
            return ToolResult(call.id, f"Tool call rejected: {decision.reason or 'denied by reviewer'}", True)
        start = monotonic()
        tool = self._tools.get(call.tool_name)
        if tool is None:
            error = f"Unknown tool: {call.tool_name}"
        else:
            try:
                output = tool.execute(call.arguments, ToolContext(session.id))
            except Exception as exc:
                error = f"Tool execution error ({type(exc).__name__}): {exc}"
            else:
                self._log("tool_executed", session, **fields, elapsed_ms=(monotonic() - start) * 1000)
                return ToolResult(call.id, output)
        self._log("tool_error", session, **fields, error=error)
        return ToolResult(call.id, error, True)
