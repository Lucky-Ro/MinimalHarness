"""Shared persistence primitives for session transcripts and per-session todos."""

import fcntl
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


def _check_spelling(path: Path) -> None:
    # Some volumes resolve a different name to the same file. Session identity
    # is case-sensitive even when the underlying volume isn't.
    if path.exists():
        with os.scandir(path.parent) as entries:
            if not any(entry.name == path.name for entry in entries):
                raise ValueError("Session filename differs in capitalization")


def session_path(root: Path, session_id: str, suffix: str) -> Path:
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError("Invalid session ID")
    if not re.fullmatch(r"\.[a-z]+", suffix):
        raise ValueError("Invalid file suffix")
    root = Path(root)
    path = root / (session_id + suffix)
    if root.is_symlink() or path.is_symlink():
        raise ValueError("Session storage cannot use a symbolic link")
    _check_spelling(path)
    return path


def atomic_write(path: Path, text: str) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Refusing to write through a symbolic link")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            # Publish a first version only if no other writer claimed the name.
            # Concurrent writers for exactly the same spelling can still replace it.
            try:
                os.link(pending, path)
            except FileExistsError:
                _check_spelling(path)
                os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


@contextmanager
def session_lock(root: Path, session_id: str) -> Iterator[None]:
    path = session_path(root, session_id, ".lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep the lock file across turns. Deleting it could let two processes lock
    # different inodes while updating the same transcript.
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _check_spelling(path)
        yield
    finally:
        os.close(fd)
