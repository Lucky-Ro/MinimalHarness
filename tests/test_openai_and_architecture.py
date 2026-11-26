import ast
import json
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest
from openai import OpenAI, __version__ as sdk_version

if int(sdk_version.split(".")[0]) >= 3:
    import httpx2 as httpx
else:
    import httpx

from agent.adapters.tools.weather import WeatherTool
from agent.app.ports import Effort, ModelSettings
from agent.domain.context import Context
from agent.domain.roles import CompactionSummary, Decision, ToolCallReviewer
from agent.domain.session import AssistantMessage, Session, ToolCall, ToolResult, UserMessage


def sdk_fixture(message, *, base_url="https://example.test/v1", status=200):
    from agent.adapters.models.openai_client import OpenAIClient

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        body = {"choices": [{"index": 0, "message": {"role": "assistant", **message},
                             "finish_reason": "stop"}],
                "id": "test", "model": "test", "created": 1, "object": "chat.completion"}
        return httpx.Response(status, json=body if status == 200 else message)

    sdk = OpenAI(api_key="fake-test-key", base_url=base_url, max_retries=0,
                 http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    return OpenAIClient("fake-test-key", base_url, client=sdk), requests


def wire_call(key, name, arguments):
    return {"id": key, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_native_batch_mapping_and_reasoning_replay_with_tools():
    client, requests = sdk_fixture({"content": None, "reasoning_content": "current",
                                    "tool_calls": [wire_call("new1", "weather", '{"city":"杭州"}'),
                                                   wire_call("new2", "todo", '{"action":"list"}')]})
    weather = WeatherTool()
    prior = ToolCall("old", "weather", {"city": "北京"})
    ctx = Context("static", AssistantMessage, tools=(weather,), summary="记录 & </conversation_summary>",
                  messages=(UserMessage("天气"), AssistantMessage("", (prior,), "private prior thought"),
                            ToolResult("old", "晴"), AssistantMessage("好的"), UserMessage("继续")))
    result = client.generate(ctx, ModelSettings("chat"))
    assert result == AssistantMessage("", (ToolCall("new1", "weather", {"city": "杭州"}),
                                           ToolCall("new2", "todo", {"action": "list"})), "current")
    [payload] = requests
    head, summary, user, assistant, tool, plain_assistant, followup = payload["messages"]
    assert head == {"role": "system", "content": "static"}
    assert "<conversation_summary>记录 &amp; &lt;/conversation_summary&gt;</conversation_summary>" in summary["content"]
    assert summary["role"] == "user"
    assert user == {"role": "user", "content": "天气"} and followup["content"] == "继续"
    assert assistant["role"] == "assistant" and assistant["tool_calls"][0]["id"] == "old"
    assert assistant["reasoning_content"] == "private prior thought"
    assert "reasoning_content" not in plain_assistant
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"}
    assert tool == {"role": "tool", "tool_call_id": "old", "content": "晴"}
    assert payload["tools"] == [{"type": "function", "function": {
        "name": weather.name, "description": weather.description, "parameters": weather.parameters}}]


@pytest.mark.parametrize(("tools", "effort", "expect_reasoning"), [
    ((WeatherTool(),), Effort.LOW, True),
    ((WeatherTool(),), Effort.MEDIUM, True),
    ((WeatherTool(),), Effort.HIGH, True),
    ((WeatherTool(),), Effort.XHIGH, True),
    ((WeatherTool(),), Effort.MAX, True),
    ((WeatherTool(),), Effort.OFF, False),
    ((), Effort.MEDIUM, False),
    ((), Effort.MAX, False),
    ((), Effort.OFF, False),
])
def test_historical_reasoning_replayed_only_when_tools_and_thinking_enabled(tools, effort, expect_reasoning):
    from agent.domain.roles import Assistant

    client, requests = sdk_fixture({"content": "ok"}, base_url="https://api.deepseek.com")
    old = AssistantMessage("旧结论", thinking="已压缩旧思考")
    active = AssistantMessage("先查天气", (ToolCall("c1", "weather", {"city": "上海"}),), "活跃思考")
    session = Session(
        messages=[old, UserMessage("天气"), active, ToolResult("c1", "晴")],
        summary="旧对话摘要",
        compacted_until=1,
    )
    ctx = Assistant("system", tools).context(session)
    assert client.generate(ctx, ModelSettings("deepseek-v4-pro", effort)) == AssistantMessage("ok")
    [payload] = requests
    assert "已压缩旧思考" not in json.dumps(payload, ensure_ascii=False)
    assistant_msgs = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    if expect_reasoning:
        assert assistant_msgs[0]["reasoning_content"] == "活跃思考"
    else:
        assert "reasoning_content" not in assistant_msgs[0]


@pytest.mark.parametrize(("base", "model", "effort", "tools", "expected"), [
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.OFF, (WeatherTool(),), {"thinking": {"type": "disabled"}}),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.OFF, (), {"thinking": {"type": "disabled"}}),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.LOW, (WeatherTool(),), {"reasoning_effort": "low"}),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.MEDIUM, (WeatherTool(),), {"reasoning_effort": "high"}),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.HIGH, (WeatherTool(),), {"reasoning_effort": "high"}),
    ("https://api.deepseek.com/v1", "deepseek-v4-pro", Effort.XHIGH, (WeatherTool(),), {"reasoning_effort": "high"}),
    ("https://api.deepseek.com", "deepseek-flash", Effort.MAX, (), {"reasoning_effort": "max"}),
    ("https://api.deepseek.com", "deepseek-v4-pro", Effort.MAX, (WeatherTool(),), {"reasoning_effort": "max"}),
    ("https://api.deepseek.com", "deepseek-custom", Effort.HIGH, (WeatherTool(),), {}),
    ("https://api.deepseek.com.example.test", "deepseek-v4-pro", Effort.HIGH, (WeatherTool(),), {}),
    ("https://api.openai.com/v1", "o3", Effort.OFF, (), {}),
    ("https://api.openai.com/v1", "o3", Effort.LOW, (), {"reasoning_effort": "low"}),
    ("https://api.openai.com/v1", "o3-mini", Effort.MEDIUM, (WeatherTool(),), {"reasoning_effort": "medium"}),
    ("https://api.openai.com/v1", "o4-mini", Effort.HIGH, (), {"reasoning_effort": "high"}),
    ("https://api.openai.com/v1", "o3", Effort.XHIGH, (), {}),
    ("https://api.openai.com/v1", "o3", Effort.MAX, (), {}),
    ("https://api.openai.com/v1", "gpt-4o", Effort.HIGH, (), {}),
    ("https://example.test", "o3", Effort.HIGH, (), {}),
])
def test_provider_specific_controls_only_on_compatible_endpoint(base, model, effort, tools, expected):
    client, requests = sdk_fixture({"content": "ok"}, base_url=base)
    assert client.generate(Context("system", AssistantMessage, tools=tools), ModelSettings(model, effort)) == AssistantMessage("ok")
    actual = {key: requests[0][key] for key in ("thinking", "reasoning_effort") if key in requests[0]}
    assert actual == expected


