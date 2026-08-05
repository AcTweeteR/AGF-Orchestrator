"""Atomic persistence and resumable transitions for scheduler state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .locking import project_lock
from .scheduler_models import (
    SchedulerState,
    SchedulerStatus,
    SchedulerValidationError,
    scheduler_from_dict,
)


class SchedulerStoreError(RuntimeError):
    """Raised when scheduler state cannot safely be persisted or resumed."""


class SchedulerStore:
    schema_version = "1.0"

    def __init__(self, state_dir: str | Path, project_id: str, scheduler_id: str):
        if not project_id.startswith("project-") or "/" in project_id or "\\" in project_id:
            raise SchedulerStoreError("project identity is invalid")
        if not scheduler_id.startswith("scheduler-") or "/" in scheduler_id or "\\" in scheduler_id:
            raise SchedulerStoreError("scheduler identity is invalid")
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_id = project_id
        self.scheduler_id = scheduler_id
        self.path = self.state_dir / "schedulers" / project_id / f"{scheduler_id}.json"

    def create(self, state: SchedulerState) -> SchedulerState:
        """Persist a new state, accepting an identical restart retry."""
        self._validate_identity(state)
        with project_lock(self.state_dir, self.project_id, "scheduler-create", timeout=5.0):
            existing = self._load_unlocked()
            if existing is not None:
                if existing == state:
                    return existing
                raise SchedulerStoreError("scheduler state already exists with different content")
            self._save_unlocked(state)
        return state

    def load(self) -> SchedulerState:
        with project_lock(self.state_dir, self.project_id, "scheduler-load", timeout=5.0):
            state = self._load_unlocked()
        if state is None:
            raise SchedulerStoreError("scheduler state was not found")
        return state

    def transition(self, status: SchedulerStatus) -> SchedulerState:
        """Persist one lifecycle transition; retrying the current status is idempotent."""
        with project_lock(self.state_dir, self.project_id, "scheduler-transition", timeout=5.0):
            current = self._load_unlocked()
            if current is None:
                raise SchedulerStoreError("scheduler state was not found")
            if current.status is status:
                return current
            try:
                updated = current.transition(status)
            except SchedulerValidationError as exc:
                raise SchedulerStoreError(str(exc)) from exc
            self._save_unlocked(updated)
            return updated

    def _validate_identity(self, state: SchedulerState) -> None:
        try:
            state.validate()
        except SchedulerValidationError as exc:
            raise SchedulerStoreError(str(exc)) from exc
        if state.project_id != self.project_id or state.scheduler_id != self.scheduler_id:
            raise SchedulerStoreError("scheduler state identity does not match store")

    def _load_unlocked(self) -> SchedulerState | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return scheduler_from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, SchedulerStoreError):
                raise
            raise SchedulerStoreError(f"invalid scheduler state: {exc}") from exc

    def _save_unlocked(self, state: SchedulerState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".scheduler.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise SchedulerStoreError(f"scheduler state write failed: {exc}") from exc
