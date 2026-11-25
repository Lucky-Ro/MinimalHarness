import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, Generic, TypeVar

from .session import AssistantMessage, Message, ToolResult, UserMessage
from .tool import Tool


O = TypeVar("O")


@dataclass(frozen=True)
class Context(Generic[O]):
    instruction: str
    output_type: type[O]
    tools: tuple[Tool, ...] = ()
    messages: tuple[Message, ...] = ()
    summary: str | None = None
    memory: tuple[str, ...] = ()


def estimate_tokens(text: str) -> int:
    """Approximate 4 ASCII characters or 1.5 other characters per token."""
    ascii_count = sum(char.isascii() for char in text)
    other_count = len(text) - ascii_count
    return (3 * ascii_count + 8 * other_count + 11) // 12


def estimate_context_tokens(context: Context[Any]) -> int:
    parts = [context.instruction, context.summary or "", *context.memory]
    for tool in context.tools:
        parts.extend((tool.name, tool.description, json.dumps(tool.parameters, ensure_ascii=False)))
    for message in context.messages:
        if isinstance(message, (UserMessage, AssistantMessage)):
            parts.append(message.text)
        if isinstance(message, AssistantMessage):
            parts.extend(json.dumps(asdict(call), ensure_ascii=False) for call in message.tool_calls)
        elif isinstance(message, ToolResult):
            parts.extend((message.tool_call_id, message.content))
    return sum(estimate_tokens(part) for part in parts)


def history_records(messages: Iterable[Message]) -> list[dict[str, Any]]:
    """Describe transcript events as data for tasks that do not execute tools."""
    history = []
    for message in messages:
        fields = asdict(message)
        fields.pop("thinking", None)
        history.append({"kind": type(message).__name__, **fields})
    return history


def split_for_compaction(messages: list[Message], keep_recent: int = 4) -> tuple[list[Message], int]:
    if keep_recent < 0:
        raise ValueError("keep_recent must be nonnegative")
    boundary = max(0, len(messages) - keep_recent)
    # If the tail starts with a result, retain its entire tool-call batch.
    while 0 < boundary < len(messages) and isinstance(messages[boundary], ToolResult):
        boundary -= 1
    return messages[:boundary], boundary
