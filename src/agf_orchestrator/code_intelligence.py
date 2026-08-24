"""Provider-neutral, advisory code-intelligence evidence.

Providers return evidence only.  AGF decides eligibility, paths, risk,
execution, delivery, and completion through the existing governed layers.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .capability_selection import (
    CapabilityCandidate,
    CapabilitySelector,
    SelectionGates,
    SelectionResult,
)
from .session_store import SessionStore, SessionStoreError


class CodeIntelligenceError(ValueError):
    """Raised for malformed or unsafe intelligence evidence."""


class IntelligenceStatus(StrEnum):
    VALID = "VALID"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
    MALFORMED = "MALFORMED"
    MISMATCHED_PROJECT = "MISMATCHED_PROJECT"
    MISMATCHED_REPOSITORY = "MISMATCHED_REPOSITORY"
    MISMATCHED_REVISION = "MISMATCHED_REVISION"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    BLOCKED_PATH = "BLOCKED_PATH"


class IntelligenceOperation(StrEnum):
    SYMBOL = "SYMBOL"
    DEFINITION = "DEFINITION"
    REFERENCES = "REFERENCES"
    NAVIGATION = "NAVIGATION"
    EDIT_TARGET = "EDIT_TARGET"


_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")
_MAX_TEXT = 4000
_MAX_LOCATIONS = 256
_MAX_CONTEXT = 32


def _text(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise CodeIntelligenceError(f"{label} is invalid")
    if _SECRET.search(value):
        raise CodeIntelligenceError(f"{label} contains secret-shaped data")


def _relative_path(value: Any) -> None:
    _text("location path", value)
    path = Path(value)
    if (
        path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise CodeIntelligenceError("location path must be repository-relative")


def _sha(label: str, value: Any, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CodeIntelligenceError(f"{label} is invalid")


class CodeIntelligenceProvider(Protocol):
    """Provider contract: return evidence, never mutate or authorize."""

    provider_id: str

    def query(self, request: "CodeIntelligenceRequest") -> "CodeIntelligenceEvidence":
        ...


@dataclass(frozen=True)
class CodeLocation:
    path: str
    start_line: int
    end_line: int
    symbol: str | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def validate(self) -> None:
        _relative_path(self.path)
        if (
            not isinstance(self.start_line, int)
            or isinstance(self.start_line, bool)
            or self.start_line < 1
            or not isinstance(self.end_line, int)
            or isinstance(self.end_line, bool)
            or self.end_line < self.start_line
            or self.end_line - self.start_line > 1000
        ):
            raise CodeIntelligenceError("location line range is invalid")
        if self.symbol is not None:
            _text("location symbol", self.symbol)
        if self.kind is not None:
            _text("location kind", self.kind)


@dataclass(frozen=True)
class CodeIntelligenceRequest:
    project_id: str
    repository_id: str
    revision_sha: str
    operation: IntelligenceOperation
    query: str
    allowed_paths: tuple[str, ...] = ()

    def validate(self) -> None:
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise CodeIntelligenceError("project_id is invalid")
        _text("repository_id", self.repository_id)
        _sha("revision_sha", self.revision_sha, _SHA1)
        if not isinstance(self.operation, IntelligenceOperation):
            raise CodeIntelligenceError("operation is invalid")
        _text("query", self.query)
        for path in self.allowed_paths:
            _relative_path(path)


@dataclass(frozen=True)
class CodeIntelligenceEvidence:
    schema_version: str
    evidence_id: str
    provider_id: str
    project_id: str
    repository_id: str
    revision_sha: str
    index_revision_sha: str | None
    operation: IntelligenceOperation
    query: str
    locations: tuple[CodeLocation, ...]
    context: tuple[str, ...]
    provenance: str
    observed_at: str
    status: IntelligenceStatus
    evidence_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "provider_id": self.provider_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "revision_sha": self.revision_sha,
            "index_revision_sha": self.index_revision_sha,
            "operation": self.operation.value,
            "query": self.query,
            "locations": [item.to_dict() for item in self.locations],
            "context": list(self.context),
            "provenance": self.provenance,
            "observed_at": self.observed_at,
            "status": self.status.value,
            "evidence_sha256": self.evidence_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.evidence_id):
            raise CodeIntelligenceError("evidence identity is invalid")
        if not _ID.fullmatch(self.provider_id):
            raise CodeIntelligenceError("provider_id is invalid")
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise CodeIntelligenceError("project_id is invalid")
        _text("repository_id", self.repository_id)
        _sha("revision_sha", self.revision_sha, _SHA1)
        if self.index_revision_sha is not None:
            _sha("index_revision_sha", self.index_revision_sha, _SHA1)
        if not isinstance(self.operation, IntelligenceOperation):
            raise CodeIntelligenceError("operation is invalid")
        _text("query", self.query)
        if len(self.locations) > _MAX_LOCATIONS:
            raise CodeIntelligenceError("locations exceed the bound")
        identities = set()
        for location in self.locations:
            location.validate()
            identity = (location.path, location.start_line, location.end_line, location.symbol)
            if identity in identities:
                raise CodeIntelligenceError("duplicate locations are invalid")
            identities.add(identity)
        if len(self.context) > _MAX_CONTEXT:
            raise CodeIntelligenceError("context exceeds the bound")
        for item in self.context:
            _text("context", item)
        _text("provenance", self.provenance)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", self.observed_at):
            raise CodeIntelligenceError("observed_at is invalid")
        if not isinstance(self.status, IntelligenceStatus):
            raise CodeIntelligenceError("status is invalid")
        if self.status is IntelligenceStatus.VALID and self.index_revision_sha != self.revision_sha:
            raise CodeIntelligenceError("valid evidence requires matching index revision")
        _sha("evidence_sha256", self.evidence_sha256, _SHA256)
        if self.evidence_sha256 != evidence_hash(self):
            raise CodeIntelligenceError("evidence_sha256 does not match content")

    def assess(self, request: CodeIntelligenceRequest) -> IntelligenceStatus:
        self.validate()
        request.validate()
        if self.project_id != request.project_id:
            return IntelligenceStatus.MISMATCHED_PROJECT
        if self.repository_id != request.repository_id:
            return IntelligenceStatus.MISMATCHED_REPOSITORY
        if self.revision_sha != request.revision_sha:
            return IntelligenceStatus.MISMATCHED_REVISION
        if self.status is not IntelligenceStatus.VALID:
            return self.status
        def allowed(path: str, pattern: str) -> bool:
            return (
                path == pattern
                or path.startswith(pattern.rstrip("/") + "/")
                or PurePosixPath(path).match(pattern)
            )

        if request.allowed_paths and any(
            not any(allowed(location.path, pattern) for pattern in request.allowed_paths)
            for location in self.locations
        ):
            return IntelligenceStatus.BLOCKED_PATH
        return IntelligenceStatus.VALID


def evidence_hash(evidence: CodeIntelligenceEvidence) -> str:
    payload = evidence.to_dict()
    payload["evidence_sha256"] = ""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evidence_from_dict(payload: dict[str, Any]) -> CodeIntelligenceEvidence:
    required = set(CodeIntelligenceEvidence.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != required:
        raise CodeIntelligenceError("evidence schema is missing or contains unknown fields")
    try:
        value = CodeIntelligenceEvidence(
            payload["schema_version"], payload["evidence_id"], payload["provider_id"],
            payload["project_id"], payload["repository_id"], payload["revision_sha"],
            payload["index_revision_sha"], IntelligenceOperation(payload["operation"]),
            payload["query"], tuple(CodeLocation(**item) for item in payload["locations"]),
            tuple(payload["context"]), payload["provenance"], payload["observed_at"],
            IntelligenceStatus(payload["status"]), payload["evidence_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CodeIntelligenceError("evidence structure is invalid") from exc
    value.validate()
    return value


def seal(evidence: CodeIntelligenceEvidence) -> CodeIntelligenceEvidence:
    value = CodeIntelligenceEvidence(**{**evidence.__dict__, "evidence_sha256": ""})
    value = CodeIntelligenceEvidence(**{**value.__dict__, "evidence_sha256": evidence_hash(value)})
    value.validate()
    return value


def persist_evidence(
    store: SessionStore, session_id: str, evidence: CodeIntelligenceEvidence
) -> tuple[str, str]:
    evidence.validate()
    return store.write_artifact(
        session_id, f"code-intelligence-{evidence.evidence_id}.json",
        json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
    )


def load_evidence(
    store: SessionStore, session_id: str, evidence_id: str
) -> CodeIntelligenceEvidence:
    if not _ID.fullmatch(evidence_id):
        raise CodeIntelligenceError("evidence_id is invalid")
    path = (
        store._path(session_id).parent.parent
        / "artifacts"
        / session_id
        / f"code-intelligence-{evidence_id}.json"
    )
    try:
        path = store.ensure_safe_path(path)
        return evidence_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, SessionStoreError, json.JSONDecodeError, TypeError) as exc:
        raise CodeIntelligenceError("code-intelligence evidence is unavailable") from exc


@dataclass(frozen=True)
class ProviderResolution:
    status: IntelligenceStatus
    selection: SelectionResult | None
    reason: str


def resolve_provider(
    candidates: tuple[CapabilityCandidate, ...], *, project_id: str,
    required: bool, now: str, gates: SelectionGates,
) -> ProviderResolution:
    try:
        selection = CapabilitySelector().select(
            candidates, project_id=project_id, required_capabilities=("code-intelligence",),
            now=now, gates=gates,
        )
        return ProviderResolution(IntelligenceStatus.VALID, selection, "eligible")
    except (ValueError, TypeError) as exc:
        if "required capability is not supported" in str(exc):
            return ProviderResolution(IntelligenceStatus.UNSUPPORTED_CAPABILITY, None, str(exc))
        if required:
            return ProviderResolution(IntelligenceStatus.UNAVAILABLE, None, f"required:{exc}")
        return ProviderResolution(IntelligenceStatus.UNAVAILABLE, None, f"optional:{exc}")


@dataclass(frozen=True)
class EfficiencyComparison:
    baseline_items: int
    assisted_items: int

    @property
    def reduction(self) -> int:
        return max(self.baseline_items - self.assisted_items, 0)

    @property
    def improved(self) -> bool:
        return self.assisted_items < self.baseline_items


def compare_efficiency(
    repository_paths: tuple[str, ...], evidence: CodeIntelligenceEvidence
) -> EfficiencyComparison:
    evidence.validate()
    selected = {location.path for location in evidence.locations}
    if not selected.issubset(set(repository_paths)):
        raise CodeIntelligenceError(
            "efficiency evidence references paths outside repository fixture"
        )
    return EfficiencyComparison(len(repository_paths), len(selected))
