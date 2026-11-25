from types import SimpleNamespace

import pytest

from agent.adapters.reviewers.rule_reviewer import RuleBasedReviewer
from agent.adapters.stores.jsonl import JsonlSessionStore
from agent.adapters.tools.calculator import CalculatorTool
from agent.adapters.tools.todo import TodoTool
from agent.adapters.tools.weather import WeatherTool
from agent.app.ports import ModelSettings
from agent.domain.roles import Assistant, CompactionSummary, ContextCompactor, Decision
from agent.domain.session import AssistantMessage, Session, ToolCall, ToolResult, UserMessage


def runtime(data_dir, replies, *, tools=(), reviewer=None, **overrides):
    from agent.app.service import AgentService
    from agent.adapters.logging.trace_logger import MemoryTraceLogger
    from agent.adapters.models.fake_client import FakeModelClient

    model = FakeModelClient(replies)
    trace = MemoryTraceLogger()
    store = JsonlSessionStore(data_dir / "sessions")
    service = AgentService(
        model_client=model, session_store=store, assistant=Assistant("回答问题", tools),
        reviewer=reviewer if reviewer is not None else RuleBasedReviewer(tools),
        compactor=ContextCompactor("概括事实"), assistant_model_settings=ModelSettings("assistant"),
        compactor_model_settings=ModelSettings("flash"), tracer=trace, **overrides,
    )
    return service, model, trace


def request(call_id, tool_name, **arguments):
    return AssistantMessage(tool_calls=(ToolCall(call_id, tool_name, arguments),))


def echo_tool_result(context, settings):
    result = context.messages[-1]
    assert isinstance(result, ToolResult)
    return AssistantMessage(result.content)


def test_direct_follow_up_remembers_context_and_returns_increment_only(tmp_path):
    first = AssistantMessage("我记下了。", thinking="仅展示")
    second = AssistantMessage("你叫小雨。")
    service, model, trace = runtime(tmp_path, [first, second])
    session = service.new_session("notes")
    assert service.get_session("notes") == session
    assert service.chat("notes", "我叫小雨") == [UserMessage("我叫小雨"), first]
    assert service.chat("notes", "我叫什么？") == [UserMessage("我叫什么？"), second]
    context, settings = model.calls[-1]
    assert settings.model_id == "assistant"
    assert context.messages == (UserMessage("我叫小雨"), AssistantMessage("我记下了。"), UserMessage("我叫什么？"))
    assert service.get_session("notes").messages[1] == first
    assert [name for name, _ in trace.events].count("model_called") == 2


def test_two_windows_resume_with_isolated_weather_and_report_todos(tmp_path):
    first_tools = (WeatherTool(), TodoTool(tmp_path))
    steps = [
        request("weather", "weather", city="上海"),
        request("umbrella", "todo", action="add", title="查天气后带伞"),
        echo_tool_result,
        request("report", "todo", action="add", title="写周报"), echo_tool_result,
    ]
    service, model, trace = runtime(tmp_path, steps, tools=first_tools)
    window1, window2 = service.new_session().id, service.new_session().id
    first = service.chat(window1, "查上海天气，帮我记下带伞")
    assert [type(message) for message in first] == [
        UserMessage, AssistantMessage, ToolResult, AssistantMessage, ToolResult, AssistantMessage,
    ]
    assert first[2].tool_call_id == "weather" and "上海" in first[2].content
    assert first[4].tool_call_id == "umbrella" and "查天气后带伞" in first[-1].text
    assert model.calls[1][0].messages[-1] == first[2]
    assert model.calls[2][0].messages[-1] == first[4]
    assert "写周报" in service.chat(window2, "记录写周报")[-1].text
    assert set(s.id for s in service.list_sessions()) == {window1, window2}
    events = [name for name, _ in trace.events]
    assert events.count("tool_reviewed") == events.count("tool_executed") == 3

    query = request("query", "todo", action="list")
    resumed, resumed_model, _ = runtime(tmp_path, [query, echo_tool_result] * 2, tools=(TodoTool(tmp_path),))
    for window, included, excluded in ((window1, "查天气后带伞", "写周报"), (window2, "写周报", "查天气后带伞")):
        added = resumed.chat(window, "我刚才记了什么待办？")
        assert len(added) == 4 and included in added[-1].text and excluded not in added[-1].text
        assert added[2].tool_call_id == "query" and not added[2].is_error
        assert excluded not in str(resumed_model.calls[-1][0].messages)


