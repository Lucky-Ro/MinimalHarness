import json
from datetime import datetime, timezone
from typing import Any, TextIO


class JsonTraceLogger:
    def __init__(self, stream: TextIO):
        self.stream = stream

    def log(self, event: str, payload: dict[str, Any]) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(), "event": event, "payload": payload}
        print(json.dumps(record, ensure_ascii=False), file=self.stream, flush=True)


class MemoryTraceLogger:
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, dict(payload)))
