"""Provider-backed architecture planning with deterministic validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .capability_profiles import CapabilityProfileError, profile_from_dict
from .capability_selection import (
    CapabilityCandidate,
    CapabilitySelectionError,
    CapabilitySelector,
    SelectionGates,
    SelectionResult,
)
from .models import RepositoryContext
from .remote_identity import RemoteIdentityError, canonical_remote_identity
from .target_assessment import AssessmentError, TargetAssessment, derive_architecture
from .validation_commands import validate_commands


class ArchitectPlanningError(ValueError):
    """Raised when provider-backed architecture evidence is unusable."""


class ProviderInvocationError(RuntimeError):
    """Raised by an adapter for an expected provider transport failure."""


ARCHITECT_REQUIRED_CAPABILITIES = (
    "repository-understanding", "structured-output", "reasoning", "context-capacity",
)


_SECRET = re.compile(
    r"(?is)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"-----BEGIN [^-]+-----|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
)
_NON_BLANK = {"type": "string", "pattern": r".*\S.*"}
REQUIRED_RESPONSE_FIELDS = {
    "assessment_summary", "proposed_outcome", "rationale", "confidence", "proposed_tasks",
    "architecture_implications", "preliminary_risk_indicators", "evidence_references",
    "unresolved_unknowns",
}
REQUIRED_TASK_FIELDS = {
    "objective", "justification", "dependencies", "allowed_paths", "prohibited_paths",
    "acceptance_criteria", "validation_requirements", "evidence_references", "risk_level",
}


def architect_response_schema() -> dict[str, Any]:
    """Return the one provider-facing schema for the validated response model."""
    task_properties = {
        "objective": _NON_BLANK,
        "justification": _NON_BLANK,
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "allowed_paths": {"type": "array", "items": {"type": "string"}},
        "prohibited_paths": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {
            "type": "array", "items": _NON_BLANK, "minItems": 1,
        },
        "validation_requirements": {
            "type": "array", "items": _NON_BLANK, "minItems": 1,
        },
        "evidence_references": {"type": "array", "items": {"type": "string"}},
        "risk_level": {"enum": ["low", "medium", "high", "critical"]},
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(REQUIRED_RESPONSE_FIELDS),
        "properties": {
            "assessment_summary": _NON_BLANK,
            "proposed_outcome": {"enum": ["BOUNDED_IMPLEMENTATION", "NO_JUSTIFIED_WORK"]},
            "rationale": _NON_BLANK,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "proposed_tasks": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": sorted(REQUIRED_TASK_FIELDS), "properties": task_properties,
                },
            },
            "architecture_implications": {"type": "array", "items": {"type": "string"}},
            "preliminary_risk_indicators": {"type": "array", "items": {"type": "string"}},
            "evidence_references": {"type": "array", "items": {"type": "string"}},
            "unresolved_unknowns": {"type": "array", "items": {"type": "string"}},
        },
    }
    if set(schema["properties"]) != REQUIRED_RESPONSE_FIELDS:
        raise RuntimeError("Architect response schema fields drifted from validator")
    task_schema = schema["properties"]["proposed_tasks"]["items"]
    if set(task_schema["properties"]) != REQUIRED_TASK_FIELDS:
        raise RuntimeError("Architect task schema fields drifted from validator")
    return schema


@dataclass(frozen=True)
class ArchitectRequest:
    objective: str
    repository: RepositoryContext
    assessment: TargetAssessment
    constitution_constraints: tuple[str, ...]
    protected_paths: tuple[str, ...]
    request_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "repository": {
                "root": self.repository.root,
                "branch": self.repository.branch,
                "origin": self.repository.origin,
                "clean": self.repository.clean,
                "head_sha": self.repository.head_sha,
            },
            "assessment": self.assessment.to_dict(),
            "constitution_constraints": list(self.constitution_constraints),
            "protected_paths": list(self.protected_paths),
            "request_hash": self.request_hash,
        }

def architect_request_hash(payload: dict[str, Any]) -> str:
    value = dict(payload)
    value.pop("request_hash", None)
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def architect_response_hash(raw: str | dict[str, Any]) -> str:
    """Hash the decoded response canonically, independent of formatting."""
    payload = json.loads(raw) if isinstance(raw, str) else raw
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _decision_view(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in selection.items()
        if key not in {"provider_evidence_hash", "architect_request_hash"}
    }


def provider_evidence_payload(
    architect: "ProviderArchitect", request: ArchitectRequest, *, session_id: str,
    plan_path: str | None, plan_hash: str, target_sha: str,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return auditable observation evidence for a provider decision."""
    selection = selection or architect.provider_selection
    candidates = selection.get("profile_evidence", [])
    inputs = {
        "project_id": request.assessment.project_id,
        "session_id": session_id,
        "plan_path": plan_path,
        "plan_hash": plan_hash,
        "architecture_stage": "TARGET_ASSESSMENT_ARCHITECTURE",
        "target_sha": target_sha,
        "request_hash": request.request_hash,
        "required_capabilities": list(ARCHITECT_REQUIRED_CAPABILITIES),
        "gate_results": selection.get("gate_results"),
        "candidates": candidates,
    }
    return {
        "schema_version": "1.0",
        "source": "adapter",
        "evidence_kind": "observation",
        "attestation": "unavailable",
        "inputs": inputs,
        "attempts": list(architect.attempts),
        "selection_audit": selection.get("selection_audit", []),
        "decision_hash": _canonical_hash(_decision_view(selection)),
    }


