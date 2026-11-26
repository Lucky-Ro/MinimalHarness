import json
from dataclasses import asdict, dataclass, replace

from .context import Context, history_records
from .session import AssistantMessage, Message, Session, ToolCall, UserMessage
from .tool import Tool


@dataclass(frozen=True)
class Decision:
    should_allow: bool
    reason: str | None = None


@dataclass(frozen=True)
class CompactionSummary:
    summary: str


def _visible_messages(messages: list[Message]) -> tuple[Message, ...]:
    return tuple(replace(message, thinking=None) if isinstance(message, AssistantMessage)
                 else message for message in messages)


@dataclass(frozen=True)
class Assistant:
    instruction: str
    tools: tuple[Tool, ...] = ()

    def context(self, session: Session) -> Context[AssistantMessage]:
        return Context(instruction=self.instruction, output_type=AssistantMessage, tools=self.tools,
                       messages=tuple(session.active_messages()), summary=session.summary)


@dataclass(frozen=True)
class ToolCallReviewer:
    instruction: str

    def context(self, session: Session, call: ToolCall) -> Context[Decision]:
        # Review can run before some results arrive. Treat the transcript as evidence,
        # so the reviewer never receives an open tool call to continue itself.
        history = history_records(session.active_messages())
        evidence = json.dumps({"history": history, "proposed_call": asdict(call)}, ensure_ascii=False)
        return Context(instruction=self.instruction, output_type=Decision, summary=session.summary,
                       messages=(UserMessage(evidence),))


@dataclass(frozen=True)
class ContextCompactor:
    instruction: str

    def context(self, previous_summary: str | None, messages_to_compact: list[Message]) -> Context[CompactionSummary]:
        return Context(instruction=self.instruction, output_type=CompactionSummary,
                       summary=previous_summary, messages=_visible_messages(messages_to_compact))
