from typing import Any

from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.tool import ToolContext


class SearchTool:
    name = "search"
    description = "在内置样例资料中模拟搜索，支持周报、会议纪要和会话管理等主题。"
    parameters = BUILTIN_SCHEMAS[name]

    _entries = (
        (("周报", "weekly report"), "周报可按本周进展、当前风险和下周计划组织。"),
        (("会议纪要", "meeting notes"), "会议纪要可记录结论、负责人和行动项截止时间。"),
        (("会话", "session"), "使用 /list 查看会话，使用 /use <id> 恢复对应会话。"),
    )

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("query 必须是 1 至 200 字符的关键词")
        normalized = query.strip().casefold()
        matches = [text for keywords, text in self._entries if any(key in normalized for key in keywords)]
        return "模拟搜索结果\n" + "\n".join(matches) if matches else "模拟搜索未找到匹配的内置资料。"
