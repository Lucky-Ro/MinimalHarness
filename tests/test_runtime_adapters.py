import io
import json

import pytest

from agent.app.ports import ModelSettings
from agent.domain.roles import Assistant, Decision
from agent.domain.session import AssistantMessage, Session


def test_scripted_model_preserves_call_history_and_signals_invalid_responses():
    from agent.adapters.models.fake_client import FakeModelClient

    context = Assistant("task").context(Session())
    settings = ModelSettings("mock")
    model = FakeModelClient([
        AssistantMessage("one"),
        lambda ctx, used: AssistantMessage(f"{used.model_id}:{ctx.instruction}"),
        TimeoutError("busy"),
        Decision(True),
    ])
    assert model.generate(context, settings) == AssistantMessage("one")
    assert model.generate(context, settings) == AssistantMessage("mock:task")
    with pytest.raises(TimeoutError):
        model.generate(context, settings)
    with pytest.raises(TypeError):
        model.generate(context, settings)
    with pytest.raises(RuntimeError):
        model.generate(context, settings)
    assert model.calls == [(context, settings)] * 5


def test_trace_stream_has_one_structured_record_per_event():
    from agent.adapters.logging.trace_logger import JsonTraceLogger, MemoryTraceLogger

    sink = io.StringIO()
    logger = JsonTraceLogger(sink)
    memory = MemoryTraceLogger()
    payload = {"session_id": "北京", "is_error": False}
    for tracer in (logger, memory):
        tracer.log("tool_executed", payload)
    [line] = sink.getvalue().splitlines()
    record = json.loads(line)
    assert record["event"] == "tool_executed" and record["payload"] == payload
    assert memory.events == [("tool_executed", payload)]