@pytest.mark.parametrize(("kind", "expected"), [
    ("denied", "rejected"), ("unknown", "Unknown tool"), ("broken", "RuntimeError"),
])
def test_model_receives_review_and_execution_errors_and_can_finish(tmp_path, kind, expected):
    class BrokenTool:
        name = "broken"
        description = "模拟不可用服务"
        parameters = {"type": "object", "properties": {}}

        def execute(self, arguments, ctx):
            raise RuntimeError("不可用")

    reviewer = RuleBasedReviewer() if kind == "denied" else SimpleNamespace(review=lambda s, c: Decision(True))
    if kind == "denied":
        call = request("attempt", "bash", command="sudo ls")
    else:
        call = request("attempt", kind)
    service, model, trace = runtime(tmp_path, [call, AssistantMessage("本次无法执行。")],
                                    tools=(BrokenTool(),), reviewer=reviewer)
    session = service.new_session()
    added = service.chat(session.id, "尝试调用")
    assert len(added) == 4 and added[-1].text == "本次无法执行。"
    result = model.calls[1][0].messages[-1]
    assert result == added[2] and result.tool_call_id == "attempt" and result.is_error
    assert expected in result.content
    assert "tool_reviewed" in [name for name, _ in trace.events]
    if kind == "broken":
        assert "tool_error" in [name for name, _ in trace.events]


def test_stops_on_exact_tool_iteration_limit_and_persists_fallback(tmp_path):
    calls = [request(str(i), "calculator", expression="2+2") for i in range(5)]
    service, model, trace = runtime(tmp_path, calls, tools=(CalculatorTool(),), max_iterations=2)
    session = service.new_session()
    generated = service.chat(session.id, "继续计算")
    assert len(model.calls) == 2
    assert [m.tool_call_id for m in generated if isinstance(m, ToolResult)] == ["0", "1"]
    assert "上限" in generated[-1].text
    assert service.get_session(session.id).messages == generated
    assert [name for name, _ in trace.events].count("max_iterations_reached") == 1


class Trace:
    def __init__(self):
        self.events = []

    def log(self, event, payload):
        self.events.append((event, payload))

    def count(self, name):
        return sum(event == name for event, _ in self.events)


class Allow:
    def review(self, session, call):
        return Decision(True)


class LargeResult:
    name = "large"
    description = "Large result fixture"
    parameters = {"type": "object"}

    def __init__(self):
        self.executed = []

    def execute(self, arguments, ctx):
        self.executed.append(ctx.session_id)
        return "x" * 2000


def compaction_runtime(root, responses, *, window, keep=1, tools=()):
    from agent.app.service import AgentService
    from agent.adapters.models.fake_client import FakeModelClient

    model = FakeModelClient(responses)
    trace = Trace()
    store = JsonlSessionStore(root)
    service = AgentService(model, store, Assistant("", tools), Allow(), ContextCompactor("compact"),
                           ModelSettings("main", context_window=window), ModelSettings("flash"),
                           keep_recent_messages=keep, tracer=trace)
    return service, store, model, trace


@pytest.mark.parametrize(("text", "at_threshold"), [("1234", False), ("12345678", True)])
def test_threshold_uses_greater_than_or_equal_and_persists_only_at_boundary(tmp_path, text, at_threshold):
    reply = AssistantMessage("done")
    responses = [CompactionSummary("short"), reply] if at_threshold else [reply]
    service, store, model, trace = compaction_runtime(tmp_path, responses, window=10)
    old = UserMessage("x" * 20)  # 5 tokens + 1 or 2 tokens from the new input
    store.save(Session("s", [old]))
    assert service.chat("s", text) == [UserMessage(text), reply]
    loaded = JsonlSessionStore(tmp_path).load("s")
    assert loaded.messages == [old, UserMessage(text), reply]
    if at_threshold:
        assert loaded.summary == "short" and loaded.compacted_until == 1
        assert [s.model_id for _, s in model.calls] == ["flash", "main"]
        assert model.calls[0][0].messages == (old,)
        assert model.calls[1][0].messages == (UserMessage(text),)
        assert model.calls[1][0].summary == "short"
        assert trace.count("compaction_succeeded") == 1
    else:
        assert loaded.summary is None and loaded.compacted_until == 0
        assert [s.model_id for _, s in model.calls] == ["main"]
        assert trace.count("compaction_triggered") == 0


@pytest.mark.parametrize("keep", [0, 2])
def test_new_large_results_cross_threshold_inside_turn_and_keep_whole_batch(tmp_path, keep):
    tool = LargeResult()
    calls = (ToolCall("a", "large", {}), ToolCall("b", "large", {}))
    batch = AssistantMessage(tool_calls=calls)
    service, store, model, trace = compaction_runtime(tmp_path, [batch, CompactionSummary("summary"),
                                                     AssistantMessage("done")],
                                          window=1000, keep=keep, tools=(tool,))
    store.save(Session("s", [UserMessage("previous")]))
    service.chat("s", "go")
    assert [s.model_id for _, s in model.calls] == ["main", "flash", "main"]
    assert model.calls[1][0].messages == (UserMessage("previous"), UserMessage("go"))
    assert model.calls[2][0].messages == (batch, ToolResult("a", "x" * 2000), ToolResult("b", "x" * 2000))
    assert store.load("s").compacted_until == 2
    assert trace.count("compaction_triggered") == 1


