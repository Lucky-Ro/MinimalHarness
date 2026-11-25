from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ToolCall:
    id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    thinking: str | None = None


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


Message = UserMessage | AssistantMessage | ToolResult


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    summary: str | None = None
    compacted_until: int = 0

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def active_messages(self) -> list[Message]:
        return self.messages[self.compacted_until:]
