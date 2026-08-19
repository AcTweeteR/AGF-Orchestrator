"""Fail-closed review-gate evidence independent of GitHub UI state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class ReviewGateError(ValueError):
    """Raised when independent review evidence cannot authorize a gate."""


@dataclass(frozen=True)
class IndependentReviewEvidence:
    project_id: str
    repository: str
    head_sha: str
    implementer: str
    reviewer: str
    decision: str
    findings: tuple[dict[str, Any], ...]
    evidence_id: str
    provenance: str
    issued_at: str


def _require_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ReviewGateError(f"review evidence field {name} is invalid")
    return value.strip()


def _valid_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    return True


def parse_independent_review(
    payload: dict[str, Any], *, expected_project_id: str, expected_repository: str,
    expected_head_sha: str, implementer: str, seen_evidence_ids: set[str] | None = None,
) -> IndependentReviewEvidence:
    """Validate immutable, head-bound review evidence.

    This validates an AGF evidence record; it never treats a GitHub COMMENTED
    review as approval and never creates a GitHub review state.
    """
    if payload.get("schema_version") != "1.0":
        raise ReviewGateError("review evidence schema is unsupported")
    project_id = _require_text(payload, "project_id")
    repository = _require_text(payload, "repository")
    head_sha = _require_text(payload, "head_sha")
    reviewer = _require_text(payload, "reviewer")
    decision = _require_text(payload, "decision")
    evidence_id = _require_text(payload, "evidence_id")
    provenance = _require_text(payload, "provenance")
    issued_at = _require_text(payload, "issued_at")
    if project_id != expected_project_id or repository != expected_repository:
        raise ReviewGateError("review evidence binding is incorrect")
    if head_sha != expected_head_sha:
        raise ReviewGateError("review evidence is stale")
    if implementer.strip() == reviewer:
        raise ReviewGateError("reviewer must be independent of implementer")
    if decision != "APPROVE":
        raise ReviewGateError("review decision is not APPROVE")
    if not _valid_timestamp(issued_at):
        raise ReviewGateError("review evidence timestamp is invalid")
    if seen_evidence_ids is not None and evidence_id in seen_evidence_ids:
        raise ReviewGateError("review evidence replay detected")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ReviewGateError("review findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise ReviewGateError("review finding is invalid")
        severity = finding.get("severity")
        status = finding.get("status", "OPEN")
        if severity in {"P1", "P2"} and status != "RESOLVED":
            raise ReviewGateError("unresolved P1/P2 finding blocks review")
    return IndependentReviewEvidence(
        project_id, repository, head_sha, implementer.strip(), reviewer, decision,
        tuple(findings), evidence_id, provenance, issued_at,
    )


def review_gate_ready(
    *, ci_passed: bool, github_state: str, github_approved: bool,
    independent_review: IndependentReviewEvidence | None,
    formal_github_approval_required: bool,
) -> bool:
    """Return readiness without weakening either review or CI requirements."""
    if not ci_passed or github_state not in {"OPEN", "MERGED"}:
        return False
    if github_state == "MERGED":
        return True
    if formal_github_approval_required:
        return github_approved
    return independent_review is not None
