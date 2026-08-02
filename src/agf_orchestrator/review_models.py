"""Typed review, compliance, and delivery records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ReviewStatus(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class ComplianceStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    severity: str
    message: str
    affected_paths: list[str]
    accepted: bool = True


@dataclass(frozen=True)
class ReviewReport:
    reviewer: str
    status: ReviewStatus
    findings: list[ReviewFinding]
    evidence: list[str]
    blocking_issues: list[str]

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
