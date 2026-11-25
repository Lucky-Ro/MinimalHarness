import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.adapters.files import atomic_write, session_lock, session_path
from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.tool import ToolContext


class TodoTool:
    name = "todo"
    description = "管理当前会话的待办。add 需要 title，complete 和 delete 使用 list 显示的 todo_id。"
    parameters = BUILTIN_SCHEMAS[name]

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "todos"

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        action = arguments.get("action")
        with session_lock(self.root, ctx.session_id):
            path = session_path(self.root, ctx.session_id, ".json")
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if action == "add":
                title = arguments.get("title")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("请提供非空的待办标题")
                items.append({"id": uuid4().hex, "title": title.strip(), "done": False})
            elif action in ("complete", "delete"):
                item = next((item for item in items if item["id"] == arguments.get("todo_id")), None)
                if item is None:
                    raise ValueError("当前会话中找不到该 todo_id")
                if action == "complete":
                    item["done"] = True
                else:
                    items.remove(item)
            elif action != "list":
                raise ValueError("未知的待办操作")
            if action != "list":
                atomic_write(path, json.dumps(items, ensure_ascii=False) + "\n")
        return "\n".join(
            f"{item['id']} [{'x' if item['done'] else ' '}] {item['title']}"
            for item in items
        ) or "当前会话没有待办。"
