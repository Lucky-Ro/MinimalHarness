import json
import subprocess
from typing import Any

from agent.adapters.command_policy import safe_command_argv
from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.tool import ToolContext


class BashTool:
    name = "bash"
    description = "执行受限本地命令：pwd、ls，或 echo 加纯文本。"
    parameters = BUILTIN_SCHEMAS[name]

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        argv = safe_command_argv(arguments.get("command"))
        try:
            process = subprocess.run(argv, timeout=5, capture_output=True, text=True, errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("命令执行超过 5 秒") from exc
        return json.dumps({
            "stdout": process.stdout, "stderr": process.stderr, "exit_code": process.returncode,
        }, ensure_ascii=False)
