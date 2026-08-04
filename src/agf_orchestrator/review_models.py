"""Typed review, compliance, and delivery records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ReviewStatus(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class FindingResolution(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    UNCHANGED = "UNCHANGED"
    REPLACED = "REPLACED"
    NEW = "NEW"
    UNVERIFIABLE = "UNVERIFIABLE"


class ComplianceStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    category: str
    severity: str
    message: str
    affected_paths: list[str]
    evidence: str
    required_change: str
    accepted: bool = True

    @property
    def code(self) -> str:
        """Backward-compatible alias for the structured finding identifier."""
        return self.finding_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def finding_identity(finding: ReviewFinding) -> str:
    """Return a stable identity that is independent of mutable line numbers."""
    normalized = {
        "code": finding.finding_id.strip().casefold(),
        "paths": sorted(path.strip().casefold() for path in finding.affected_paths),
        "message": " ".join(finding.message.split()),
        "required_change": " ".join(finding.required_change.split()),
    }
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    code = re.sub(r"[^a-z0-9_.-]+", "-", normalized["code"]).strip("-") or "finding"
    return f"{code}:{digest}"


@dataclass(frozen=True)
class ReviewReport:
    reviewer: str
    status: ReviewStatus
    findings: list[ReviewFinding]
    evidence: list[str]
    blocking_issues: list[str]
    summary: str = ""
    checks_performed: list[str] | None = None
    residual_risks: list[str] | None = None
    resolved_finding_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComplianceReport:
    checker: str
    status: ComplianceStatus
    evidence: list[str]
    blocking_issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeliveryReport:
    delivery_id: str
    plan_id: str
    task_id: str
    repository: str
    base_sha: str
    delivery_branch: str
    patch_sha256: str
    execution_status: str
    review_status: str
    review_findings: list[dict[str, Any]]
    correction_rounds: int
    compliance_status: str
    changed_files: list[str]
    validation_results: list[str]
    commit_sha: str | None
    push_status: str
    pr_url: str | None
    status: str
    blocking_issues: list[str]
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
