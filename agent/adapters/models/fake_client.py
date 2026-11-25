from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from agent.app.ports import ModelSettings
from agent.domain.context import Context
from agent.domain.roles import CompactionSummary, Decision
from agent.domain.session import AssistantMessage


O = TypeVar("O")
ModelOutput = AssistantMessage | Decision | CompactionSummary
Reply = ModelOutput | Exception | Callable[[Context[Any], ModelSettings], ModelOutput]


class FakeModelClient:
    """A scripted offline model. A function can answer from the actual context."""

    def __init__(self, responses: Iterable[Reply]):
        self._responses = iter(responses)
        self.calls: list[tuple[Context[Any], ModelSettings]] = []

    def generate(self, context: Context[O], model_settings: ModelSettings) -> O:
        self.calls.append((context, model_settings))
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise RuntimeError("FakeModelClient: no scripted responses left") from exc
        if isinstance(response, Exception):
            raise response
        output = response(context, model_settings) if callable(response) else response
        if not isinstance(output, context.output_type):
            raise TypeError(f"expected {context.output_type.__name__}, received {type(output).__name__}")
        return output
