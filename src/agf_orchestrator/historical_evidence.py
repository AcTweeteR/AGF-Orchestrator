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
from .project_registry import ProjectRegistry


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
    target_identity: str | None = None
    target_sha: str | None = None
    policy_id: str | None = None
    policy_generation: int | None = None
    constitution_record_hash: str | None = None
    owner_fingerprint: str | None = None
    evidence_definition_version: str | None = None
    source_set: tuple[str, ...] = ()
    source_hashes: tuple[str, ...] = ()
    baseline_generation: int | None = None
    predecessor_baseline_hash: str | None = None
    operation_id: str | None = None
    authoritative: bool = False


class EvidenceStatus(StrEnum):
    VERIFIED_ZERO = "VERIFIED_ZERO"
    VERIFIED_EVENTS = "VERIFIED_EVENTS"
    UNKNOWN = "UNKNOWN"


_PROJECT = re.compile(r"^project-[0-9a-f]{16}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TARGET_SHA = re.compile(r"^[0-9a-f]{40,64}$")
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
    evidence_generation: int
    predecessor_evidence_hash: str | None
    renewal_operation_id: str
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
    if evidence.evidence_generation > 1:
        history_dir = directory / "history"
        predecessor_path = directory / "history" / (
            f"{evidence_type}-{evidence.predecessor_evidence_hash}.json"
        )
        try:
            if history_dir.is_symlink() or predecessor_path.is_symlink():
                raise HistoricalEvidenceError("historical predecessor namespace uses symlinks")
            predecessor_doc = json.loads(predecessor_path.read_text(encoding="utf-8"))
            verify_envelope(predecessor_doc["payload"], predecessor_doc["envelope"])
            if (
                predecessor_doc["payload"].get("evidence_hash")
                != evidence.predecessor_evidence_hash
            ):
                raise HistoricalEvidenceError("historical predecessor hash is invalid")
            predecessor_payload = predecessor_doc["payload"]
            if (
                predecessor_payload.get("project_id") != project_id
                or predecessor_payload.get("evidence_type") != evidence_type
                or predecessor_payload.get("baseline_id") != evidence.baseline_id
                or predecessor_payload.get("policy_hash") != evidence.policy_hash
                or predecessor_payload.get("constitution_id") != evidence.constitution_id
                or predecessor_payload.get("authority_generation") != evidence.authority_generation
                or predecessor_payload.get("coverage_end") != evidence.coverage_start
            ):
                raise HistoricalEvidenceError("historical predecessor binding is invalid")
            if (
                predecessor_payload.get("evidence_generation", 1)
                != evidence.evidence_generation - 1
            ):
                raise HistoricalEvidenceError("historical evidence generation is not monotonic")
            journal_document = json.loads(
                (directory / "renewal-journal.json").read_text(encoding="utf-8")
            )
            verify_envelope(journal_document["payload"], journal_document["envelope"])
            journal = journal_document["payload"]
            if (
                journal.get("status") != "COMMITTED"
                or journal.get("operation_id") != evidence.renewal_operation_id
                or journal.get("project_id") != project_id
                or journal.get("baseline_id") != evidence.baseline_id
                or journal.get("evidence_type") not in {evidence_type, "rollback+incident"}
                or journal.get("evidence_generation") != evidence.evidence_generation
                or journal.get("policy_hash") != evidence.policy_hash
                or journal.get("constitution_id") != evidence.constitution_id
                or journal.get("authority_generation") != evidence.authority_generation
                or journal.get("predecessor_evidence_hashes", {}).get(evidence_type)
                != evidence.predecessor_evidence_hash
                or journal.get("evidence_hashes", {}).get(evidence_type)
                != evidence.evidence_hash
            ):
                raise HistoricalEvidenceError("historical renewal journal is invalid")
            if any(
                json.loads(item.read_text(encoding="utf-8")).get("payload", {}).get(
                    "renewal_operation_id"
                )
                == evidence.renewal_operation_id
                for item in (directory / "history").glob(f"{evidence_type}-*.json")
            ):
                raise HistoricalEvidenceError("historical renewal operation was replayed")
        except HistoricalEvidenceError:
            raise
        except (
            OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError
        ) as exc:
            raise HistoricalEvidenceError("historical predecessor evidence is unavailable") from exc
    try:
        start = _timestamp(evidence.coverage_start)
        end = _timestamp(evidence.coverage_end)
        generated = _timestamp(evidence.generated_at)
        now = datetime.now(UTC)
        if generated < start or end < start or end > now or generated > now:
            return None
        if now - generated > timedelta(seconds=max_age_seconds) or now - end > timedelta(
            seconds=max_age_seconds
        ):
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
    legacy_required = {
        "schema_version", "baseline_id", "project_id", "coverage_start",
        "policy_hash", "constitution_id", "authority_generation", "generated_at",
        "provenance",
    }
    extended_required = legacy_required | {
        "target_identity", "target_sha", "policy_id", "policy_generation",
        "constitution_record_hash", "owner_fingerprint", "evidence_definition_version",
        "source_set", "source_hashes", "baseline_generation", "predecessor_baseline_hash",
        "operation_id",
    }
    payload_keys = set(payload)
    if (
        payload_keys not in (legacy_required, extended_required)
        or payload["schema_version"] != "1.0"
    ):
        raise HistoricalEvidenceError("historical baseline schema is invalid")
    if payload["project_id"] != project_id or not _BASELINE.fullmatch(payload["baseline_id"]):
        raise HistoricalEvidenceError("historical baseline binding is invalid")
    if not _HEX.fullmatch(payload["policy_hash"]):
        raise HistoricalEvidenceError("historical baseline policy is invalid")
    if not isinstance(payload["authority_generation"], int) or payload["authority_generation"] < 1:
        raise HistoricalEvidenceError("historical baseline generation is invalid")
    if payload_keys == extended_required:
        if (
            not isinstance(payload["target_identity"], str)
            or not payload["target_identity"]
            or not isinstance(payload["target_sha"], str)
            or not _TARGET_SHA.fullmatch(payload["target_sha"])
            or not isinstance(payload["policy_id"], str)
            or not payload["policy_id"]
            or not isinstance(payload["policy_generation"], int)
            or payload["policy_generation"] < 1
            or not _HEX.fullmatch(payload["constitution_record_hash"])
            or payload["owner_fingerprint"]
            != "d23e23484571f256610658dd2b851ef3e4144dbd03827b8a66ee421c93ffe42a"
            or not isinstance(payload["evidence_definition_version"], str)
            or not payload["evidence_definition_version"]
            or not isinstance(payload["source_set"], list)
            or not payload["source_set"]
            or any(not isinstance(item, str) or not item for item in payload["source_set"])
            or not isinstance(payload["source_hashes"], list)
            or len(payload["source_hashes"])
            != len(payload["source_set"])
            or any(
                not isinstance(item, str) or not _HEX.fullmatch(item)
                for item in payload["source_hashes"]
            )
            or payload["baseline_generation"] != 1
            or payload["predecessor_baseline_hash"] is not None
            or not isinstance(payload["operation_id"], str)
            or not payload["operation_id"].startswith("historical-baseline-")
        ):
            raise HistoricalEvidenceError("historical baseline extended bindings are invalid")
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
        payload.get("target_identity"), payload.get("target_sha"), payload.get("policy_id"),
        payload.get("policy_generation"), payload.get("constitution_record_hash"),
        payload.get("owner_fingerprint"), payload.get("evidence_definition_version"),
        tuple(payload.get("source_set", ())), tuple(payload.get("source_hashes", ())),
        payload.get("baseline_generation"),
        payload.get("predecessor_baseline_hash"), payload.get("operation_id"),
        payload_keys == extended_required,
    )
    if payload_keys == extended_required:
        journal_path = directory / "baseline-journal.json"
        activation_path = directory / "baseline-activation.json"
        try:
            if journal_path.is_symlink():
                raise HistoricalEvidenceError("historical baseline journal must not use symlinks")
            if activation_path.is_symlink():
                raise HistoricalEvidenceError(
                    "historical baseline activation must not use symlinks"
                )
            journal_document = json.loads(journal_path.read_text(encoding="utf-8"))
            verify_envelope(journal_document["payload"], journal_document["envelope"])
            journal = journal_document["payload"]
            if (
                journal.get("status") != "COMMITTED"
                or journal.get("operation_id") != baseline.operation_id
                or journal.get("project_id") != project_id
                or journal.get("baseline_id") != baseline.baseline_id
                or journal.get("target_sha") != baseline.target_sha
                or tuple(journal.get("source_hashes", ())) != baseline.source_hashes
                or journal.get("baseline_hash") != hashlib.sha256(
                    json.dumps(
                        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest()
                or journal.get("baseline_generation") != baseline.baseline_generation
            ):
                raise HistoricalEvidenceError("historical baseline journal is invalid")
            activation_document = json.loads(activation_path.read_text(encoding="utf-8"))
            verify_envelope(activation_document["payload"], activation_document["envelope"])
            activation = activation_document["payload"]
            if (
                activation.get("status") != "COMMITTED"
                or activation.get("project_id") != project_id
                or activation.get("baseline_id") != baseline.baseline_id
                or activation.get("baseline_hash") != _object_hash(payload)
                or activation.get("journal_hash") != _object_hash(journal)
                or tuple(activation.get("source_hashes", ())) != baseline.source_hashes
            ):
                raise HistoricalEvidenceError("historical baseline activation is invalid")
            for source_ref, expected_hash in zip(baseline.source_set, baseline.source_hashes):
                prefix = f"ledger:{project_id}:"
                if not source_ref.startswith(prefix):
                    raise HistoricalEvidenceError("historical baseline source binding is invalid")
                source_path = directory / source_ref[len(prefix):]
                if source_path.is_symlink() or not source_path.is_file():
                    raise HistoricalEvidenceError("historical baseline source is unavailable")
                source_payload = json.loads(source_path.read_text(encoding="utf-8"))
                if _object_hash(source_payload) != expected_hash:
                    raise HistoricalEvidenceError("historical baseline source integrity is invalid")
                if (
                    source_payload.get("project_id") != project_id
                    or source_payload.get("baseline_id") != baseline.baseline_id
                    or source_payload.get("coverage_before_baseline") != "UNKNOWN"
                    or source_payload.get("coverage_status_from_baseline") != "AUTHORITATIVE"
                ):
                    raise HistoricalEvidenceError("historical baseline source binding is invalid")
        except HistoricalEvidenceError:
            raise
        except (
            OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError
        ) as exc:
            raise HistoricalEvidenceError("historical baseline journal is unavailable") from exc
    verify_current_baseline_bindings(baseline)
    return baseline


def _object_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _parse(payload: Any, evidence_type: str, *, expected_project_id: str) -> HistoricalEvidence:
    required = {
        "schema_version", "project_id", "evidence_type", "status", "count",
        "baseline_id", "coverage_before_baseline", "coverage_start", "coverage_end",
        "evidence_generation", "predecessor_evidence_hash", "renewal_operation_id",
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
    if not isinstance(payload["evidence_generation"], int) or payload["evidence_generation"] < 1:
        raise HistoricalEvidenceError("historical evidence generation is invalid")
    predecessor = payload["predecessor_evidence_hash"]
    if predecessor is not None and not _HEX.fullmatch(predecessor):
        raise HistoricalEvidenceError("historical predecessor binding is invalid")
    if payload["evidence_generation"] == 1 and predecessor is not None:
        raise HistoricalEvidenceError("initial historical evidence cannot have predecessor")
    if payload["evidence_generation"] > 1 and predecessor is None:
        raise HistoricalEvidenceError("renewed historical evidence requires predecessor")
    if (
        not isinstance(payload["renewal_operation_id"], str)
        or not payload["renewal_operation_id"].startswith("historical-renewal-")
    ):
        raise HistoricalEvidenceError("historical renewal operation is invalid")
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
        payload["coverage_before_baseline"], payload["evidence_generation"], predecessor,
        payload["renewal_operation_id"], payload["coverage_start"],
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
    if baseline.policy_id is not None and baseline.policy_id != resolved.policy.policy_id:
        raise HistoricalEvidenceError("historical baseline policy identity is stale")
    if baseline.policy_generation is not None and baseline.policy_generation != int(
        resolved.snapshot["generation"]
    ):
        raise HistoricalEvidenceError("historical baseline policy generation is stale")
    if baseline.constitution_record_hash is not None and baseline.constitution_record_hash != (
        resolved.constitution.record_hash
    ):
        raise HistoricalEvidenceError("historical baseline Constitution hash is stale")
    if baseline.owner_fingerprint is not None and baseline.owner_fingerprint != (
        "d23e23484571f256610658dd2b851ef3e4144dbd03827b8a66ee421c93ffe42a"
    ):
        raise HistoricalEvidenceError("historical baseline owner authority is stale")
    if baseline.target_identity is not None:
        project = ProjectRegistry().get(baseline.project_id)
        if (
            project.origin_url != baseline.target_identity
            or project.current_head_sha != baseline.target_sha
        ):
            raise HistoricalEvidenceError("historical baseline target identity is stale")
