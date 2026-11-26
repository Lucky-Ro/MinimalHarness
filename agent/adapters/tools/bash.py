"""Run the reviewed source in native Bash with a workspace for each session."""

import json
import sys
from pathlib import Path
from typing import Any

from agent.adapters.command_policy import validate_command
from agent.adapters.files import session_path
from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.adapters.tools._bash_process import run_process
from agent.domain.tool import ToolContext


_MAC_PROFILE = """
(version 1)
(deny default)
(import "dyld-support.sb")
(allow process-fork sysctl-read file-read-metadata)
(allow process-exec (subpath "/bin") (subpath "/usr/bin"))
(allow file-read* file-map-executable
    (subpath "/bin") (subpath "/usr/bin") (subpath "/usr/lib")
    (subpath "/System/Library") (subpath "/usr/share/locale"))
(allow file-read* file-write* (literal "/dev/null") (subpath (param "WORK")))
"""

# Set hard and soft limits in the child before handing control to the reviewed
# source. Keeping the program in its own argument preserves its original bytes.
_LIMITS = 'umask 077; ulimit -c 0 && ulimit -f 8192 && ulimit -t 5 && ulimit -n 128 || exit 125; exec "$@"'


def _sandbox_argv(platform: str, work: Path, command: str) -> list[str]:
    bash = ["/bin/bash", "--noprofile", "--norc", "-c"]
    script = [*bash, _LIMITS, "bash-limits", *bash, command]
    if platform == "darwin":
        return ["/usr/bin/sandbox-exec", "-D", f"WORK={work}", "-p", _MAC_PROFILE, *script]
    if platform == "linux":
        argv = ["/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns",
                "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
        for path in ("/bin", "/usr/bin", "/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/share/locale"):
            argv.extend(["--ro-bind-try", path, path])
        return [*argv, "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(work), "/work",
                "--chdir", "/work", "--", *script]
    raise RuntimeError("Bash 沙箱需要 macOS 或安装了 bubblewrap 的 Linux")


class BashTool:
    name = "bash"
    description = ("在当前会话独立的本地目录中运行 Bash 脚本，支持管道、重定向、变量和循环。"
                   "文件可跨调用使用，每次从会话目录启动，无网络，超时 5 秒。"
                   "返回 stdout、stderr、exit_code。")
    parameters = BUILTIN_SCHEMAS[name]

    def __init__(self, data_dir: Path = Path("data")):
        self.root = Path(data_dir) / "bash"

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        command = arguments.get("command")
        validate_command(command)
        work = session_path(self.root, ctx.session_id, ".work")
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Repeat identity validation after mkdir for racing case variants on macOS.
        work = session_path(self.root, ctx.session_id, ".work").resolve(strict=True)
        argv = _sandbox_argv(sys.platform, work, command)
        home = "/work" if sys.platform == "linux" else str(work)
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "TMPDIR": home, "LC_ALL": "C.UTF-8"}
        try:
            stdout, stderr, status = run_process(argv, work, env)
        except FileNotFoundError as exc:
            raise RuntimeError(f"无法启动 Bash 沙箱，缺少 {argv[0]}") from exc
        if status and stderr.startswith(("sandbox-exec:", "bwrap:")):
            raise RuntimeError(f"Bash 沙箱错误：{stderr.strip()}")
        return json.dumps({"stdout": stdout, "stderr": stderr, "exit_code": status}, ensure_ascii=False)
