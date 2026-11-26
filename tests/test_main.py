import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent.domain.roles import Decision
from agent.domain.session import AssistantMessage, ToolCall, ToolResult


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("outcome", ["approve", "deny", "timeout"])
def test_composite_reviews_exact_bash_pipeline_before_execution(tmp_path, monkeypatch, outcome):
    from agent.adapters.models.openai_client import OpenAIClient
    from agent.adapters.tools.bash import BashTool
    from agent.main import build_service

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline")
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONTEXT_WINDOW", "64000")
    monkeypatch.setenv("REVIEWER_MODE", "composite")
    monkeypatch.setenv("FLASH_MODEL", "offline-reviewer")
    command = """printf '%s\\n' 'deep sky' | tr 'a-z' 'A-Z' | tee approved.txt"""
    events = []

    def scripted(self, context, model_settings):
        if context.output_type is Decision:
            call = json.loads(context.messages[-1].text)["proposed_call"]
            events.append(("review", model_settings.model_id, call))
            if outcome == "timeout":
                raise TimeoutError()
            return Decision(True) if outcome == "approve" else Decision(False, "未授权")
        if isinstance(context.messages[-1], ToolResult):
            return AssistantMessage(context.messages[-1].content)
        return AssistantMessage(tool_calls=(ToolCall("pipeline", "bash", {"command": command}),))

    execute = BashTool.execute

    def record_execution(self, arguments, ctx):
        events.append(("execute", ctx.session_id, arguments))
        return execute(self, arguments, ctx)

    monkeypatch.setattr(OpenAIClient, "generate", scripted)
    monkeypatch.setattr(BashTool, "execute", record_execution)
    service = build_service()
    session = service.new_session()
    messages = service.chat(session.id, "请把 deep sky 转成大写并保存到 approved.txt")
    output_file = tmp_path / "data" / "bash" / f"{session.id}.work" / "approved.txt"

    [result] = [message for message in messages if isinstance(message, ToolResult)]
    expected = [("review", "offline-reviewer", {
        "id": "pipeline", "tool_name": "bash", "arguments": {"command": command},
    })]
    if outcome == "approve":
        expected.append(("execute", session.id, {"command": command}))
        assert not result.is_error, result.content
        assert json.loads(result.content) == {"stdout": "DEEP SKY\n", "stderr": "", "exit_code": 0}
        assert output_file.read_text() == "DEEP SKY\n"
    else:
        assert not output_file.exists()
        assert result.tool_call_id == "pipeline" and result.is_error
        assert ("TimeoutError" if outcome == "timeout" else "未授权") in result.content
    assert events == expected


@pytest.mark.parametrize(("mode", "models"), [
    ("rule", ["local-main", "local-main"]),
    ("llm", ["local-main", "local-flash", "local-flash", "local-main"]),
    ("composite", ["local-main", "local-flash", "local-flash", "local-main"]),
])
def test_wired_runtime_routes_registered_tools_and_models(tmp_path, monkeypatch, mode, models):
    from agent.main import build_service
    from agent.adapters.models.openai_client import OpenAIClient

    monkeypatch.chdir(tmp_path)
    for key in ("OPENAI_API_KEY", "ASSISTANT_MODEL", "ASSISTANT_EFFORT", "FLASH_MODEL", "REVIEWER_MODE",
                "CONTEXT_WINDOW", "OPENAI_BASE_URL", "AGENT_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=offline\nASSISTANT_MODEL=local-main\nASSISTANT_EFFORT=xhigh\n"
                                   f"FLASH_MODEL=local-flash\nREVIEWER_MODE={mode}\nCONTEXT_WINDOW=4096\n")
    calls = []
    def scripted(self, context, model_settings):
        calls.append(model_settings.model_id)
        if context.output_type is Decision:
            return Decision(True)
        registered = {tool.name: tool for tool in context.tools}
        assert set(registered) == {"todo", "weather", "calculator", "bash", "search"}
        assert registered["search"].parameters["required"] == ["query"]
        if isinstance(context.messages[-1], ToolResult):
            results = [msg for msg in context.messages if isinstance(msg, ToolResult)]
            assert all(not msg.is_error for msg in results)
            return AssistantMessage("\n".join(msg.content for msg in results))
        return AssistantMessage(tool_calls=(
            ToolCall("calc", "calculator", {"expression": "3*5"}),
            ToolCall("search", "search", {"query": "如何写周报"}),
        ))
    monkeypatch.setattr(OpenAIClient, "generate", scripted)
    service = build_service()
    from agent.app.ports import Effort
    assert service.assistant_model_settings.context_window == 4096
    assert service.get_effort() == Effort.XHIGH
    assert service.compactor_model_settings.effort == Effort.MEDIUM
    session = service.new_session()
    answer = service.chat(session.id, "三乘五，另外搜一下如何写周报")[-1].text
    assert answer.startswith("15\n") and "本周进展" in answer
    assert calls == models and (tmp_path / "data" / "sessions" / (session.id + ".jsonl")).is_file()


def test_python_module_entry_point_starts_and_handles_commands_without_network(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "OPENAI_API_KEY": "offline",
           "AGENT_DATA_DIR": str(tmp_path / "state"), "REVIEWER_MODE": "rule"}
    proc = subprocess.run([sys.executable, "-m", "agent.main"], cwd=tmp_path, env=env,
                          input="/new\n/list\n/history\n/quit\n", text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    paths = list((tmp_path / "state" / "sessions").glob("*.jsonl"))
    assert len(paths) == 2
    assert all(path.stem in proc.stdout for path in paths)


@pytest.mark.parametrize(("env", "expected"), [
    ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
    ({"REVIEWER_MODE": "typo"}, "REVIEWER_MODE"),
    ({"CONTEXT_WINDOW": "zero"}, "CONTEXT_WINDOW"),
    ({"ASSISTANT_EFFORT": "invalid"}, "ASSISTANT_EFFORT"),
])
def test_bad_configuration_reports_field_without_traceback(tmp_path, env, expected):
    proc = subprocess.run([sys.executable, "-m", "agent.main"], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "OPENAI_API_KEY": "offline", **env},
                          input="/quit\n", text=True, capture_output=True, timeout=10)
    assert proc.returncode != 0 and expected in proc.stderr and "Traceback" not in proc.stderr
