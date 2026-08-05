"""Independent delivery compliance checks."""

from __future__ import annotations

import re

from .models import ExecutionPlan, Task
from .review_models import ComplianceReport, ComplianceStatus, ReviewReport, ReviewStatus
from .risk_engine import risk_evidence
from .risk_models import RiskAssessment, RiskValidationError

SECRET_SHAPED = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]")


class ComplianceChecker:
    name = "agf-compliance"

    def check(
        self,
        plan: ExecutionPlan,
        task: Task,
        review: ReviewReport,
        changed_files: list[str],
        validation_results: list[str],
        evidence: list[str],
        caller_clean: bool,
        base_sha: str,
        final_branch_files: list[str] | None = None,
        risk_assessment: RiskAssessment | None = None,
    ) -> ComplianceReport:
        blockers: list[str] = []
        checks: list[str] = []
        if plan.status.value != "READY":
            blockers.append("plan is not READY")
        else:
            checks.append("plan status READY")
        if task.status.value != "READY":
            blockers.append("task is not READY")
        else:
            checks.append("task status READY")
        if not set(changed_files).issubset(set(task.allowed_paths)):
            blockers.append("changed paths exceed allowed paths")
        else:
            checks.append("allowed paths conform")
        if review.status is not ReviewStatus.APPROVE:
            blockers.append("reviewer did not APPROVE")
        else:
            checks.append("reviewer APPROVE")
        if not validation_results or any("exit_code=0" not in item for item in validation_results):
            blockers.append("approved validations did not all pass")
        else:
            checks.append("validations passed")
        if not evidence:
            blockers.append("required evidence is missing")
        else:
            checks.append("evidence exists")
        checks.append(
            "objective traceability: objective_id="
            f"{plan.objective_id or 'UNSET'}; requirement_refs="
            f"{sorted(set(plan.requirement_refs or task.requirement_refs))}"
        )
        if not caller_clean:
            blockers.append("caller repository was not clean before delivery")
        else:
            checks.append("caller repository remained unchanged")
        if not base_sha or not plan.repository.head_sha:
            blockers.append("base SHA evidence is missing")
        if any(SECRET_SHAPED.search(item) for item in evidence):
            blockers.append("secret-shaped data found in evidence")
        if final_branch_files is not None and set(final_branch_files) != set(changed_files):
            blockers.append("final delivery branch paths differ from approved patch")
        if risk_assessment is not None:
            try:
                summary = risk_evidence(risk_assessment)
            except RiskValidationError:
                blockers.append("risk assessment is invalid")
            else:
                if summary not in evidence:
                    blockers.append("risk assessment evidence is missing")
                else:
                    checks.append("risk assessment evidence is present")
        status = ComplianceStatus.PASS if not blockers else ComplianceStatus.FAIL
        return ComplianceReport(self.name, status, checks, blockers)