@pytest.mark.parametrize(("calls", "field"), [
    ([wire_call("", "todo", "{}")], "id"), ([wire_call("a", "  ", "{}")], "name"),
    ([wire_call("a", "todo", "{")], "arguments"), ([wire_call("a", "todo", "[]")], "arguments"),
    ([wire_call("a", "todo", "null")], "arguments"),
    ([wire_call("a", "todo", "{}"), wire_call("a", "weather", "{}")], "id"),
])
def test_malformed_tool_calls_raise_clear_parse_errors(calls, field):
    client, _ = sdk_fixture({"content": None, "tool_calls": calls})
    with pytest.raises(ValueError, match=field):
        client.generate(Context("s", AssistantMessage), ModelSettings("m"))


def test_review_of_pending_batch_has_only_reference_data_and_requires_json():
    proposed = ToolCall("pending", "weather", {"city": "北京"})
    session = Session(messages=[UserMessage("check"), AssistantMessage(tool_calls=(proposed,), thinking="secret")])
    client, requests = sdk_fixture({"content": '{"should_allow":false,"reason":"not needed"}'})
    result = client.generate(ToolCallReviewer("review").context(session, proposed), ModelSettings("flash"))
    assert result == Decision(False, "not needed")
    [payload] = requests
    assert not payload.get("tools")
    assert payload["response_format"] == {"type": "json_object"} and "json" in str(payload["messages"]).lower()
    assert all(message["role"] in ("system", "user") and "tool_calls" not in message for message in payload["messages"])
    assert "北京" in str(payload["messages"]) and "secret" not in str(payload["messages"])


@pytest.mark.parametrize("value", ["garbled", "[]", '{"should_allow":"false"}',
                                    '{"should_allow":false}', '{"should_allow":false,"reason":" "}',
                                    '{"should_allow":true,"reason":12}'])
