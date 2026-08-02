"""Atomic local session and artifact storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .session_models import Session, session_from_dict


class SessionStoreError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, state_dir: str | Path | None = None):
        configured = state_dir or os.environ.get("AGF_STATE_DIR") or "~/.agf-orchestrator"
        self.state_dir = Path(configured).expanduser().resolve()
        self.sessions_dir = self.state_dir / "sessions"
        self.artifacts_dir = self.state_dir / "artifacts"
        self.locks_dir = self.state_dir / "locks"

    def _path(self, session_id: str) -> Path:
        if not session_id or Path(session_id).name != session_id:
            raise SessionStoreError("invalid session identifier")
        return self.sessions_dir / f"{session_id}.json"

    def save(self, session: Session) -> None:
        path = self._path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(session.to_dict(), indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise

    def load(self, session_id: str) -> Session:
        try:
            payload = json.loads(self._path(session_id).read_text(encoding="utf-8"))
            return session_from_dict(payload)
        except FileNotFoundError as exc:
            raise SessionStoreError("session not found") from exc
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            raise SessionStoreError(str(exc)) from exc

    def list(self) -> list[Session]:
        if not self.sessions_dir.exists():
            return []
        return [self.load(path.stem) for path in sorted(self.sessions_dir.glob("*.json"))]

    def write_artifact(self, session_id: str, name: str, content: str) -> tuple[str, str]:
        if Path(name).name != name or name.endswith(".json") is False:
            raise SessionStoreError("artifact name must be a simple JSON filename")
        directory = self.artifacts_dir / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return str(path), hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def artifact_hash(path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