def test_zero_keep_recent_still_sends_live_question_as_user_message(tmp_path):
    question = UserMessage("新问题")
    service, store, model, _ = compaction_runtime(
        tmp_path, [CompactionSummary("旧事实"), AssistantMessage("回答新问题")],
        window=20, keep=0,
    )
    prior = UserMessage("x" * 100)
    store.save(Session("s", [prior]))
    service.chat("s", question.text)
    assert model.calls[0][0].messages == (prior,)
    assert model.calls[1][0].messages == (question,)
    assert store.load("s").compacted_until == 1


def test_compaction_failure_does_not_discard_history_and_retries_on_next_input(tmp_path):
    batch = AssistantMessage(tool_calls=(ToolCall("c", "absent", {}),))
    service, store, model, trace = compaction_runtime(tmp_path, [
        TimeoutError("flash offline"), batch, AssistantMessage("first answer"),
        CompactionSummary("updated"), AssistantMessage("second answer"),
    ], window=20)
    prior = [UserMessage("archived"), UserMessage("x" * 100)]
    store.save(Session("s", prior.copy(), "earlier", 1))
    service.chat("s", "first")
    first = store.load("s")
    assert (first.summary, first.compacted_until) == ("earlier", 1)
    assert first.messages[:2] == prior
    assert trace.count("compaction_failed") == 1 and "flash offline" in str(trace.events)
    assert model.calls[1][0].summary == "earlier" and model.calls[1][0].messages[0] == prior[1]
    service.chat("s", "second")
    second = store.load("s")
    assert second.summary == "updated" and second.compacted_until == len(first.messages)
    assert second.messages[:len(first.messages)] == first.messages
    assert [s.model_id for _, s in model.calls] == ["flash", "main", "main", "flash", "main"]
    assert model.calls[3][0].summary == "earlier" and model.calls[4][0].summary == "updated"


def test_no_compactable_history_skips_once_even_when_context_remains_large(tmp_path):
    service, store, model, trace = compaction_runtime(tmp_path, [
        AssistantMessage(tool_calls=(ToolCall("c", "missing", {}),)), AssistantMessage("done"),
    ], window=10, keep=4)
    store.save(Session("s"))
    service.chat("s", "x" * 2000)
    assert [s.model_id for _, s in model.calls] == ["main", "main"]
    assert trace.count("compaction_skipped") == 1 and trace.count("compaction_triggered") == 0
    assert store.load("s").compacted_until == 0


def test_resumed_batch_records_missing_result_without_repeating_side_effect(tmp_path):
    tool = LargeResult()
    service, store, model, _ = compaction_runtime(tmp_path, [AssistantMessage("resumed")], window=64000, tools=(tool,))
    batch = AssistantMessage(tool_calls=(ToolCall("done", "large", {}), ToolCall("lost", "large", {})))
    previous = [UserMessage("perform actions"), batch, ToolResult("done", "saved")]
    store.save(Session("s", previous.copy()))
    service.chat("s", "continue")
    received = model.calls[0][0].messages
    assert list(received[:3]) == previous
    assert isinstance(received[3], ToolResult) and received[3].tool_call_id == "lost" and received[3].is_error
    assert received[4:] == (UserMessage("continue"),)
    assert store.load("s").messages[:-1] == list(received)
    assert tool.executed == []


def test_creating_duplicate_session_keeps_history_and_main_failure_is_traced(tmp_path):
    service, store, _, trace = compaction_runtime(tmp_path, [TimeoutError("main offline")], window=64000)
    original = Session("s", [UserMessage("previous")])
    store.save(original)
    with pytest.raises(ValueError):
        service.new_session("s")
    assert store.load("s") == original
    with pytest.raises(TimeoutError, match="main offline"):
        service.chat("s", "new turn")
    assert store.load("s").messages == [*original.messages, UserMessage("new turn")]
    assert trace.count("model_error") == 1


def test_reviewer_exception_denies_call_and_returns_error_to_model(tmp_path):
    tool = LargeResult()
    call = ToolCall("r", tool.name, {})
    service, store, model, trace = compaction_runtime(tmp_path, [AssistantMessage(tool_calls=(call,)), AssistantMessage("done")],
                                          window=64000, tools=(tool,))

    class BrokenReviewer:
        def review(self, session, candidate):
            raise RuntimeError("review offline")

    service.reviewer = BrokenReviewer()
    store.save(Session("s"))
    service.chat("s", "question")
    result = model.calls[-1][0].messages[-1]
    assert isinstance(result, ToolResult) and result.is_error and "review offline" in result.content
    assert tool.executed == [] and trace.count("tool_reviewed") == 1
