import json
import os
from dataclasses import asdict
from pathlib import Path

from agent.adapters.files import atomic_write, session_lock, session_path
from agent.app.ports import SessionNotFoundError
from agent.domain.session import AssistantMessage, Message, Session, ToolCall, ToolResult, UserMessage


_KINDS = {UserMessage: "user", AssistantMessage: "assistant", ToolResult: "tool_result"}


def _decode(record: dict) -> Message:
    kind = record.pop("type")
    if kind == "user":
        return UserMessage(**record)
    if kind == "tool_result":
        return ToolResult(**record)
    if kind == "assistant":
        calls = tuple(ToolCall(**call) for call in record.pop("tool_calls", ()))
        return AssistantMessage(tool_calls=calls, **record)
    raise ValueError(f"Unknown message kind {kind!r}")


class JsonlSessionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def lock(self, session_id: str):
        return session_lock(self.root, session_id)

    def save(self, session: Session) -> None:
        path = session_path(self.root, session.id, ".jsonl")
        meta = {"type": "meta", "id": session.id, "summary": session.summary,
                "compacted_until": session.compacted_until}
        lines = [json.dumps(meta, ensure_ascii=False)]
        for message in session.messages:
            record = {"type": _KINDS[type(message)], **asdict(message)}
            lines.append(json.dumps(record, ensure_ascii=False))
        atomic_write(path, "\n".join(lines) + "\n")

    def load(self, session_id: str) -> Session:
        path = session_path(self.root, session_id, ".jsonl")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise SessionNotFoundError(f"Session {session_id!r} not found") from exc
        with os.fdopen(fd, encoding="utf-8") as source:
            meta = json.loads(source.readline())
            if not isinstance(meta, dict) or meta.get("type") != "meta" or meta.get("id") != session_id:
                raise ValueError(f"Invalid metadata in {path.name}")
            messages = []
            for number, line in enumerate(source, start=2):
                try:
                    messages.append(_decode(json.loads(line)))
                except (TypeError, KeyError, ValueError, AttributeError) as exc:
                    raise ValueError(f"Invalid message in {path.name} at line {number}") from exc
        offset = meta.get("compacted_until", 0)
        if type(offset) is not int or not 0 <= offset <= len(messages):
            raise ValueError(f"Invalid compaction offset in {path.name}")
        return Session(id=session_id, messages=messages, summary=meta.get("summary"), compacted_until=offset)

    def list(self) -> list[Session]:
        return [self.load(path.stem) for path in sorted(self.root.glob("*.jsonl"))
                if not path.is_symlink() and path.is_file()]
