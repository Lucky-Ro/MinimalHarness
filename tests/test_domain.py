from dataclasses import replace

import pytest


from agent.domain.context import estimate_context_tokens, estimate_tokens, split_for_compaction
from agent.domain.roles import Assistant, CompactionSummary, ContextCompactor, Decision, ToolCallReviewer
from agent.domain.session import AssistantMessage, Session, ToolCall, ToolResult, UserMessage


def test_roles_preserve_history_without_replaying_thinking():
    call = ToolCall("c1", "weather", {"city": "北京"})
    old, current = UserMessage("昨天"), AssistantMessage("晴", thinking="hidden")
    session = Session(summary="北京", compacted_until=1)
    assert session.id != Session().id
    session.append(old)
    session.append(current)
    assistant = Assistant("回答问题", ()).context(session)
    assert assistant.summary == "北京" and assistant.memory == ()
    assert assistant.messages == (AssistantMessage("晴"),)
    assert assistant.output_type is AssistantMessage
    review = ToolCallReviewer("审核").context(session, call)
    assert review.tools == () and review.output_type is Decision
    assert "weather" in str(review.messages) and "北京" in str(review.messages)
    compact = ContextCompactor("压缩").context("旧摘要", [current])
    assert compact.summary == "旧摘要" and compact.tools == ()
    assert compact.output_type is CompactionSummary
    assert compact.messages == (AssistantMessage("晴"),)
    assert session.active_messages() == [current] and session.messages == [old, current]


def test_estimation_counts_all_visible_inputs():
    from types import SimpleNamespace
    ctx = Assistant("任务", ()).context(Session())
    count = estimate_context_tokens
    base = count(ctx)
    assert base > 0 and count(ctx) == base and estimate_tokens("") == 0
    assert estimate_tokens("汉" * 20) > estimate_tokens("a" * 20)
    msg = AssistantMessage("answer")
    assert count(replace(ctx, messages=(msg,))) == count(replace(ctx, messages=(replace(msg, thinking="x"*100),)))
    tool = SimpleNamespace(name="test", description="listed", parameters={"type": "object"})
    for additions in (dict(summary="北京"), dict(memory=("记忆",)), dict(tools=(tool,)),
                      dict(messages=(ToolResult("id", "result"),)), dict(messages=(UserMessage("ask"),)),
                      dict(messages=(AssistantMessage(tool_calls=(ToolCall("id", "tool", {"arg": 3}),)),))):
        assert count(replace(ctx, **additions)) > base


@pytest.mark.parametrize(("keep", "delta"), [(1, 4), (2, 1), (3, 1), (4, 1), (5, 0)])
def test_compaction_preserves_entire_multi_call_batch(keep, delta):
    messages = [UserMessage("ask"), AssistantMessage(tool_calls=(
        ToolCall("a", "weather", {}), ToolCall("b", "todo", {}))),
        ToolResult("a", "a"), ToolResult("b", "b"), AssistantMessage("done")]
    assert split_for_compaction(messages, keep) == (messages[:delta], delta)


def test_plain_history_and_single_large_exchange():
    history = [UserMessage(str(i)) for i in range(9)]
    assert split_for_compaction(history) == (history[:5], 5)
    exchange = [AssistantMessage(tool_calls=(ToolCall("k", "test", {}),)),
                ToolResult("k", "x" * 100_000)]
    assert split_for_compaction(exchange, 1) == ([], 0)