def verify_provider_evidence(
    evidence: dict[str, Any], selection: dict[str, Any], *, request: ArchitectRequest,
    session_id: str, plan_path: str | None, plan_hash: str, target_sha: str, now: str,
    authoritative_candidates: tuple[CapabilityCandidate, ...],
    authoritative_gates: SelectionGates,
) -> None:
    """Recompute provider history from durable inputs, never from decision text."""
    if not isinstance(evidence, dict) or evidence.get("schema_version") != "1.0":
        raise ArchitectPlanningError("provider evidence schema is invalid")
    if (
        evidence.get("source") != "adapter"
        or evidence.get("evidence_kind") != "observation"
        or evidence.get("attestation") != "unavailable"
    ):
        raise ArchitectPlanningError("provider evidence trust boundary is invalid")
    inputs = evidence.get("inputs")
    if not isinstance(inputs, dict):
        raise ArchitectPlanningError("provider evidence inputs are missing")
    expected = {
        "project_id": request.assessment.project_id,
        "session_id": session_id,
        "plan_path": plan_path,
        "plan_hash": plan_hash,
        "architecture_stage": "TARGET_ASSESSMENT_ARCHITECTURE",
        "target_sha": target_sha,
        "request_hash": request.request_hash,
        "required_capabilities": list(ARCHITECT_REQUIRED_CAPABILITIES),
        "gate_results": selection.get("gate_results"),
        "candidates": selection.get("profile_evidence", []),
    }
    if inputs != expected:
        raise ArchitectPlanningError("provider evidence inputs differ from authority")
    attempts = evidence.get("attempts")
    audit = evidence.get("selection_audit")
    if not isinstance(attempts, list) or not isinstance(audit, list):
        raise ArchitectPlanningError("provider evidence attempt history is invalid")
    valid_outcomes = {
        "PROVIDER_UNAVAILABLE", "TRANSPORT_FAILURE", "INVALID_PROVIDER_OUTPUT",
    }
    for item in audit:
        if (
            not isinstance(item, dict)
            or item.get("type") != "invocation_failure"
            or not isinstance(item.get("attempt_id"), str)
            or not item["attempt_id"].strip()
            or not isinstance(item.get("provider_id"), str)
            or not item["provider_id"].strip()
            or not isinstance(item.get("profile_id"), str)
            or not item["profile_id"].strip()
            or item.get("outcome") not in valid_outcomes
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
        ):
            raise ArchitectPlanningError("provider evidence audit schema is invalid")
    if audit != selection.get("selection_audit", []):
        raise ArchitectPlanningError("provider evidence audit differs from decision")
    if evidence.get("decision_hash") != _canonical_hash(_decision_view(selection)):
        raise ArchitectPlanningError("provider evidence decision hash differs")
    candidate_by_id = {
        item.profile.provider_id: item for item in authoritative_candidates
    }
    attempts_by_id = {
        attempt.get("attempt_id"): attempt for attempt in attempts
        if isinstance(attempt, dict)
    }
    if any(
        not isinstance(attempt.get("attempt_id"), str)
        or not attempt["attempt_id"].strip()
        for attempt in attempts
        if isinstance(attempt, dict)
    ) or len(attempts_by_id) != len(attempts):
        raise ArchitectPlanningError("provider attempt IDs are invalid")
    for audit_item in audit:
        if (
            not isinstance(audit_item.get("attempt_id"), str)
            or not audit_item["attempt_id"].strip()
        ):
            raise ArchitectPlanningError("provider audit attempt ID is invalid")
        attempt = attempts_by_id.get(audit_item.get("attempt_id"))
        if attempt is None:
            raise ArchitectPlanningError("provider audit attempt binding is missing")
        if (
            attempt.get("provider_id") != audit_item.get("provider_id")
            or attempt.get("profile_id") != audit_item.get("profile_id")
            or attempt.get("outcome") != audit_item.get("outcome")
            or attempt.get("outcome") not in {
                "PROVIDER_UNAVAILABLE", "TRANSPORT_FAILURE", "INVALID_PROVIDER_OUTPUT",
            }
        ):
            raise ArchitectPlanningError("provider audit outcome binding differs")
        expected_failure = (
            "provider unavailable"
            if attempt["outcome"] == "PROVIDER_UNAVAILABLE"
            else attempt.get("failure")
        )
        if audit_item.get("reason", "").strip() != expected_failure:
            raise ArchitectPlanningError("provider audit failure classification differs")
    remaining = list(authoritative_candidates)
    for sequence, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict) or attempt.get("sequence") != sequence:
            raise ArchitectPlanningError("provider attempt sequence is invalid")
        if sequence > 1 and not authoritative_gates.allow_fallback:
            raise ArchitectPlanningError("historical provider fallback is not permitted")
        provider_id = attempt.get("provider_id")
        candidate = candidate_by_id.get(provider_id)
        if candidate is None or attempt.get("profile_id") != candidate.profile.profile_id:
            raise ArchitectPlanningError("provider attempt identity is invalid")
        expected_attempt_id = _canonical_hash({
            "request_hash": request.request_hash,
            "sequence": sequence,
            "provider_id": provider_id,
            "profile_id": attempt.get("profile_id"),
        })
        if attempt.get("attempt_id") != expected_attempt_id:
            raise ArchitectPlanningError("provider attempt ID derivation differs")
        if attempt.get("profile_sha256") != candidate.profile.profile_sha256:
            raise ArchitectPlanningError("provider attempt profile evidence differs")
        if attempt.get("request_hash") != request.request_hash:
            raise ArchitectPlanningError("provider attempt request binding differs")
        if attempt.get("requirements_hash") != _canonical_hash(
            list(ARCHITECT_REQUIRED_CAPABILITIES)
        ):
            raise ArchitectPlanningError("provider attempt requirements differ")
        selected = CapabilitySelector().select(
            tuple(remaining), project_id=request.assessment.project_id,
            required_capabilities=ARCHITECT_REQUIRED_CAPABILITIES,
            now=attempt.get("selection_time", now),
            gates=authoritative_gates,
        )
        if selected.provider_id != provider_id:
            raise ArchitectPlanningError("provider attempt order differs from authority")
        remaining = [item for item in remaining if item.profile.provider_id != provider_id]
        if attempt.get("outcome") == "VALIDATED_RESPONSE":
            if sequence != len(attempts) or selection.get("provider_id") != provider_id:
                raise ArchitectPlanningError("provider response outcome is inconsistent")
        elif attempt.get("outcome") not in {
            "PROVIDER_UNAVAILABLE", "TRANSPORT_FAILURE", "INVALID_PROVIDER_OUTPUT",
        }:
            raise ArchitectPlanningError("provider attempt outcome is invalid")
    attempt_ids = [attempt.get("attempt_id") for attempt in attempts]
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ArchitectPlanningError("provider attempt IDs are duplicated")
    audit_ids = [item.get("attempt_id") for item in audit]
    if len(set(audit_ids)) != len(audit_ids):
        raise ArchitectPlanningError("provider audit attempt IDs are duplicated")
    failure_attempt_ids = {
        attempt["attempt_id"] for attempt in attempts
        if attempt.get("outcome") != "VALIDATED_RESPONSE"
    }
    if set(audit_ids) != failure_attempt_ids:
        raise ArchitectPlanningError("provider attempt/audit cardinality differs")
    remaining = list(authoritative_candidates)
    terminal = None
    for attempt in attempts:
        terminal = CapabilitySelector().select(
            tuple(remaining), project_id=request.assessment.project_id,
            required_capabilities=ARCHITECT_REQUIRED_CAPABILITIES,
            now=attempt.get("selection_time", now), gates=authoritative_gates,
        )
        remaining = [
            item for item in remaining
            if item.profile.provider_id != attempt["provider_id"]
        ]
    if selection.get("status") == "BLOCKED" and attempts and remaining:
        raise ArchitectPlanningError("blocked provider evidence is not exhausted")
    if terminal is not None and attempts[-1].get("outcome") == "VALIDATED_RESPONSE":
        reconstructed = {
            "status": "SELECTED",
            "provider_id": terminal.provider_id,
            "profile_id": terminal.profile_id,
            "fallback_used": bool(failure_attempt_ids) or terminal.fallback_used,
            "considered": [item["provider_id"] for item in audit]
            + list(terminal.considered_candidates),
            "rejected_reasons": list(terminal.rejected_reasons),
            "invocation_failures": [
                f"{item['provider_id']}: {item['reason']}" for item in audit
            ],
            "selection_audit": audit,
            "reason": None,
        }
    elif attempts:
        failures = [f"{item['provider_id']}: {item['reason']}" for item in audit]
        reason = "all architect providers failed: " + "; ".join(failures)
        reconstructed = {
            "status": "BLOCKED", "provider_id": None, "profile_id": None,
            "fallback_used": False,
            "considered": [
                item.profile.provider_id for item in sorted(
                    authoritative_candidates,
                    key=lambda item: (
                        item.priority, item.profile.provider_id, item.profile.profile_id
                    ),
                )
            ],
            "rejected_reasons": [reason], "reason": reason,
            "invocation_failures": failures, "selection_audit": audit,
        }
    else:
        try:
            terminal = CapabilitySelector().select(
                tuple(authoritative_candidates), project_id=request.assessment.project_id,
                required_capabilities=ARCHITECT_REQUIRED_CAPABILITIES,
                now=now, gates=authoritative_gates,
            )
        except CapabilitySelectionError as exc:
            reason = str(exc)
            reconstructed = {
                "status": "BLOCKED", "provider_id": None, "profile_id": None,
                "fallback_used": False,
                "considered": [
                    item.profile.provider_id for item in sorted(
                        authoritative_candidates,
                        key=lambda item: (
                            item.priority, item.profile.provider_id, item.profile.profile_id
                        ),
                    )
                ],
                "rejected_reasons": [reason], "reason": reason,
                "invocation_failures": [], "selection_audit": [],
            }
        else:
            reconstructed = {
                "status": "SELECTED", "provider_id": terminal.provider_id,
                "profile_id": terminal.profile_id, "fallback_used": terminal.fallback_used,
                "considered": list(terminal.considered_candidates),
                "rejected_reasons": list(terminal.rejected_reasons),
                "invocation_failures": [], "selection_audit": [], "reason": None,
            }
    for key in (
        "status", "provider_id", "profile_id", "fallback_used", "considered",
        "rejected_reasons", "invocation_failures", "selection_audit", "reason",
    ):
        actual = selection.get(key)
        expected_value = reconstructed[key]
        if key in {"considered", "rejected_reasons", "invocation_failures", "selection_audit"}:
            actual = list(actual or ())
            expected_value = list(expected_value or ())
        if actual != expected_value:
            raise ArchitectPlanningError("provider decision does not reconstruct from evidence")


