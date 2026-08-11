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
        self.state_dir = Path(configured).expanduser().absolute()
        self.sessions_dir = self.state_dir / "sessions"
        self.artifacts_dir = self.state_dir / "artifacts"
        self.locks_dir = self.state_dir / "locks"

    def ensure_safe_path(self, path: str | Path) -> Path:
        base = self.state_dir.absolute()
        raw = Path(os.path.normpath(str(Path(path).absolute())))
        if base.is_symlink():
            raise SessionStoreError("state root must not be a symlink")
        absolute_parts = raw.parts[1:] if raw.anchor else raw.parts
        current = Path(raw.anchor or "/")
        for component in absolute_parts:
            current /= component
            if current.is_symlink():
                raise SessionStoreError("state path must not contain symlinks")
        try:
            relative = raw.relative_to(base)
        except ValueError as exc:
            raise SessionStoreError("path escaped state root") from exc
        current = base
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise SessionStoreError("state path must not contain symlinks")
        return raw

    def _path(self, session_id: str) -> Path:
        if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id:
            raise SessionStoreError("invalid session identifier")
        self.ensure_safe_path(self.sessions_dir)
        path = self.ensure_safe_path(self.sessions_dir / f"{session_id}.json")
        return path

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
        self.ensure_safe_path(self.sessions_dir)
        if not self.sessions_dir.exists():
            return []
        return [self.load(path.stem) for path in sorted(self.sessions_dir.glob("*.json"))]

    def write_artifact(self, session_id: str, name: str, content: str) -> tuple[str, str]:
        if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id:
            raise SessionStoreError("invalid session identifier")
        if Path(name).name != name or name.endswith(".json") is False:
            raise SessionStoreError("artifact name must be a simple JSON filename")
        directory = self.artifacts_dir / session_id
        self.ensure_safe_path(self.artifacts_dir)
        self.ensure_safe_path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.ensure_safe_path(directory / name)
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() == hashlib.sha256(content.encode()).hexdigest():
                return str(path), hashlib.sha256(existing).hexdigest()
            raise SessionStoreError("artifact is immutable and already exists")
        temporary_fd, temporary_name = tempfile.mkstemp(
            dir=directory, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = path.read_bytes()
                if hashlib.sha256(existing).hexdigest() != hashlib.sha256(
                    content.encode()
                ).hexdigest():
                    raise SessionStoreError("artifact is immutable and already exists")
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return str(path), hashlib.sha256(content.encode()).hexdigest()

    def artifact_hash(self, path: str) -> str:
        artifact = self.ensure_safe_path(path)
        if artifact.is_symlink():
            raise SessionStoreError("artifact read cannot follow a symlink")
        return hashlib.sha256(artifact.read_bytes()).hexdigest()
