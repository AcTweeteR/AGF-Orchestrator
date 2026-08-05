"""Atomic, project-isolated persistence and bounded search for memory entries."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .engineering_memory_models import MemoryEntry, MemoryValidationError, memory_from_dict
from .locking import project_lock


class MemoryStoreError(RuntimeError):
    """Raised when memory storage cannot safely complete an operation."""


class EngineeringMemoryStore:
    schema_version = "1.0"
    default_limit = 50
    maximum_limit = 200

    def __init__(self, state_dir: str | Path, project_id: str):
        if not project_id.startswith("project-") or "/" in project_id or "\\" in project_id:
            raise MemoryStoreError("project identity is invalid")
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_id = project_id
        self.path = self.state_dir / "memory" / project_id / "entries.json"

    def put(self, entry: MemoryEntry) -> MemoryEntry:
        """Atomically add an entry, accepting an identical retry only."""
        try:
            entry.validate()
        except MemoryValidationError as exc:
            raise MemoryStoreError(str(exc)) from exc
        if entry.project_id != self.project_id:
            raise MemoryStoreError("memory entry belongs to another project")
        with project_lock(self.state_dir, self.project_id, "memory-put", timeout=5.0):
            entries = self._load_unlocked()
            existing = next((item for item in entries if item.entry_id == entry.entry_id), None)
            if existing is not None:
                if existing == entry:
                    return existing
                raise MemoryStoreError("entry_id already exists with different content")
            self._save_unlocked(entries + [entry])
        return entry

    def get(self, entry_id: str) -> MemoryEntry:
        with project_lock(self.state_dir, self.project_id, "memory-get", timeout=5.0):
            for entry in self._load_unlocked():
                if entry.entry_id == entry_id:
                    return entry
        raise MemoryStoreError("memory entry was not found")

    def search(self, query: str = "", *, limit: int = default_limit) -> tuple[MemoryEntry, ...]:
        """Return active entries matching bounded terms in stable ID order."""
        if not isinstance(query, str) or len(query) > 400:
            raise MemoryStoreError("memory search query is invalid")
        invalid_limit = (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= self.maximum_limit
        )
        if invalid_limit:
            raise MemoryStoreError("memory search limit is invalid")
        terms = tuple(query.casefold().split())
        with project_lock(self.state_dir, self.project_id, "memory-search", timeout=5.0):
            entries = self._load_unlocked()
        matches = (
            entry for entry in entries
            if entry.superseded_at is None
            and all(term in self._search_text(entry) for term in terms)
        )
        return tuple(sorted(matches, key=lambda item: item.entry_id)[:limit])

    def _load_unlocked(self) -> list[MemoryEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.schema_version:
                raise MemoryStoreError("unsupported memory store schema")
            entries = [memory_from_dict(item) for item in payload.get("entries", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, MemoryStoreError):
                raise
            raise MemoryStoreError(f"invalid memory store: {exc}") from exc
        if any(entry.project_id != self.project_id for entry in entries):
            raise MemoryStoreError("memory store contains a foreign project entry")
        return entries

    def _save_unlocked(self, entries: list[MemoryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".entries.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {
                        "schema_version": self.schema_version,
                        "entries": [item.to_dict() for item in entries],
                    },
                    handle, ensure_ascii=False, indent=2, sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise MemoryStoreError(f"memory store write failed: {exc}") from exc

    @staticmethod
    def _search_text(entry: MemoryEntry) -> str:
        return " ".join((entry.title, entry.summary, *entry.tags)).casefold()
