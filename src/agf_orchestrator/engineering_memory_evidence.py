"""Bounded evidence records for memory queries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .engineering_memory_models import MemoryEntry


class MemoryEvidenceError(ValueError):
    """Raised when memory query evidence is missing or unbounded."""


_ENTRY_ID = re.compile(r"^memory-[a-z0-9][a-z0-9-]{0,79}$")
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")
_TRANSCRIPT = re.compile(r"(?i)\btranscript\b|conversation\s+log|chat\s+history")
_MAX_QUERY = 400
_MAX_RESULTS = 200


@dataclass(frozen=True)
class MemoryQueryEvidence:
    query: str
    limit: int
    result_ids: tuple[str, ...]

    def validate(self) -> None:
        if not isinstance(self.query, str) or len(self.query) > _MAX_QUERY:
            raise MemoryEvidenceError("memory query is invalid")
        if _SECRET.search(self.query) or _TRANSCRIPT.search(self.query):
            raise MemoryEvidenceError("memory query contains prohibited content")
        invalid_limit = (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not 1 <= self.limit <= _MAX_RESULTS
        )
        if invalid_limit:
            raise MemoryEvidenceError("memory query limit is invalid")
        if len(self.result_ids) > _MAX_RESULTS or len(set(self.result_ids)) != len(self.result_ids):
            raise MemoryEvidenceError("memory query results are invalid")
        if any(not _ENTRY_ID.fullmatch(item) for item in self.result_ids):
            raise MemoryEvidenceError("memory query result ID is invalid")

    def to_text(self) -> str:
        self.validate()
        terms = " ".join(sorted(set(self.query.casefold().split()))) or "<all>"
        results = ",".join(sorted(self.result_ids)) or "<none>"
        return f"memory-query: terms={terms}; limit={self.limit}; result_ids={results}"


def query_evidence(query: str, limit: int, entries: tuple[MemoryEntry, ...]) -> str:
    """Create bounded evidence without copying memory content into reports."""
    if any(not isinstance(entry, MemoryEntry) for entry in entries):
        raise MemoryEvidenceError("memory query entries are invalid")
    return MemoryQueryEvidence(query, limit, tuple(entry.entry_id for entry in entries)).to_text()


def validate_query_evidence(value: str) -> None:
    if not isinstance(value, str) or len(value) > 2000 or not value.startswith("memory-query:"):
        raise MemoryEvidenceError("memory query evidence is invalid")
    if _SECRET.search(value) or _TRANSCRIPT.search(value):
        raise MemoryEvidenceError("memory query evidence contains prohibited content")
