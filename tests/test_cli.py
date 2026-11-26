from io import StringIO
import sys

import pytest

from agent.adapters.models.fake_client import FakeModelClient
from agent.adapters.reviewers.rule_reviewer import RuleBasedReviewer
from agent.adapters.stores.jsonl import JsonlSessionStore
from agent.adapters.tools.calculator import CalculatorTool
from agent.app.ports import ModelSettings
from agent.app.service import AgentService
from agent.domain.roles import Assistant, ContextCompactor
from agent.domain.session import AssistantMessage, Session, ToolCall, UserMessage


def service_with_replies(tmp_path, replies=()):
    tools = (CalculatorTool(),)
    return AgentService(
        FakeModelClient(replies), JsonlSessionStore(tmp_path), Assistant("回答", tools),
        RuleBasedReviewer(tools), ContextCompactor("总结"), ModelSettings("main"), ModelSettings("flash"),
    )


def run_cli(service, commands, monkeypatch, capsys):
    from agent.adapters.cli.app import CliApp

    monkeypatch.setattr(sys, "stdin", StringIO(commands))
    CliApp(service).run()
    return capsys.readouterr().out


@pytest.mark.parametrize("ending", ["/quit\n", "/exit\n", ""])
def test_clean_exit_creates_a_fresh_window_and_shows_active_id(tmp_path, monkeypatch, capsys, ending):
    service = service_with_replies(tmp_path)
    service.new_session("old")
    output = run_cli(service, ending, monkeypatch, capsys)
    [fresh] = [s for s in service.list_sessions() if s.id != "old"]
    assert fresh.id in output
    assert fresh.messages == []


def test_new_list_resume_and_full_history(tmp_path, monkeypatch, capsys):
    service = service_with_replies(tmp_path, [AssistantMessage("周报已记录"), AssistantMessage("带伞")])
    original = Session("weather", [UserMessage("旧问题"), AssistantMessage("昨日有雨")], "上海小雨", 2)
    service.session_store.save(original)
    output = run_cli(service, "记周报\n/new\n/list\n/use weather\n/history\n今天呢\n/quit\n",
                     monkeypatch, capsys)
    sessions = service.list_sessions()
    assert len(sessions) == 3
    assert len([s for s in sessions if not s.messages]) == 1
    assert service.get_session("weather").messages == original.messages + [UserMessage("今天呢"), AssistantMessage("带伞")]
    assert all(text in output for text in ("旧问题", "昨日有雨", "上海小雨", "周报已记录", "带伞"))
    assert any("weather" in line and "2 条" in line and "有摘要" in line for line in output.splitlines())
    assert any("0 条" in line and "无摘要" in line for line in output.splitlines())


def test_cli_displays_batch_calls_reviews_errors_and_answer(tmp_path, monkeypatch, capsys):
    response = AssistantMessage(thinking="先计算", tool_calls=(
        ToolCall("ok", "calculator", {"expression": "3*5"}),
        ToolCall("denied", "missing", {}),
        ToolCall("error", "calculator", {"expression": "1/0"}),
    ))
    service = service_with_replies(tmp_path, [response, AssistantMessage("计算完成")])
    output = run_cli(service, "计算\n/quit\n", monkeypatch, capsys)
    assert all(text in output for text in ("先计算", "calculator", "3*5", "计算完成"))
    lines = output.splitlines()
    assert any("ok" in line and "通过" in line and "15" in line for line in lines)
    assert any("denied" in line and "拒绝" in line for line in lines)
    assert any("error" in line and "执行失败" in line for line in lines)


def test_cli_stays_in_session_after_errors_and_masks_model_error_detail(tmp_path, monkeypatch, capsys):
    service = service_with_replies(tmp_path, [RuntimeError("sample-private-provider-token"), AssistantMessage("恢复成功")])
    output = run_cli(service, "/use missing\n/use\n/invalid\n\n第一次\n第二次\n/quit\n", monkeypatch, capsys)
    assert all(text in output for text in ("找不到", "/use", "未知命令", "失败", "恢复成功"))
    assert "sample-private-provider-token" not in output
    [session] = service.list_sessions()
    assert session.messages == [UserMessage("第一次"), UserMessage("第二次"), AssistantMessage("恢复成功")]


def test_cli_effort_command_views_switches_globally_and_handles_invalid_input(tmp_path, monkeypatch, capsys):
    from agent.app.ports import Effort

    service = service_with_replies(tmp_path, [
        lambda ctx, settings: AssistantMessage(f"档位={settings.effort.value}"),
        lambda ctx, settings: AssistantMessage(f"新会话档位={settings.effort.value}"),
    ])
    output = run_cli(
        service,
        "/effort\n/effort bad\n/effort a b\n/effort xhigh\n测试主会话\n/new\n/effort OFF\n测试新会话\n/quit\n",
        monkeypatch,
        capsys,
    )
    assert "/effort" in output
    assert all(level in output for level in ("off", "low", "medium", "high", "xhigh", "max"))
    assert "用法" in output
    assert "档位=xhigh" in output
    assert "新会话档位=off" in output
    assert service.get_effort() == Effort.OFF
    for vendor_term in ("reasoning_effort", "reasoning_content", "extra_body", "thinking_level"):
        assert vendor_term not in output
