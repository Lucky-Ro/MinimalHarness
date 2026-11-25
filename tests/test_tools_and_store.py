import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent.app.ports import SessionNotFoundError
from agent.domain.session import AssistantMessage, Session, ToolCall, ToolResult, UserMessage
from agent.domain.tool import ToolContext


CTX = ToolContext("session_1")


def test_mock_search_matches_local_entries_and_reports_no_results():
    from agent.adapters.tools.search import SearchTool

    tool = SearchTool()
    assert "本周进展" in tool.execute({"query": "如何写周报"}, CTX)
    assert "未找到" in tool.execute({"query": "无关词-83934"}, CTX)


@pytest.mark.parametrize("query", ["  ", 42, "a" * 201])
def test_search_rejects_invalid_queries_at_both_boundaries(query):
    from agent.adapters.reviewers.rule_reviewer import RuleBasedReviewer
    from agent.adapters.tools.search import SearchTool

    tool = SearchTool()
    arguments = {"query": query}
    assert not RuleBasedReviewer([tool]).review(Session(), ToolCall("s", "search", arguments)).should_allow
    with pytest.raises(ValueError, match="query"):
        tool.execute(arguments, CTX)


@pytest.mark.parametrize(("expression", "value"), [
    (" 2 + 3 * (8 - 5) ", 11), ("-5 / +2", -2.5),
    ("(0.2 + 0.3) ** 2", 0.25), ("2 ** -3", 0.125),
])
def test_calculator_arithmetic(expression, value):
    from agent.adapters.tools.calculator import CalculatorTool

    assert float(CalculatorTool().execute({"expression": expression}, CTX)) == value


@pytest.mark.parametrize("expression", [
    "1/0", "2+", "x + 3", "[1, 2]", "True + 4", "abs(-4)",
    "1e999", "(-1)**0.5", "2**10000000", "9**(9**9)",
    "((2**100)**100)**100", "1+" * 501 + "1",
])
def test_calculator_rejects_invalid_or_excessive_arithmetic(expression):
    from agent.adapters.tools.calculator import CalculatorTool

    with pytest.raises((ArithmeticError, ValueError)):
        CalculatorTool().execute({"expression": expression}, CTX)


def test_weather_known_city_date_and_unknown_fallback():
    from agent.adapters.tools.weather import WeatherTool

    tool = WeatherTool()
    known = tool.execute({"city": "北京", "date": "明天"}, CTX)
    assert all(detail in known for detail in ("北京", "明天", "°C", "模拟"))
    unknown = tool.execute({"city": "小岛"}, CTX)
    assert "小岛" in unknown and "模拟" in unknown
    assert unknown == tool.execute({"city": "小岛"}, CTX)


def test_bash_real_allowed_command_returns_streams_and_exit_code():
    from agent.adapters.tools.bash import BashTool

    result = BashTool().execute({"command": 'echo "hello world"'}, CTX)
    assert json.loads(result) == {"stdout": "hello world\n", "stderr": "", "exit_code": 0}


