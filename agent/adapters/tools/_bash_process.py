import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


_MAX_OUTPUT_BYTES = 1024 * 1024


def _kill_group(group_id: int) -> None:
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_process(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float = 5
) -> tuple[str, str, int]:
    """Run a sandbox launcher and collect at most one MiB across both streams."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
    ) as process:
        stdout = bytearray()
        stderr = bytearray()
        group_stopped = False
        try:
            with selectors.DefaultSelector() as streams:
                streams.register(process.stdout, selectors.EVENT_READ, stdout)
                streams.register(process.stderr, selectors.EVENT_READ, stderr)
                while streams.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"命令执行超过 {timeout:g} 秒")
                    # A completed foreground command can leave descendants holding
                    # open its pipe ends. Stop its group before draining the tail.
                    if not group_stopped and process.poll() is not None:
                        _kill_group(process.pid)
                        group_stopped = True
                    for key, _ in streams.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            streams.unregister(key.fd)
                            continue
                        if len(stdout) + len(stderr) + len(chunk) > _MAX_OUTPUT_BYTES:
                            raise ValueError("命令总输出超过 1 MiB")
                        key.data.extend(chunk)
            try:
                status = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"命令执行超过 {timeout:g} 秒") from exc
        finally:
            if not group_stopped:
                _kill_group(process.pid)
        return stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), status
