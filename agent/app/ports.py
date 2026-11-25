from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TypeVar

from agent.domain.context import Context
from agent.domain.roles import Decision
from agent.domain.session import Session, ToolCall


class Effort(Enum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


@dataclass(frozen=True)
class ModelSettings:
    model_id: str
    effort: Effort = Effort.MEDIUM
    context_window: int = 64_000

    def __post_init__(self) -> None:
        if not self.model_id.strip() or self.context_window < 1:
            raise ValueError("模型名称不能为空，上下文窗口必须为正数")


O = TypeVar("O")


class ModelClient(Protocol):
    def generate(self, context: Context[O], model_settings: ModelSettings) -> O: ...


class SessionNotFoundError(LookupError):
    """The requested session has not been saved."""


class SessionStore(Protocol):
    def list(self) -> list[Session]: ...
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...


class ToolReviewer(Protocol):
    def review(self, session: Session, call: ToolCall) -> Decision: ...


class TraceLogger(Protocol):
    def log(self, event: str, payload: dict[str, Any]) -> None: ...


class NullTraceLogger:
    def log(self, event: str, payload: dict[str, Any]) -> None:
        pass
