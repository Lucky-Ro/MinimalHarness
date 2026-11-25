import fcntl
from pathlib import Path
import sys
import time

import pytest


def run_python(tmp_path, script, *args, timeout=5):
    from agent.adapters.tools._bash_process import run_process

    return run_process(
        [sys.executable, "-c", script, *args], tmp_path,
        {"PATH": "/bin:/usr/bin", "MARKER": "value"}, timeout,
    )


def test_process_reports_streams_and_nonzero_status(tmp_path):
    script = """
import os, sys
from pathlib import Path
assert sys.stdin.read() == ""
sys.stdout.buffer.write(os.environ["MARKER"].encode() + b" " + os.fsencode(Path.cwd().name) + b"\\xff")
sys.stderr.write("problem\\n")
sys.exit(7)
"""
    assert run_python(tmp_path, script) == (f"value {tmp_path.name}�", "problem\n", 7)


def test_output_budget_counts_stdout_and_stderr_together(tmp_path):
    script = "import sys; sys.stdout.buffer.write(b'x' * 700000); sys.stderr.buffer.write(b'y' * 700000)"
    with pytest.raises(ValueError, match="输出|output"):
        run_python(tmp_path, script)


def test_process_times_out_when_open_streams_are_silent(tmp_path):
    with pytest.raises(TimeoutError):
        run_python(tmp_path, "import time; time.sleep(3)", timeout=0.1)


def test_timeout_still_applies_after_both_output_pipes_close(tmp_path):
    with pytest.raises(TimeoutError):
        run_python(tmp_path, "import os,time; os.close(1); os.close(2); time.sleep(3)", timeout=0.1)


@pytest.mark.parametrize("foreground", ["exit", "sleep"])
def test_lingering_background_process_releases_its_lock(tmp_path, foreground):
    worker = """
import fcntl, time
from pathlib import Path
with open("held", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    Path("ready").touch()
    time.sleep(5)
"""
    launcher = """
from pathlib import Path
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1]])
while not Path("ready").exists():
    time.sleep(0.005)
if sys.argv[2] == "sleep":
    time.sleep(5)
"""
    if foreground == "exit":
        assert run_python(tmp_path, launcher, worker, foreground, timeout=2) == ("", "", 0)
    else:
        with pytest.raises(TimeoutError):
            run_python(tmp_path, launcher, worker, foreground, timeout=0.7)
    assert (tmp_path / "ready").exists()
    deadline = time.monotonic() + 1
    with (tmp_path / "held").open() as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    pytest.fail("background process still holds its lock")
                time.sleep(0.01)
