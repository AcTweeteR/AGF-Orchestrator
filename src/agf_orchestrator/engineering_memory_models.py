"""Bounded, project-isolated engineering memory records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MemoryValidationError(ValueError):
    """Raised when an engineering memory entry is invalid."""


class MemoryEntryType(StrEnum):
    ADR = "ADR"
    RFC = "RFC"
    INVARIANT = "INVARIANT"
    SECURITY_DECISION = "SECURITY_DECISION"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    TECHNICAL_DEBT = "TECHNICAL_DEBT"
    TEMPORARY_EXCEPTION = "TEMPORARY_EXCEPTION"
    INCIDENT = "INCIDENT"
    RECURRING_FINDING = "RECURRING_FINDING"
    PERFORMANCE_BASELINE = "PERFORMANCE_BASELINE"
    COMPATIBILITY_REQUIREMENT = "COMPATIBILITY_REQUIREMENT"
    KNOWN_LIMITATION = "KNOWN_LIMITATION"


class MemorySensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"


_ID = re.compile(r"^memory-[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT_ID = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_REQUIREMENT_ID = re.compile(r"^requirement-[a-z0-9][a-z0-9-]{0,79}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET_SHAPED = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")
_TRANSCRIPT_SHAPED = re.compile(
    r"(?i)\b(?:complete\s+)?transcript\b|conversation\s+log|chat\s+history"
)
_MAX_TEXT = 4000
_MAX_ITEMS = 200


@dataclass(frozen=True)
class MemoryEntry:
    schema_version: str
    project_id: str
    entry_id: str
    entry_type: MemoryEntryType
    title: str
    summary: str
    tags: tuple[str, ...]
    requirement_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    actor: str
    created_at: str
    superseded_at: str | None
    supersedes_entry_id: str | None
    content_sha256: str
    sensitivity: MemorySensitivity

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "entry_id": self.entry_id,
            "entry_type": self.entry_type.value,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "requirement_refs": list(self.requirement_refs),
            "evidence_refs": list(self.evidence_refs),
            "actor": self.actor,
            "created_at": self.created_at,
            "superseded_at": self.superseded_at,
            "supersedes_entry_id": self.supersedes_entry_id,
            "content_sha256": self.content_sha256,
            "sensitivity": self.sensitivity.value,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise MemoryValidationError("schema_version must be 1.0")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise MemoryValidationError("project_id is invalid")
        if not _ID.fullmatch(self.entry_id):
            raise MemoryValidationError("entry_id is invalid")
        if not isinstance(self.entry_type, MemoryEntryType):
            raise MemoryValidationError("entry_type is invalid")
        if not isinstance(self.sensitivity, MemorySensitivity):
            raise MemoryValidationError("sensitivity is invalid")
        for label, value in (
            ("title", self.title), ("summary", self.summary), ("actor", self.actor)
        ):
            self._bounded_text(label, value)
        self._bounded_list("tags", self.tags, allow_empty=True)
        self._bounded_list("requirement_refs", self.requirement_refs, allow_empty=True)
        self._bounded_list("evidence_refs", self.evidence_refs, allow_empty=True)
        if any(not _REQUIREMENT_ID.fullmatch(value) for value in self.requirement_refs):
            raise MemoryValidationError("requirement_refs contain an invalid ID")
        if not _TIMESTAMP.fullmatch(self.created_at):
            raise MemoryValidationError("created_at is invalid")
        if self.superseded_at is not None and not _TIMESTAMP.fullmatch(self.superseded_at):
            raise MemoryValidationError("superseded_at is invalid")
        if self.supersedes_entry_id is not None:
            if not _ID.fullmatch(self.supersedes_entry_id):
                raise MemoryValidationError("supersedes_entry_id is invalid")
            if self.supersedes_entry_id == self.entry_id:
                raise MemoryValidationError("an entry cannot supersede itself")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise MemoryValidationError("content_sha256 is invalid")
        if self.content_sha256 != memory_entry_hash(self):
            raise MemoryValidationError("content_sha256 does not match entry content")

    @staticmethod
    def _bounded_text(label: str, value: Any) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
            raise MemoryValidationError(f"{label} must be a bounded non-empty string")
        if _SECRET_SHAPED.search(value):
            raise MemoryValidationError(f"{label} contains secret-shaped data")
        if _TRANSCRIPT_SHAPED.search(value):
            raise MemoryValidationError(f"{label} contains transcript-shaped data")

    @classmethod
    def _bounded_list(cls, label: str, values: Any, *, allow_empty: bool) -> None:
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_ITEMS:
            raise MemoryValidationError(f"{label} must be a bounded list")
        if not allow_empty and not values:
            raise MemoryValidationError(f"{label} must not be empty")
        for value in values:
            cls._bounded_text(label, value)


def _content_dict(entry: MemoryEntry) -> dict[str, Any]:
    payload = entry.to_dict()
    payload["content_sha256"] = ""
    return payload


def memory_entry_hash(entry: MemoryEntry) -> str:
    """Return the deterministic hash of an entry excluding its stored hash."""
    encoded = json.dumps(
        _content_dict(entry), ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def memory_from_dict(payload: dict[str, Any]) -> MemoryEntry:
    """Construct and validate an exact JSON-shaped memory entry."""
    required = {
        "schema_version", "project_id", "entry_id", "entry_type", "title", "summary",
        "tags", "requirement_refs", "evidence_refs", "actor", "created_at", "superseded_at",
        "supersedes_entry_id", "content_sha256", "sensitivity",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise MemoryValidationError("memory schema is missing or contains unknown fields")
    try:
        entry = MemoryEntry(
            schema_version=payload["schema_version"], project_id=payload["project_id"],
            entry_id=payload["entry_id"], entry_type=MemoryEntryType(payload["entry_type"]),
            title=payload["title"], summary=payload["summary"], tags=tuple(payload["tags"]),
            requirement_refs=tuple(payload["requirement_refs"]),
            evidence_refs=tuple(payload["evidence_refs"]), actor=payload["actor"],
            created_at=payload["created_at"], superseded_at=payload["superseded_at"],
            supersedes_entry_id=payload["supersedes_entry_id"],
            content_sha256=payload["content_sha256"],
            sensitivity=MemorySensitivity(payload["sensitivity"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MemoryValidationError(f"invalid memory structure: {exc}") from exc
    entry.validate()
    return entry