def validate_provider_selection_evidence(
    selection: dict[str, Any], *, project_id: str, now: str,
    authoritative_candidates: tuple[CapabilityCandidate, ...] | None = None,
    authoritative_gates: SelectionGates | None = None,
) -> None:
    """Validate durable, non-secret provider selection evidence."""
    if not isinstance(selection, dict) or selection.get("status") not in {"SELECTED", "BLOCKED"}:
        raise ArchitectPlanningError("provider selection evidence status is invalid")
    if not isinstance(selection.get("architect_request_hash"), str):
        raise ArchitectPlanningError("provider selection request evidence is missing")
    if selection.get("project_id") != project_id:
        raise ArchitectPlanningError("provider selection project binding differs")
    if selection["status"] == "BLOCKED":
        if not isinstance(selection.get("reason"), str) or not selection["reason"].strip():
            raise ArchitectPlanningError("provider selection block reason is missing")
    required = (
        "provider_id", "profile_id", "considered", "rejected_reasons", "profile_evidence",
    )
    if any(key not in selection for key in required):
        raise ArchitectPlanningError("provider selection evidence is incomplete")
    if not isinstance(selection.get("fallback_used"), bool):
        raise ArchitectPlanningError("provider selection identity is invalid")
    if selection["status"] == "SELECTED" and (
        not isinstance(selection["provider_id"], str)
        or not isinstance(selection["profile_id"], str)
    ):
        raise ArchitectPlanningError("provider selection identity is invalid")
    required_capabilities = selection.get("required_capabilities")
    if tuple(required_capabilities or ()) != ARCHITECT_REQUIRED_CAPABILITIES:
        raise ArchitectPlanningError("provider capability requirements are invalid")
    if not all(isinstance(item, str) for item in selection["considered"]):
        raise ArchitectPlanningError("provider considered evidence is invalid")
    if not all(isinstance(item, str) for item in selection["rejected_reasons"]):
        raise ArchitectPlanningError("provider rejection evidence is invalid")
    if not isinstance(selection["profile_evidence"], list):
        raise ArchitectPlanningError("provider profile evidence is invalid")
    audit = selection.get("selection_audit")
    if not isinstance(audit, list) or any(
        not isinstance(item, dict)
        or item.get("type") != "invocation_failure"
        or not isinstance(item.get("provider_id"), str)
        or not isinstance(item.get("profile_id"), str)
        or not isinstance(item.get("reason"), str)
        or not item["reason"].strip()
        for item in audit
    ):
        raise ArchitectPlanningError("provider selection audit is invalid")
    profiles = []
    for item in selection["profile_evidence"]:
        if not isinstance(item, dict) or "profile" not in item:
            raise ArchitectPlanningError("provider profile evidence is incomplete")
        if (
            not isinstance(item.get("priority"), int)
            or isinstance(item.get("priority"), bool)
            or item["priority"] < 0
            or not isinstance(item.get("diagnostic_only"), bool)
        ):
            raise ArchitectPlanningError("provider candidate evidence is invalid")
        try:
            profile = profile_from_dict(item["profile"])
            profile.validate_binding(project_id, profile.provider_id)
            profile.validate_at(now)
        except (CapabilityProfileError, TypeError) as exc:
            raise ArchitectPlanningError("provider profile evidence is invalid") from exc
        profiles.append(profile)
    selected = next(
        (profile for profile in profiles if profile.provider_id == selection["provider_id"]
         and profile.profile_id == selection["profile_id"]),
        None,
    )
    if selection["status"] == "SELECTED" and selected is None:
        raise ArchitectPlanningError("selected provider profile is not evidenced")
    gates = selection.get("gate_results")
    gate_keys = {
        "policy_eligible", "privacy_eligible", "independence_eligible", "budget_eligible",
        "health_eligible", "empirical_evidence_eligible", "allow_fallback",
    }
    if gates is None or (not isinstance(gates, dict) or any(
        key not in gates for key in (
            "policy_eligible", "privacy_eligible", "independence_eligible",
            "budget_eligible", "health_eligible", "empirical_evidence_eligible",
        )
    )):
        raise ArchitectPlanningError("provider gate evidence is incomplete")
    if set(gates) != gate_keys:
        raise ArchitectPlanningError("provider gate evidence schema is invalid")
    if gates is not None and any(not isinstance(value, bool) for value in gates.values()):
        raise ArchitectPlanningError("provider gate evidence is invalid")
    persisted_candidates = tuple(
        CapabilityCandidate(
            profile_from_dict(item["profile"]), item["priority"], item["diagnostic_only"]
        )
        for item in selection["profile_evidence"]
    )
    if authoritative_candidates is None:
        raise ArchitectPlanningError("authoritative provider candidates are required for recovery")
    if authoritative_gates is None:
        raise ArchitectPlanningError("authoritative provider gates are required for recovery")
    candidates = authoritative_candidates or persisted_candidates
    if authoritative_candidates is None and candidates:
        raise ArchitectPlanningError("authoritative provider candidates are required for recovery")
    if authoritative_candidates is not None:
        persisted_identity = [
            (candidate.profile.provider_id, candidate.profile.profile_id,
             candidate.profile.profile_sha256, candidate.priority, candidate.diagnostic_only)
            for candidate in persisted_candidates
        ]
        authoritative_identity = [
            (candidate.profile.provider_id, candidate.profile.profile_id,
             candidate.profile.profile_sha256, candidate.priority, candidate.diagnostic_only)
            for candidate in authoritative_candidates
        ]
        if persisted_identity != authoritative_identity:
            raise ArchitectPlanningError("provider candidate evidence differs from authority")
    if any(not isinstance(value, bool) for value in authoritative_gates.__dict__.values()):
        raise ArchitectPlanningError("authoritative provider gates are invalid")
    if gates != authoritative_gates.__dict__:
        raise ArchitectPlanningError("provider gate evidence differs from authority")
    audit_provider_ids = {item["provider_id"] for item in audit}
    if any(provider not in {candidate.profile.provider_id for candidate in candidates}
           for provider in audit_provider_ids):
        raise ArchitectPlanningError("provider fallback audit references unknown provider")
    persisted_failures = selection.get("invocation_failures", [])
    if not isinstance(persisted_failures, list) or any(
        not isinstance(item, str) or ":" not in item for item in persisted_failures
    ):
        raise ArchitectPlanningError("provider fallback failure evidence is invalid")
    authority_audit = []
    expected_failures = []
    if persisted_failures or audit:
        raise ArchitectPlanningError("historical provider observation requires RETRY_REQUIRED")
    actual_audit = authority_audit
    remaining = list(candidates)
    recomputed = None
    for provider_id, profile_id, _reason in actual_audit:
        try:
            attempt = CapabilitySelector().select(
                tuple(remaining), project_id=project_id,
                required_capabilities=required_capabilities, now=now,
                gates=authoritative_gates,
            )
        except (CapabilityProfileError, CapabilitySelectionError, TypeError) as exc:
            raise ArchitectPlanningError("provider fallback sequence is not deterministic") from exc
        if attempt.provider_id != provider_id or attempt.profile_id != profile_id:
            raise ArchitectPlanningError("provider fallback sequence differs from authority")
        remaining = [
            candidate for candidate in remaining
            if candidate.profile.provider_id != provider_id
        ]
        if not remaining and selection["status"] != "BLOCKED":
            raise ArchitectPlanningError("provider fallback has no remaining candidate")
    selection_error = None
    try:
        recomputed = CapabilitySelector().select(
            tuple(remaining), project_id=project_id,
            required_capabilities=required_capabilities, now=now,
            gates=authoritative_gates,
        )
    except (CapabilityProfileError, CapabilitySelectionError, TypeError) as exc:
        selection_error = exc
        if selection["status"] != "BLOCKED":
            raise ArchitectPlanningError("provider selection evidence is not eligible") from exc
    if selection["status"] == "BLOCKED":
        expected_considered = [
            candidate.profile.provider_id
            for candidate in sorted(candidates, key=lambda item: (
                item.priority, item.profile.provider_id, item.profile.profile_id
            ))
        ]
        if selection["considered"] != expected_considered or not selection["rejected_reasons"]:
            raise ArchitectPlanningError("blocked provider audit evidence differs")
        if selection["rejected_reasons"] != [selection["reason"]]:
            raise ArchitectPlanningError("blocked provider reason evidence differs")
        if remaining:
            if selection_error is None:
                raise ArchitectPlanningError(
                    "blocked provider selection did not recompute as blocked"
                )
        expected_reason = (
            str(selection_error)
            if remaining
            else "all architect providers failed: " + "; ".join(expected_failures)
        )
        if selection["reason"] != expected_reason:
            raise ArchitectPlanningError("blocked provider reason is not deterministic")
        return
    if (
        recomputed.provider_id != selection["provider_id"]
        or recomputed.profile_id != selection["profile_id"]
        or (recomputed.fallback_used or bool(audit_provider_ids)) != selection["fallback_used"]
    ):
        raise ArchitectPlanningError("provider selection evidence does not recompute")
    expected_considered = [
        item["provider_id"] for item in audit
    ] + list(recomputed.considered_candidates)
    if selection["considered"] != expected_considered:
        raise ArchitectPlanningError("provider considered evidence differs")
    if selection["rejected_reasons"] != list(recomputed.rejected_reasons):
        raise ArchitectPlanningError("provider rejection evidence differs")



