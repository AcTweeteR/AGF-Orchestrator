"""Non-destructive per-session and per-project filesystem locks."""

from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - supported runtime is POSIX
    fcntl = None


class LockError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FileLock:
    def __init__(self, path: str | Path, operation: str, timeout: float = 0.0):
        self.path = Path(path)
        self.operation = operation
        self.timeout = timeout
        self.handle = None

    def acquire(self) -> None:
        if fcntl is None:
            raise LockError("filesystem locking is unsupported on this platform")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                metadata = {
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "acquired_at": _now(),
                    "operation": self.operation,
                }
                self.handle.seek(0)
                self.handle.truncate()
                self.handle.write(json.dumps(metadata, sort_keys=True))
                self.handle.flush()
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise LockError(f"lock is held: {self.path.name}")
                time.sleep(0.02)

    def release(self) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def lock_status(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"locked": False, "path": str(path)}
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            locked = True
        else:
            locked = False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
    try:
        metadata = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        metadata = {"status": "unreadable"}
    return {
        "locked": locked,
        "path": str(path),
        "metadata": metadata,
        "diagnostic": "stale locks are reported but never broken",
    }


@contextmanager
def session_lock(state_dir: str | Path, session_id: str, operation: str, timeout: float = 0.0):
    with FileLock(Path(state_dir) / "locks" / f"session-{session_id}.lock", operation, timeout):
        yield


@contextmanager
def project_lock(state_dir: str | Path, project_id: str, operation: str, timeout: float = 0.0):
    with FileLock(Path(state_dir) / "locks" / f"project-{project_id}.lock", operation, timeout):
        yield
