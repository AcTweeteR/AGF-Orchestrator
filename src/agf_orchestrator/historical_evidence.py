"""Verification of owner-controlled historical risk evidence.

Runtime can consume this evidence but cannot create, sign, activate, or widen
its coverage. Missing or unverifiable evidence remains UNKNOWN.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .authority_context import resolve_authority
from .owner_authority import OwnerAuthorityError, verify_envelope


class HistoricalEvidenceError(ValueError):
    """Raised when historical evidence is present but invalid."""


@dataclass(frozen=True)
class HistoricalBaseline:
    baseline_id: str
    project_id: str
    coverage_start: str
    policy_hash: str
    constitution_id: str
    authority_generation: int
    generated_at: str


class EvidenceStatus(StrEnum):
    VERIFIED_ZERO = "VERIFIED_ZERO"
    VERIFIED_EVENTS = "VERIFIED_EVENTS"
    UNKNOWN = "UNKNOWN"


_PROJECT = re.compile(r"^project-[0-9a-f]{16}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_BASELINE = re.compile(r"^baseline-[a-z0-9-]{8,80}$")
_TYPES = frozenset({"rollback", "incident"})


@dataclass(frozen=True)
class HistoricalEvidence:
    project_id: str
    evidence_type: str
    status: EvidenceStatus
    count: int
    baseline_id: str
    coverage_before_baseline: str
    coverage_start: str
    coverage_end: str
    definition_version: str
    source_refs: tuple[str, ...]
    source_hashes: tuple[str, ...]
    policy_hash: str
    constitution_id: str
    authority_generation: int
    generated_at: str
    provenance: str
    coverage_complete: bool
    completeness_basis: str
    evidence_hash: str


def load_historical_evidence(
    project_id: str,
    evidence_type: str,
    *,
    state_root: str | Path | None = None,
    required_start: str | None = None,
    required_end: str | None = None,
    max_age_seconds: int = 86400,
) -> HistoricalEvidence | None:
    """Load and verify one signed evidence record; absent state is UNKNOWN."""
    if not _PROJECT.fullmatch(project_id) or evidence_type not in _TYPES:
        raise HistoricalEvidenceError("historical evidence identity is invalid")
    root = Path(state_root or (Path.home() / ".agf-orchestrator")).expanduser().resolve()
    namespace = root / "historical-evidence"
    directory = namespace / project_id
    if namespace.is_symlink() or directory.is_symlink():
        raise HistoricalEvidenceError("historical evidence namespace must not use symlinks")
    path = directory / f"{evidence_type}.json"
    if not path.exists():
        return None
    try:
        if path.is_symlink():
            raise HistoricalEvidenceError("historical evidence must not use symlinks")
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document["payload"]
        envelope = document["envelope"]
        verify_envelope(payload, envelope)
    except OwnerAuthorityError as exc:
        raise HistoricalEvidenceError("historical evidence signature is invalid") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HistoricalEvidenceError("historical evidence is unreadable") from exc
    evidence = _parse(payload, evidence_type, expected_project_id=project_id)
    try:
        start = _timestamp(evidence.coverage_start)
        end = _timestamp(evidence.coverage_end)
        generated = _timestamp(evidence.generated_at)
        now = datetime.now(UTC)
        if generated < start or end < start or end > now or generated > now:
            return None
        if now - generated > timedelta(seconds=max_age_seconds):
            return None
        if required_start is not None and start > _timestamp(required_start):
            return None
        if required_end is not None and end < _timestamp(required_end):
            return None
    except (TypeError, ValueError):
        return None
    return evidence


def load_historical_baseline(
    project_id: str,
    *,
    state_root: str | Path | None = None,
    max_age_seconds: int = 86400,
) -> HistoricalBaseline | None:
    """Load the separately signed owner baseline bound to a project."""
    if not _PROJECT.fullmatch(project_id):
        raise HistoricalEvidenceError("historical baseline identity is invalid")
    root = Path(state_root or (Path.home() / ".agf-orchestrator")).expanduser().resolve()
    namespace = root / "historical-evidence"
    directory = namespace / project_id
    if namespace.is_symlink() or directory.is_symlink():
        raise HistoricalEvidenceError("historical baseline namespace must not use symlinks")
    path = directory / "baseline.json"
    if not path.exists():
        return None
    try:
        if path.is_symlink():
            raise HistoricalEvidenceError("historical baseline must not use symlinks")
        document = json.loads(path.read_text(encoding="utf-8"))
        payload, envelope = document["payload"], document["envelope"]
        verify_envelope(payload, envelope)
    except OwnerAuthorityError as exc:
        raise HistoricalEvidenceError("historical baseline signature is invalid") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HistoricalEvidenceError("historical baseline is unreadable") from exc
    required = {
        "schema_version", "baseline_id", "project_id", "coverage_start",
        "policy_hash", "constitution_id", "authority_generation", "generated_at",
        "provenance",
    }
    if set(payload) != required or payload["schema_version"] != "1.0":
        raise HistoricalEvidenceError("historical baseline schema is invalid")
    if payload["project_id"] != project_id or not _BASELINE.fullmatch(payload["baseline_id"]):
        raise HistoricalEvidenceError("historical baseline binding is invalid")
    if not _HEX.fullmatch(payload["policy_hash"]):
        raise HistoricalEvidenceError("historical baseline policy is invalid")
    if not isinstance(payload["authority_generation"], int) or payload["authority_generation"] < 1:
        raise HistoricalEvidenceError("historical baseline generation is invalid")
    try:
        _timestamp(payload["coverage_start"])
        generated = _timestamp(payload["generated_at"])
        if generated < _timestamp(payload["coverage_start"]):
            return None
        if generated > datetime.now(UTC) or datetime.now(UTC) - generated > timedelta(
            seconds=max_age_seconds
        ):
            return None
    except (TypeError, ValueError):
        return None
    baseline = HistoricalBaseline(
        payload["baseline_id"], project_id, payload["coverage_start"],
        payload["policy_hash"], payload["constitution_id"],
        payload["authority_generation"], payload["generated_at"],
    )
    verify_current_baseline_bindings(baseline)
    return baseline


def _parse(payload: Any, evidence_type: str, *, expected_project_id: str) -> HistoricalEvidence:
    required = {
        "schema_version", "project_id", "evidence_type", "status", "count",
        "baseline_id", "coverage_before_baseline", "coverage_start", "coverage_end",
        "definition_version", "source_refs",
        "source_hashes", "policy_hash", "constitution_id", "authority_generation",
        "generated_at", "provenance", "coverage_complete", "completeness_basis",
        "evidence_hash",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise HistoricalEvidenceError("historical evidence schema is invalid")
    if payload["schema_version"] != "1.0" or payload["evidence_type"] != evidence_type:
        raise HistoricalEvidenceError("historical evidence identity is invalid")
    try:
        status = EvidenceStatus(payload["status"])
        count = payload["count"]
        generation = payload["authority_generation"]
        refs = tuple(payload["source_refs"])
        hashes = tuple(payload["source_hashes"])
    except (TypeError, ValueError) as exc:
        raise HistoricalEvidenceError("historical evidence values are invalid") from exc
    if (
        not _PROJECT.fullmatch(payload["project_id"])
        or payload["project_id"] != expected_project_id
    ):
        raise HistoricalEvidenceError("historical evidence project is invalid")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise HistoricalEvidenceError("historical evidence count is invalid")
    if not isinstance(payload["baseline_id"], str) or not _BASELINE.fullmatch(
        payload["baseline_id"]
    ):
        raise HistoricalEvidenceError("historical evidence baseline is invalid")
    if payload["coverage_before_baseline"] != "UNKNOWN":
        raise HistoricalEvidenceError("historical pre-baseline coverage is invalid")
    if (status is EvidenceStatus.VERIFIED_ZERO) != (count == 0):
        raise HistoricalEvidenceError("historical evidence status/count mismatch")
    if status is EvidenceStatus.VERIFIED_EVENTS and count == 0:
        raise HistoricalEvidenceError("verified events require a positive count")
    if not refs or len(refs) != len(hashes) or any(not isinstance(item, str) for item in refs):
        raise HistoricalEvidenceError("historical evidence sources are invalid")
    if any(not _HEX.fullmatch(item) for item in hashes):
        raise HistoricalEvidenceError("historical source hashes are invalid")
    if payload["coverage_complete"] is not True or not isinstance(
        payload["completeness_basis"], str
    ) or not payload["completeness_basis"].startswith("owner-completeness-v1:"):
        raise HistoricalEvidenceError("historical coverage completeness is invalid")
    if not _HEX.fullmatch(payload["policy_hash"]):
        raise HistoricalEvidenceError("historical policy binding is invalid")
    if not isinstance(generation, int) or generation < 1:
        raise HistoricalEvidenceError("historical authority generation is invalid")
    canonical = dict(payload)
    canonical.pop("evidence_hash")
    actual_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if actual_hash != payload["evidence_hash"]:
        raise HistoricalEvidenceError("historical evidence hash is invalid")
    return HistoricalEvidence(
        payload["project_id"], evidence_type, status, count, payload["baseline_id"],
        payload["coverage_before_baseline"], payload["coverage_start"],
        payload["coverage_end"], payload["definition_version"], refs, hashes,
        payload["policy_hash"], payload["constitution_id"], generation,
        payload["generated_at"], payload["provenance"], payload["coverage_complete"],
        payload["completeness_basis"], payload["evidence_hash"],
    )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("historical timestamp must be UTC")
    return parsed.astimezone(UTC)


def verify_current_bindings(
    evidence: HistoricalEvidence,
    *,
    expected_project_id: str | None = None,
) -> None:
    """Reject evidence bound to another project, policy, or authority generation."""
    if expected_project_id is not None and evidence.project_id != expected_project_id:
        raise HistoricalEvidenceError("historical project binding is stale")
    resolved = resolve_authority(evidence.project_id)
    if resolved.constitution is None or resolved.policy is None or resolved.snapshot is None:
        raise HistoricalEvidenceError("historical authority binding is unavailable")
    if evidence.constitution_id != resolved.constitution.constitution_id:
        raise HistoricalEvidenceError("historical Constitution binding is stale")
    if evidence.policy_hash != resolved.policy.policy_hash:
        raise HistoricalEvidenceError("historical policy binding is stale")
    if evidence.authority_generation != int(resolved.snapshot["generation"]):
        raise HistoricalEvidenceError("historical authority generation is stale")


def verify_current_baseline_bindings(baseline: HistoricalBaseline) -> None:
    """Verify owner baseline identity against the active authority snapshot."""
    resolved = resolve_authority(baseline.project_id)
    if resolved.constitution is None or resolved.policy is None or resolved.snapshot is None:
        raise HistoricalEvidenceError("historical baseline authority is unavailable")
    if baseline.constitution_id != resolved.constitution.constitution_id:
        raise HistoricalEvidenceError("historical baseline Constitution binding is stale")
    if baseline.policy_hash != resolved.policy.policy_hash:
        raise HistoricalEvidenceError("historical baseline policy binding is stale")
    if baseline.authority_generation != int(resolved.snapshot["generation"]):
        raise HistoricalEvidenceError("historical baseline generation is stale")