class ArchitectProvider(Protocol):
    provider_id: str

    def propose(self, request: ArchitectRequest) -> str | dict[str, Any]:
        """Return one strict JSON object or an equivalent decoded mapping."""


def build_architect_request(
    objective: str,
    repository: RepositoryContext,
    assessment: TargetAssessment,
    *,
    registered_project: Any | None = None,
    constitution_constraints: tuple[str, ...] = (
        "preserve Constitution and protected policies",
        "fail closed on unknown scope or authority",
    ),
) -> ArchitectRequest:
    if registered_project is None:
        raise ArchitectPlanningError("registered project binding is required")
    if registered_project.project_id != assessment.project_id:
        raise ArchitectPlanningError("architect request project binding does not match")
    if str(Path(registered_project.repository_root).resolve()) != str(
        Path(repository.root).resolve()
    ):
        raise ArchitectPlanningError("architect request repository root does not match")
    try:
        if canonical_remote_identity(registered_project.origin_url) != canonical_remote_identity(
            repository.origin
        ):
            raise ArchitectPlanningError("architect request repository origin does not match")
    except RemoteIdentityError as exc:
        raise ArchitectPlanningError("architect request repository origin is invalid") from exc
    assessment.validate(repository)
    payload = {
        "objective": " ".join(objective.split()),
        "repository": {
            "root": repository.root,
            "branch": repository.branch,
            "origin": repository.origin,
            "clean": repository.clean,
            "head_sha": repository.head_sha,
        },
        "assessment": assessment.to_dict(),
        "constitution_constraints": constitution_constraints,
        "protected_paths": assessment.protected_paths,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if _SECRET.search(serialized):
        raise ArchitectPlanningError("architect request contains secret-like material")
    return ArchitectRequest(
        payload["objective"], repository, assessment, constitution_constraints,
        assessment.protected_paths, architect_request_hash(payload),
    )


class ProviderArchitect:
    """Select a verified provider, obtain advisory output, and validate it."""

    def __init__(
        self,
        candidates: tuple[CapabilityCandidate, ...],
        providers: dict[str, ArchitectProvider],
        *,
        now: str,
        project_id: str,
        gates: SelectionGates | None = None,
    ) -> None:
        self.candidates = candidates
        self.providers = providers
        self.now = now
        self.project_id = project_id
        self.gates = gates
        self.selection: SelectionResult | None = None
        self.response_hash: str | None = None
        self.last_response: str | dict[str, Any] | None = None
        self.invocation_failures: tuple[str, ...] = ()
        self.planning_outcome: str | None = None
        self.required_capabilities: tuple[str, ...] = ()
        self.selection_audit: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self.blocked_reason: str | None = None

    @property
    def provider_selection(self) -> dict[str, Any]:
        gate_results = {
            "policy_eligible": False,
            "privacy_eligible": False,
            "independence_eligible": False,
            "budget_eligible": False,
            "health_eligible": False,
            "empirical_evidence_eligible": False,
            "allow_fallback": True,
        }
        if self.gates is not None:
            gate_results.update({
                key: value if isinstance(value, bool) else False
                for key, value in self.gates.__dict__.items()
            })
        if self.selection is None:
            if self.blocked_reason is not None:
                return {
                    "status": "BLOCKED",
                    "project_id": self.project_id,
                    "considered": [
                        item.profile.provider_id for item in sorted(
                            self.candidates,
                            key=lambda candidate: (
                                candidate.priority,
                                candidate.profile.provider_id,
                                candidate.profile.profile_id,
                            ),
                        )
                    ],
                    "gate_results": gate_results,
                    "required_capabilities": list(self.required_capabilities),
                    "provider_id": None,
                    "profile_id": None,
                    "fallback_used": False,
                    "rejected_reasons": [self.blocked_reason],
                    "reason": self.blocked_reason,
                    "invocation_failures": list(self.invocation_failures),
                    "selection_audit": list(self.selection_audit),
                    "profile_evidence": [
                        {
                            "provider_id": candidate.profile.provider_id,
                            "profile_id": candidate.profile.profile_id,
                            "profile": candidate.profile.to_dict(),
                            "priority": candidate.priority,
                            "diagnostic_only": candidate.diagnostic_only,
                        }
                        for candidate in self.candidates
                    ],
                }
            return {
                "status": "PENDING",
                "project_id": self.project_id,
                "candidates": [item.profile.provider_id for item in self.candidates],
                "considered": [item.profile.provider_id for item in self.candidates],
                "gate_results": gate_results,
                "required_capabilities": list(self.required_capabilities),
                "provider_id": None,
                "profile_id": None,
                "fallback_used": False,
                "rejected_reasons": list(self.invocation_failures),
                "selection_audit": list(self.selection_audit),
                "profile_evidence": [
                    {
                        "provider_id": candidate.profile.provider_id,
                        "profile_id": candidate.profile.profile_id,
                        "profile": candidate.profile.to_dict(),
                        "priority": candidate.priority,
                        "diagnostic_only": candidate.diagnostic_only,
                    }
                    for candidate in self.candidates
                ],
            }
        return {
            "status": "SELECTED",
            "provider_id": self.selection.provider_id,
            "profile_id": self.selection.profile_id,
            "fallback_used": self.selection.fallback_used or bool(self.invocation_failures),
            "considered": tuple(
                [item["provider_id"] for item in self.selection_audit]
                + list(self.selection.considered_candidates)
            ),
            "rejected_reasons": self.selection.rejected_reasons,
            "invocation_failures": self.invocation_failures,
            "gate_results": gate_results,
            "project_id": self.project_id,
            "required_capabilities": list(self.required_capabilities),
            "profile_evidence": tuple(
                {
                    "provider_id": candidate.profile.provider_id,
                    "profile_id": candidate.profile.profile_id,
                    "profile": candidate.profile.to_dict(),
                    "priority": candidate.priority,
                    "diagnostic_only": candidate.diagnostic_only,
                }
                for candidate in self.candidates
            ),
            "response_hash": self.response_hash,
            "planning_outcome": self.planning_outcome,
            "selection_audit": list(self.selection_audit),
        }

    def propose(self, request: ArchitectRequest) -> dict[str, Any] | None:
        self.selection = None
        self.response_hash = None
        self.last_response = None
        self.invocation_failures = ()
        self.planning_outcome = None
        self.required_capabilities = ()
        self.selection_audit = []
        self.attempts = []
        self.blocked_reason = None
        if request.assessment.project_id != self.project_id:
            raise ArchitectPlanningError("architect project binding does not match")
        required = ARCHITECT_REQUIRED_CAPABILITIES
        self.required_capabilities = required
        if self.gates is None:
            raise ArchitectPlanningError("architect selection gates are missing")
        remaining = list(self.candidates)
        failures: list[str] = []
        while remaining:
            if self.attempts and not self.gates.allow_fallback:
                failures.append("fallback is not permitted")
                break
            try:
                self.selection = CapabilitySelector().select(
                    remaining, project_id=self.project_id, required_capabilities=required,
                    now=self.now,
                    gates=self.gates,
                )
            except (CapabilitySelectionError, TypeError) as exc:
                failures.append(str(exc))
                break
            provider = self.providers.get(self.selection.provider_id)
            selected = next(
                item for item in remaining
                if item.profile.provider_id == self.selection.provider_id
            )
            attempt = {
                "attempt_id": _canonical_hash({
                    "request_hash": request.request_hash,
                    "sequence": len(self.attempts) + 1,
                    "provider_id": selected.profile.provider_id,
                    "profile_id": selected.profile.profile_id,
                }),
                "sequence": len(self.attempts) + 1,
                "provider_id": selected.profile.provider_id,
                "profile_id": selected.profile.profile_id,
                "profile_sha256": selected.profile.profile_sha256,
                "request_hash": request.request_hash,
                "selection_time": self.now,
                "requirements_hash": _canonical_hash(list(required)),
            }
            remaining = [item for item in remaining if item is not selected]
            if provider is None:
                attempt["outcome"] = "PROVIDER_UNAVAILABLE"
                self.attempts.append(attempt)
                failures.append(f"{self.selection.provider_id}: provider unavailable")
                self.selection_audit.append({
                    "type": "invocation_failure",
                    "attempt_id": attempt["attempt_id"],
                    "provider_id": self.selection.provider_id,
                    "profile_id": self.selection.profile_id,
                    "outcome": attempt["outcome"],
                    "reason": "provider unavailable",
                })
                continue
            try:
                raw = provider.propose(request)
                response_payload = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(response_payload, dict):
                    self.planning_outcome = response_payload.get("proposed_outcome")
                result = validate_architect_response(raw, request)
            except (ProviderInvocationError, ArchitectPlanningError, json.JSONDecodeError) as exc:
                attempt["outcome"] = (
                    "TRANSPORT_FAILURE"
                    if isinstance(exc, ProviderInvocationError)
                    else "INVALID_PROVIDER_OUTPUT"
                )
                attempt["failure"] = str(exc)
                self.attempts.append(attempt)
                failures.append(f"{self.selection.provider_id}: {exc}")
                self.selection_audit.append({
                    "type": "invocation_failure",
                    "attempt_id": attempt["attempt_id"],
                    "provider_id": self.selection.provider_id,
                    "profile_id": self.selection.profile_id,
                    "outcome": attempt["outcome"],
                    "reason": str(exc),
                })
                continue
            self.response_hash = architect_response_hash(raw)
            attempt["outcome"] = "VALIDATED_RESPONSE"
            attempt["response_hash"] = self.response_hash
            self.attempts.append(attempt)
            self.last_response = raw
            self.invocation_failures = tuple(failures)
            return result
        self.invocation_failures = tuple(failures if self.attempts else ())
        self.selection = None
        self.blocked_reason = (
            "all architect providers failed: " + "; ".join(failures)
            if self.attempts else (failures[0] if failures else "provider selection blocked")
        )
        raise ArchitectPlanningError(self.blocked_reason)


def validate_architect_response(
    raw: str | dict[str, Any], request: ArchitectRequest
) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArchitectPlanningError("architect response is not valid JSON") from exc
    else:
        payload = raw
    if not isinstance(payload, dict) or set(payload) != REQUIRED_RESPONSE_FIELDS:
        raise ArchitectPlanningError("architect response schema is invalid")
    if _SECRET.search(json.dumps(payload, ensure_ascii=False)):
        raise ArchitectPlanningError("architect response contains secret-like material")
    if (
        isinstance(payload["confidence"], bool)
        or not isinstance(payload["confidence"], (int, float))
        or not 0 <= payload["confidence"] <= 1
    ):
        raise ArchitectPlanningError("architect confidence is invalid")
    for field in ("assessment_summary", "rationale", "proposed_outcome"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ArchitectPlanningError(f"architect field {field} is invalid")
    list_fields = (
        "architecture_implications", "preliminary_risk_indicators", "evidence_references",
        "unresolved_unknowns",
    )
    for field in list_fields:
        if not isinstance(payload[field], list) or any(
            not isinstance(item, str) for item in payload[field]
        ):
            raise ArchitectPlanningError(f"architect list field {field} is invalid")
    if not isinstance(payload["proposed_tasks"], list):
        raise ArchitectPlanningError("architect proposed_tasks list is invalid")
    if any(
        ref not in request.assessment.repository_structure
        and ref != request.assessment.evidence_hash
        for ref in payload["evidence_references"]
    ):
        raise ArchitectPlanningError("architect cites unknown top-level evidence")
    if payload["proposed_outcome"] == "NO_JUSTIFIED_WORK":
        if payload["proposed_tasks"]:
            raise ArchitectPlanningError("no-work decision must contain no tasks")
        if not payload["rationale"] or not payload["evidence_references"]:
            raise ArchitectPlanningError("no-work decision lacks rationale or evidence")
        return None
    if payload["proposed_outcome"] != "BOUNDED_IMPLEMENTATION":
        raise ArchitectPlanningError("architect outcome is unknown")
    if not isinstance(payload["proposed_tasks"], list) or not payload["proposed_tasks"]:
        raise ArchitectPlanningError("bounded implementation needs proposed tasks")
    tasks = []
    for index, task in enumerate(payload["proposed_tasks"], start=1):
        if not isinstance(task, dict) or set(task) != REQUIRED_TASK_FIELDS:
            raise ArchitectPlanningError(f"architect task {index} schema is invalid")
        list_fields = REQUIRED_TASK_FIELDS - {"objective", "justification", "risk_level"}
        if not all(
            isinstance(task[key], list)
            and all(isinstance(item, str) for item in task[key])
            for key in list_fields
        ):
            raise ArchitectPlanningError(f"architect task {index} list fields are invalid")
        if not task["acceptance_criteria"] or not task["validation_requirements"]:
            raise ArchitectPlanningError(f"architect task {index} requires validation criteria")
        try:
            validate_commands(task["validation_requirements"], request.repository.root)
        except ValueError as exc:
            raise ArchitectPlanningError(str(exc)) from exc
        if (
            not isinstance(task["objective"], str)
            or not isinstance(task["justification"], str)
            or not task["objective"].strip()
            or not task["justification"].strip()
        ):
            raise ArchitectPlanningError(f"architect task {index} objective is invalid")
        if task["risk_level"] not in {"low", "medium", "high", "critical"}:
            raise ArchitectPlanningError(f"architect task {index} risk is invalid")
        refs = task["evidence_references"]
        if any(
            ref not in request.assessment.repository_structure
            and ref != request.assessment.evidence_hash
            for ref in refs
        ):
            raise ArchitectPlanningError(f"architect task {index} cites unknown evidence")
        tasks.append({
            "task_id": f"task-{index:03d}",
            "title": task["objective"][:120],
            "objective": task["objective"],
            "allowed_paths": task["allowed_paths"],
            "dependencies": task["dependencies"],
            "acceptance_criteria": task["acceptance_criteria"],
            "validation_commands": task["validation_requirements"],
            "risk_level": task["risk_level"],
            "assigned_role": "Implementer",
            "requirement_refs": [],
        })
    task_ids = {item["task_id"] for item in tasks}
    for item in tasks:
        if item["task_id"] in item["dependencies"] or any(
            dependency not in task_ids for dependency in item["dependencies"]
        ):
            raise ArchitectPlanningError("architect task dependency graph is invalid")
    graph = {item["task_id"]: set(item["dependencies"]) for item in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ArchitectPlanningError("architect task dependency graph contains a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)
    proposal = {
        "rationale": payload["rationale"],
        "tasks": tasks,
        "prohibited_paths": sorted({
            path for task in payload["proposed_tasks"] for path in task["prohibited_paths"]
        }),
        "required_evidence": list(payload["evidence_references"]),
        "validation_requirements": sorted({
            command
            for task in payload["proposed_tasks"]
            for command in task["validation_requirements"]
        }),
        "risk_indicators": payload["preliminary_risk_indicators"],
    }
    try:
        derive_architecture(
            request.objective, request.repository, request.assessment, proposal=proposal
        )
    except (AssessmentError, ValueError) as exc:
        raise ArchitectPlanningError(str(exc)) from exc
    return proposal
