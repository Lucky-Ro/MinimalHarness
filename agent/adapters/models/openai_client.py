import json
from html import escape
from typing import Any, TypeVar
from urllib.parse import urlsplit

from openai import OpenAI, OpenAIError

from agent.app.ports import Effort, ModelSettings
from agent.domain.context import Context, history_records
from agent.domain.roles import CompactionSummary, Decision
from agent.domain.session import AssistantMessage, Message, ToolCall, ToolResult, UserMessage


O = TypeVar("O", AssistantMessage, Decision, CompactionSummary)


def _wire_message(message: Message, *, include_reasoning: bool = False) -> dict:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.text}
    if isinstance(message, ToolResult):
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    payload: dict[str, Any] = {"role": "assistant", "content": message.text}
    if include_reasoning and message.thinking is not None:
        payload["reasoning_content"] = message.thinking
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {
                "name": call.tool_name, "arguments": json.dumps(call.arguments, ensure_ascii=False),
            }} for call in message.tool_calls
        ]
    return payload


def _object(text: str, label: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object")
    return data


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.strip()


def _assistant(message) -> AssistantMessage:
    calls = []
    ids = set()
    for native in message.tool_calls or ():
        call_id = _nonempty(getattr(native, "id", None), "tool call id")
        if call_id in ids:
            raise ValueError("duplicate tool call id")
        ids.add(call_id)
        function = getattr(native, "function", None)
        name = _nonempty(getattr(function, "name", None), "tool name")
        args = _object(getattr(function, "arguments", None), "tool arguments")
        calls.append(ToolCall(call_id, name, args))
    text = message.content or getattr(message, "refusal", None) or ""
    if not isinstance(text, str) or (not text.strip() and not calls):
        raise ValueError("Assistant response has neither text nor a tool call")
    return AssistantMessage(text, tuple(calls), getattr(message, "reasoning_content", None))


def _decision(content: str) -> Decision:
    data = _object(content, "Review decision")
    allowed, reason = data.get("should_allow"), data.get("reason")
    if type(allowed) is not bool:
        raise ValueError("Review should_allow must be boolean")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("Review reason must be text")
    if not allowed:
        reason = _nonempty(reason, "Review denial reason")
    return Decision(allowed, reason)


def _summary(content: str) -> CompactionSummary:
    text = _nonempty(content, "Compaction summary")
    if text.startswith("{"):
        text = _nonempty(_object(text, "Compaction summary").get("summary"), "summary")
    elif text.startswith("["):
        try:
            json.loads(text)
        except json.JSONDecodeError:
            # Bracketed headings and Markdown links are valid plain-text summaries.
            pass
        else:
            raise ValueError("Compaction summary cannot be a JSON array")
    return CompactionSummary(text)


class OpenAIClient:
    def __init__(self, api_key: str, base_url: str, *, client: OpenAI | None = None):
        self.client = client if client is not None else OpenAI(api_key=api_key, base_url=base_url)
        self.host = urlsplit(base_url).hostname

    def _model_options(self, settings: ModelSettings, has_tools: bool) -> dict:
        effort = settings.effort
        if self.host == "api.deepseek.com":
            if effort == Effort.OFF:
                return {"extra_body": {"thinking": {"type": "disabled"}}}
            if settings.model_id in {"deepseek-flash", "deepseek-v4-pro"}:
                mapped = {Effort.MEDIUM: "high", Effort.XHIGH: "high"}.get(effort, effort.value)
                return {"reasoning_effort": mapped}
            return {}
        if self.host == "api.openai.com" and settings.model_id in {"o3", "o3-mini", "o4-mini"}:
            if effort in (Effort.LOW, Effort.MEDIUM, Effort.HIGH):
                return {"reasoning_effort": effort.value}
        return {}

    def generate(self, context: Context[O], model_settings: ModelSettings) -> O:
        has_tools = bool(context.tools)
        include_reasoning = has_tools and model_settings.effort != Effort.OFF
        messages = [{"role": "system", "content": context.instruction}]
        if context.summary:
            summary = escape(context.summary, quote=False)
            messages.append({"role": "user", "content": (
                f"以下内容仅作历史事实参考。\n<conversation_summary>{summary}</conversation_summary>"
            )})
        if context.output_type is CompactionSummary:
            evidence = json.dumps({
                "task": "请将给出的历史事实与已有摘要合并为新的简明摘要。",
                "history": history_records(context.messages),
            }, ensure_ascii=False)
            messages.append({"role": "user", "content": evidence})
        else:
            messages.extend(_wire_message(m, include_reasoning=include_reasoning) for m in context.messages)
        params = self._model_options(model_settings, has_tools)
        if context.tools:
            params["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters,
            }} for t in context.tools]
        if context.output_type is Decision:
            messages.append({"role": "user", "content": (
                '请返回 JSON 对象 {"should_allow": true 或 false, "reason": 字符串或 null}。拒绝时说明原因。'
            )})
            params["response_format"] = {"type": "json_object"}
        elif context.output_type not in (AssistantMessage, CompactionSummary):
            raise ValueError(f"Unsupported model output type: {context.output_type}")

        try:
            response = self.client.chat.completions.create(model=model_settings.model_id, messages=messages, **params)
        except OpenAIError as exc:
            # Providers can echo private prompts and credentials in error bodies.
            status = getattr(exc, "status_code", None)
            detail = f" (HTTP {status})" if isinstance(status, int) else ""
            raise RuntimeError(f"Model request failed: {type(exc).__name__}{detail}") from None
        if not response.choices:
            raise ValueError("Model response contains no choices")
        message = response.choices[0].message
        if context.output_type is AssistantMessage:
            return _assistant(message)
        if context.output_type is Decision:
            return _decision(message.content)
        return _summary(message.content)