def test_broken_decision_never_turns_into_an_approval(value):
    client, _ = sdk_fixture({"content": value})
    with pytest.raises(ValueError):
        client.generate(Context("review", Decision), ModelSettings("flash"))


@pytest.mark.parametrize(("value", "expected"), [
    (" 原始摘要 ", "原始摘要"), ('{"summary":"结构化摘要"}', "结构化摘要"),
    (" ", None), ('{"summary":""}', None), ("{}", None), ('{"summary": 2}', None), ('["note"]', None),
    pytest.param("[目标] 完成周报", "[目标] 完成周报", id="bracketed-heading"),
    pytest.param("[计划](notes.md) 已确定", "[计划](notes.md) 已确定", id="bracketed-link"),
])
def test_compaction_accepts_only_nonempty_summary_text(value, expected):
    client, _ = sdk_fixture({"content": value})
    ctx = Context("compact", CompactionSummary, messages=(UserMessage("facts"),))
    if expected is None:
        with pytest.raises(ValueError):
            client.generate(ctx, ModelSettings("flash"))
    else:
        assert client.generate(ctx, ModelSettings("flash")) == CompactionSummary(expected)


def test_compaction_sends_tool_history_as_reference_with_result_status():
    client, requests = sdk_fixture({"content": "第一次被拒，第二次成功"}, base_url="https://api.deepseek.com")
    batch = AssistantMessage(tool_calls=(
        ToolCall("denied", "todo", {"action": "delete"}),
        ToolCall("listed", "todo", {"action": "list"}),
    ), thinking="private")
    context = Context("概括历史", CompactionSummary, summary="旧摘要", messages=(
        UserMessage("处理待办"), batch, ToolResult("denied", "缺少 ID", is_error=True),
        ToolResult("listed", "当前没有待办"),
    ))
    assert client.generate(context, ModelSettings("deepseek-flash")) == CompactionSummary("第一次被拒，第二次成功")
    [request] = requests
    assert "tools" not in request
    head, summary, transcript = request["messages"]
    assert head == {"role": "system", "content": "概括历史"}
    assert "<conversation_summary>旧摘要</conversation_summary>" in summary["content"]
    assert transcript["role"] == "user"
    evidence = json.loads(transcript["content"])
    history = evidence["history"]
    assert history[0]["text"] == "处理待办"
    assert history[1]["tool_calls"][0]["arguments"] == {"action": "delete"}
    assert [(record["tool_call_id"], record["is_error"]) for record in history[2:]] == [
        ("denied", True), ("listed", False),
    ]
    assert "private" not in transcript["content"]


def test_api_exception_has_status_but_leaks_neither_key_nor_error_body():
    body = {"error": {"message": "credential fake-test-key; private prompt contents"}}
    client, _ = sdk_fixture(body, status=401)
    with pytest.raises(RuntimeError) as caught:
        client.generate(Context("s", AssistantMessage), ModelSettings("m"))
    detail = str(caught.value)
    assert "401" in detail and "AuthenticationError" in detail
    assert "fake-test-key" not in detail and "private prompt" not in detail


ROOT = Path(__file__).resolve().parents[1]


def imports_in(source, package):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            for alias in node.names:
                yield f"{base}.{alias.name}"


@pytest.mark.parametrize("layer", ["domain", "app"])
def test_all_inner_layer_imports_point_inward(layer):
    permitted = {"domain"} if layer == "domain" else {"domain", "app"}
    for path in (ROOT / "agent" / layer).rglob("*.py"):
        package = ".".join(path.relative_to(ROOT).parent.parts)
        for target in imports_in(path.read_text(), package):
            parts = target.split(".")
            assert parts[0] in sys.stdlib_module_names or (
                parts[0] == "agent" and len(parts) > 1 and parts[1] in permitted
            ), f"{path}: {target}"


def test_import_guard_recognizes_relative_and_from_imports():
    sample = "from .. import adapters\nfrom agent import adapters\nfrom .ports import ModelClient\nimport openai"
    assert list(imports_in(sample, "agent.app")) == [
        "agent.adapters", "agent.adapters", "agent.app.ports.ModelClient", "openai",
    ]


def test_app_and_cli_contain_no_vendor_protocol_field_names():
    forbidden = ("reasoning_effort", "reasoning_content", "extra_body", "thinking_level")
    for folder in (ROOT / "agent" / "app", ROOT / "agent" / "adapters" / "cli"):
        for path in folder.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for term in forbidden:
                assert term not in source, f"{path}: leaked vendor parameter {term}"