def test_bash_enforces_command_policy_even_without_reviewer(monkeypatch):
    from agent.adapters.tools.bash import BashTool

    def unexpected_run(*args, **kwargs):
        pytest.fail("rejected input was passed to subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    with pytest.raises(ValueError):
        BashTool().execute({"command": "pwd; ls"}, CTX)


def test_bash_limits_a_stalled_command(monkeypatch):
    from agent.adapters.tools.bash import BashTool

    def stalled(argv, **options):
        assert argv == ["/bin/pwd"] and options["timeout"] == 5
        assert not options.get("shell")
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    monkeypatch.setattr(subprocess, "run", stalled)
    with pytest.raises(TimeoutError, match="5"):
        BashTool().execute({"command": "pwd"}, CTX)


def test_todos_are_durable_and_isolated_by_trusted_context(tmp_path):
    from agent.adapters.tools.todo import TodoTool

    a, b = ToolContext("session_1"), ToolContext("session_2")
    tool = TodoTool(tmp_path)
    tool.execute({"action": "add", "title": "查天气后带伞", "session_id": b.session_id}, a)
    tool.execute({"action": "add", "title": "写周报"}, b)
    restored = TodoTool(tmp_path)
    one = restored.execute({"action": "list"}, a)
    two = restored.execute({"action": "list"}, b)
    assert "查天气后带伞" in one and "写周报" not in one
    assert "写周报" in two and "带伞" not in two
    assert (tmp_path / "todos" / "session_1.json").is_file()
    todo_id = one.split()[0]
    assert "[x]" in restored.execute({"action": "complete", "todo_id": todo_id}, a)
    assert "带伞" not in restored.execute({"action": "delete", "todo_id": todo_id}, a)
    assert restored.execute({"action": "list"}, b) == two


@pytest.mark.parametrize("arguments", [
    {"action": "complete", "todo_id": "unknown"}, {"action": "delete"},
    {"action": "add", "title": " "}, {"action": "drop_all"},
])
def test_invalid_todo_operations_cannot_modify_list(tmp_path, arguments):
    from agent.adapters.tools.todo import TodoTool

    tool, ctx = TodoTool(tmp_path), ToolContext("abc")
    before = tool.execute({"action": "add", "title": "keep me"}, ctx)
    with pytest.raises(ValueError):
        tool.execute(arguments, ctx)
    assert tool.execute({"action": "list"}, ctx) == before


def test_todo_rejects_unsafe_session_ids(tmp_path):
    from agent.adapters.tools.todo import TodoTool

    with pytest.raises(ValueError):
        TodoTool(tmp_path).execute({"action": "add", "title": "oops"}, ToolContext("../sibling"))
    assert not (tmp_path / "sibling.json").exists()


def test_concurrent_todo_additions_keep_both_updates(tmp_path):
    from agent.adapters.tools.todo import TodoTool

    def add(i):
        TodoTool(tmp_path).execute({"action": "add", "title": f"item-{i}."}, ToolContext("shared"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(12)))
    listed = TodoTool(tmp_path).execute({"action": "list"}, ToolContext("shared"))
    assert all(f"item-{i}." in listed for i in range(12))


def make_store(root):
    from agent.adapters.stores.jsonl import JsonlSessionStore

    return JsonlSessionStore(root)


def test_store_round_trip_preserves_history_and_separate_files(tmp_path):
    store = make_store(tmp_path)
    call = ToolCall("t1", "weather", {"city": "北京"})
    first = Session("window-1", [
        UserMessage("天气\n如何？"), AssistantMessage("查询", (call,), "思考"),
        ToolResult("t1", "未知", is_error=True),
    ], summary="记录", compacted_until=1)
    second = Session("window_2", [UserMessage("周报")])
    store.save(first)
    store.save(second)
    loaded_store = make_store(tmp_path)
    assert loaded_store.load(first.id) == first
    assert loaded_store.load(second.id) == second
    assert {s.id for s in loaded_store.list()} == {first.id, second.id}
    lines = (tmp_path / "window-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["meta", "user", "assistant", "tool_result"]
    assert json.loads(lines[0]) == {"type": "meta", "id": first.id, "summary": "记录", "compacted_until": 1}


def test_store_loads_older_records_with_missing_optional_fields(tmp_path):
    records = [
        {"type": "meta", "id": "old"}, {"type": "user", "text": "ask"},
        {"type": "assistant", "tool_calls": [{"id": "t", "tool_name": "todo", "arguments": {}}]},
        {"type": "tool_result", "tool_call_id": "t", "content": "done"},
        {"type": "assistant", "text": "answer"},
    ]
    (tmp_path / "old.jsonl").write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    expected = Session("old", [UserMessage("ask"), AssistantMessage(tool_calls=(ToolCall("t", "todo", {}),)),
                               ToolResult("t", "done"), AssistantMessage("answer")])
    assert make_store(tmp_path).load("old") == expected


def test_missing_session_has_distinct_error(tmp_path):
    with pytest.raises(SessionNotFoundError):
        make_store(tmp_path).load("missing")


@pytest.mark.parametrize("bad_id", ["", "../escape", ".", "/absolute", "x/y", "x\\y", "line\nbreak"])
def test_invalid_identifiers_are_rejected_on_read_write_and_lock(tmp_path, bad_id):
    store = make_store(tmp_path / "sessions")
    with pytest.raises(ValueError):
        store.load(bad_id)
    with pytest.raises(ValueError):
        store.save(Session(bad_id))
    with pytest.raises(ValueError):
        with store.lock(bad_id):
            pytest.fail("invalid session was locked")


def test_metadata_cannot_change_the_identity_being_loaded(tmp_path):
    (tmp_path / "a.jsonl").write_text('{"type":"meta","id":"b"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        make_store(tmp_path).load("a")


def test_session_file_symlink_cannot_read_or_replace_another_file(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    external = tmp_path / "outside"
    external.write_text('{"type":"meta","id":"alias"}\n', encoding="utf-8")
    alias = root / "alias.jsonl"
    alias.symlink_to(external)
    store = make_store(root)
    with pytest.raises(ValueError):
        store.load("alias")
    with pytest.raises(ValueError):
        store.save(Session("alias"))
    assert alias.is_symlink() and external.read_text(encoding="utf-8") == '{"type":"meta","id":"alias"}\n'


def test_failed_atomic_replace_preserves_previous_session_and_cleans_temporary_file(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    original = Session("data", [UserMessage("previous")])
    store.save(original)

    def fail_replace(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected disk failure"):
        store.save(Session("data", [UserMessage("partial")]))
    assert store.load("data") == original
    assert {f.name for f in tmp_path.iterdir()} == {"data.jsonl"}


def test_session_lock_serializes_independent_store_instances(tmp_path):
    first, second = make_store(tmp_path), make_store(tmp_path)
    waiting, entered = Event(), Event()

    def compete():
        waiting.set()
        with second.lock("shared"):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with first.lock("shared"):
            future = executor.submit(compete)
            assert waiting.wait(2)
            assert not entered.wait(0.05)
            # Other sessions have independent locks.
            with second.lock("other"):
                pass
        future.result(timeout=2)
    assert entered.is_set()


@pytest.mark.parametrize("resource", ["session", "todo"])
def test_case_alias_cannot_read_or_overwrite_another_session(tmp_path, resource):
    from agent.adapters.tools.todo import TodoTool

    probe = tmp_path / "CaseProbe"
    probe.touch()
    insensitive = (tmp_path / "caseprobe").exists()
    if resource == "session":
        store = make_store(tmp_path / "sessions")

        def write(key, text):
            store.save(Session(key, [UserMessage(text)]))

        def read(key):
            return store.load(key).messages[0].text
    else:
        tool = TodoTool(tmp_path)

        def write(key, text):
            tool.execute({"action": "add", "title": text}, ToolContext(key))

        def read(key):
            return tool.execute({"action": "list"}, ToolContext(key))

    write("Team", "original")
    if insensitive:
        with pytest.raises(ValueError):
            read("team")
        with pytest.raises(ValueError):
            write("team", "different")
    else:
        write("team", "different")
        assert "different" in read("team") and "original" not in read("team")
    assert "original" in read("Team") and "different" not in read("Team")


def test_first_save_cannot_replace_a_racing_case_variant(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    original = Session("Team", [UserMessage("original")])
    competing = Session("team", [UserMessage("different")])
    # Another writer wins the creation race after this call has staged its bytes.
    real_fsync = os.fsync

    def publish_competing_write(fd):
        monkeypatch.setattr(os, "fsync", real_fsync)
        store.save(original)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", publish_competing_write)
    (tmp_path / "CaseProbe").touch()
    if (tmp_path / "caseprobe").exists():
        with pytest.raises(ValueError):
            store.save(competing)
    else:
        store.save(competing)
        assert store.load("team") == competing
    assert store.load("Team") == original
