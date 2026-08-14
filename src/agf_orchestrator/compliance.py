"""Independent delivery compliance checks."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from .merge_models import MergeDecision, MergeValidationError
from .merge_policy import REQUIRED_GATES
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
        merge_decision: MergeDecision | None = None,
        expected_project_id: str | None = None,
        expected_task_id: str | None = None,
        expected_base_sha: str | None = None,
        expected_delivery_sha: str | None = None,
        expected_policy: object | None = None,
        expected_constitution_id: str | None = None,
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
        if merge_decision is not None:
            try:
                merge_decision.validate()
            except MergeValidationError:
                blockers.append("integrity-bound merge decision is invalid")
            else:
                if not merge_decision.verify_integrity():
                    blockers.append("integrity-bound merge decision failed verification")
                else:
                    checks.append("integrity-bound merge decision verified")
                if expected_project_id and merge_decision.project_id != expected_project_id:
                    blockers.append("merge decision project binding is invalid")
                if expected_task_id and merge_decision.task_id != expected_task_id:
                    blockers.append("merge decision task binding is invalid")
                if expected_base_sha and merge_decision.base_sha != expected_base_sha:
                    blockers.append("merge decision base binding is invalid")
                if expected_delivery_sha and merge_decision.delivery_sha != expected_delivery_sha:
                    blockers.append("merge decision delivery binding is invalid")
                if expected_policy is not None and (
                    merge_decision.policy_id != expected_policy.policy_id
                    or merge_decision.policy_version != expected_policy.version
                    or merge_decision.policy_hash != expected_policy.policy_hash
                    or merge_decision.authority_generation
                    != expected_policy.authority_generation
                    or merge_decision.kill_switch_active
                ):
                    blockers.append("merge decision active policy binding is invalid")
                if (
                    expected_constitution_id
                    and merge_decision.constitution_id != expected_constitution_id
                ):
                    blockers.append("merge decision Constitution binding is invalid")
                gate_names = {gate.name for gate in merge_decision.gates}
                if gate_names != set(REQUIRED_GATES):
                    blockers.append("merge decision mandatory gate set is incomplete")
                if any(gate.status.value != "PASS" for gate in merge_decision.gates):
                    blockers.append("merge decision gate evidence is incomplete")
                if expected_policy is not None:
                    now = datetime.now(UTC)
                    try:
                        expiry = datetime.fromisoformat(merge_decision.expiry)
                        policy_seconds = int(expected_policy.freshness_limits["policy_seconds"])
                        gate_seconds = int(expected_policy.freshness_limits["gate_seconds"])
                    except (KeyError, TypeError, ValueError):
                        blockers.append("merge decision freshness evidence is invalid")
                    else:
                        if (
                            expiry.tzinfo is None
                            or expiry <= now
                            or expiry - now > timedelta(seconds=policy_seconds)
                        ):
                            blockers.append("merge decision expiry is invalid")
                        for gate in merge_decision.gates:
                            try:
                                observed = datetime.fromisoformat(gate.observed_at)
                            except ValueError:
                                blockers.append(f"gate freshness is invalid: {gate.name}")
                                continue
                            if (
                                observed.tzinfo is None
                                or observed > now
                                or now - observed > timedelta(seconds=gate_seconds)
                            ):
                                blockers.append(f"gate evidence is stale: {gate.name}")
                        kill_switch = next(
                            (gate for gate in merge_decision.gates if gate.name == "kill_switch"),
                            None,
                        )
                        expected_kill_switch = (
                            f"kill-switch:{expected_policy.stop_signal.event_id}:"
                            f"{expected_policy.stop_signal.generation}"
                        )
                        if (
                            kill_switch is None
                            or expected_kill_switch not in kill_switch.evidence_refs
                        ):
                            blockers.append("kill-switch evidence is not current")
        status = ComplianceStatus.PASS if not blockers else ComplianceStatus.FAIL
        return ComplianceReport(self.name, status, checks, blockers)
