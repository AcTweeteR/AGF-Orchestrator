"""End-to-end autonomous delivery pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .adapters.codex import CodexAdapter, CodexProcessResult, redact_secrets
from .adapters.openhands import parse_openhands_output
from .authority_context import resolve_authority
from .compliance import ComplianceChecker
from .execution_models import ExecutionStatus
from .executor import (
    ExecutionValidationError,
    _changed_paths,
    _create_worktree,
    _remove_worktree,
    _run_validations,
    _status_lines,
    _validate_gates,
    load_plan,
)
from .git_delivery import (
    DraftPRCreator,
    GitDelivery,
    GitDeliveryError,
    RemoteBranchClassification,
    RemoteBranchEvidence,
    persist_remote_uncertainty,
    sanitize_branch_name,
)
from .historical_evidence import (
    EvidenceStatus,
    load_historical_baseline,
    load_historical_evidence,
    verify_current_bindings,
)
from .merge_models import GateEvidence, GateStatus, MergeDecision, RiskClass
from .merge_policy import REQUIRED_GATES, MergePolicyEngine, merge_policy_from_verified_active
from .models import ExecutionPlan, Task
from .preflight import PreflightError, collect_repository
from .review_models import (
    ComplianceStatus,
    DeliveryReport,
    FindingResolution,
    ReviewFinding,
    ReviewStatus,
    finding_identity,
)
from .reviewer import CodexReviewerAdapter, DeterministicReviewer, Reviewer
from .risk_engine import assess_risk, risk_evidence
from .risk_models import RiskAssessment, RollbackDifficulty, risk_from_dict

MAX_CORRECTION_ROUNDS = 2
_PROTECTED_PATH_MARKERS = (
    ".git",
    "constitution",
    "owner.key",
    "root_of_trust",
    "policy-state",
    "activation",
    "kill-switch",
)


@dataclass(frozen=True)
class PatchArtifact:
    path: str
    sha256: str
    changed_files: list[str]
    patch: str


@dataclass(frozen=True)
class Attempt:
    execution_status: ExecutionStatus
    changed_files: list[str]
    validation_results: list[str]
    evidence: list[str]
    blocking_issues: list[str]
    patch: PatchArtifact | None
    caller_clean: bool


def _atomic_write_text(target: Path, content: str) -> None:
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _task(plan: ExecutionPlan, task_id: str) -> Task:
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    raise ExecutionValidationError(f"task does not exist: {task_id}")


def _patch_policy(patch: str, changed_files: list[str], allowed: list[str]) -> list[str]:
    blockers: list[str] = []
    if "GIT binary patch" in patch:
        blockers.append("binary patches are not supported")
    if "160000" in patch or "Subproject commit" in patch:
        blockers.append("submodule patches are not supported")
    if re.search(r"^(old|new) mode ", patch, re.MULTILINE):
        blockers.append("mode changes are not supported")
    if re.search(r"^rename (from|to) ", patch, re.MULTILINE):
        blockers.append("renames are not supported")
    if ".git/" in patch or any(
        path == ".git" or path.startswith(".git/") for path in changed_files
    ):
        blockers.append(".git changes are not supported")
    if not set(changed_files).issubset(set(allowed)):
        blockers.append("patch paths exceed allowed_paths")
    return blockers


def _capture_patch(
    repository: str,
    base_sha: str,
    worktree: str,
    changed: list[str],
    allowed: list[str],
    artifact_dir: Path,
) -> PatchArtifact:
    patch_result = subprocess.run(
        ["git", "-C", worktree, "diff", "--binary", "--full-index", base_sha, "--"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    patch = patch_result.stdout
    blockers = _patch_policy(patch, changed, allowed)
    if blockers:
        raise ExecutionValidationError("; ".join(blockers))
    if not patch.strip():
        raise ExecutionValidationError("execution produced an empty patch")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifact_dir / f"agf-patch-{hashlib.sha256(patch.encode()).hexdigest()[:16]}.patch"
    _atomic_write_text(artifact, patch)
    return PatchArtifact(str(artifact), hashlib.sha256(patch.encode()).hexdigest(), changed, patch)


def _run_attempt(
    plan: ExecutionPlan,
    task: Task,
    repository: str,
    adapter: CodexAdapter,
    artifact_dir: Path,
    correction: str | None,
    validation_timeout: float,
) -> Attempt:
    context, allowed_paths, gate_evidence = _validate_gates(
        plan, task, repository, allow_default_branch=True
    )
    worktree: str | None = None
    process: CodexProcessResult | None = None
    changed: list[str] = []
    validation_results: list[str] = []
    blockers: list[str] = []
    evidence = list(gate_evidence)
    status = ExecutionStatus.FAILED
    caller_clean = False
    try:
        worktree = _create_worktree(context.root, context.head_sha)
        instruction = adapter.build_instruction(
            repository=worktree,
            task_id=task.task_id,
            title=task.title,
            objective=task.objective,
            allowed_paths=allowed_paths,
            acceptance_criteria=task.acceptance_criteria,
            validation_commands=task.validation_commands,
            stop_conditions=["scope expansion", "missing context", "architecture uncertainty"],
        )
        if correction:
            instruction += "\nCorrection request (accepted findings only):\n" + correction
        process = adapter.execute(instruction, worktree)
        evidence.append(f"adapter invoked: {adapter.name}: yes")
        if adapter.name == "openhands":
            if getattr(adapter, "uses_typed_events", False):
                callback_terminal = (
                    "yes"
                    if "completion source: callback" in process.stdout_summary
                    else "no"
                )
                evidence.extend(
                    [
                        process.stdout_summary,
                        "OpenHands selected transport: sdk-callback",
                        f"OpenHands terminal event found: {callback_terminal}",
                        "OpenHands terminal execution_status: "
                        f"{'finished' if process.transport_error is None else 'none'}",
                        "OpenHands final agent message present: "
                        f"{'yes' if process.final_message else 'no'}",
                    ]
                )
            else:
                interpretation = parse_openhands_output(
                    process.stdout_summary, process.stderr_summary
                )
                evidence.extend(
                    [
                        f"OpenHands JSON objects parsed: {interpretation.object_count}",
                        "OpenHands terminal event found: "
                        f"{'yes' if interpretation.terminal_event_found else 'no'}",
                        "OpenHands terminal execution_status: "
                        f"{interpretation.terminal_execution_status or 'none'}",
                        f"OpenHands selected transport: {interpretation.transport or 'none'}",
                        "OpenHands final agent message present: "
                        f"{'yes' if interpretation.final_agent_message_present else 'no'}",
                    ]
                )
        after = _status_lines(worktree)
        changed = _changed_paths([], after)
        evidence.append("changed-file scope checked")
        unauthorized = [path for path in changed if path not in allowed_paths]
        if process.human_required:
            status = ExecutionStatus.HUMAN_REQUIRED
            if process.transport_error and adapter.name == "openhands":
                blockers.append(process.transport_error)
            else:
                blockers.append("Codex invocation could not be verified")
            if adapter.name == "openhands":
                evidence.append("reviewer invoked: no")
        elif process.timed_out or process.exit_code != 0:
            blockers.append("Codex process did not complete successfully")
        elif unauthorized:
            blockers.append(f"unauthorized changed paths: {', '.join(unauthorized)}")
        else:
            validation_results, passed, validation_blockers = _run_validations(
                task.validation_commands, worktree, validation_timeout
            )
            blockers.extend(validation_blockers)
            if passed:
                patch = _capture_patch(
                    repository, context.head_sha, worktree, changed, allowed_paths, artifact_dir
                )
                status = ExecutionStatus.COMPLETED
                evidence.append("validations passed: yes")
                evidence.append("patch artifact created outside target repository")
                return Attempt(status, changed, validation_results, evidence, blockers, patch, True)
            blockers.append("one or more approved validations failed")
    except (OSError, subprocess.CalledProcessError, ExecutionValidationError) as exc:
        blockers.append(redact_secrets(str(exc)))
    finally:
        if worktree is not None:
            cleanup = _remove_worktree(repository, worktree)
            evidence.append(f"cleanup succeeded: {'yes' if cleanup else 'no'}")
        try:
            caller_clean = not _status_lines(repository)
        except (OSError, subprocess.CalledProcessError):
            caller_clean = False
        evidence.append(f"caller repository clean: {'yes' if caller_clean else 'no'}")
    return Attempt(status, changed, validation_results, evidence, blockers, None, caller_clean)


def _correction_request(findings: list[ReviewFinding], task: Task, current_patch: str = "") -> str:
    accepted = [
        finding
        for finding in findings
        if finding.accepted and finding.severity in {"blocker", "major"}
    ]
    lines = [
        "Correct only the following accepted review findings:",
        *[
            f"- ID={item.finding_id}; defect={item.message}; affected paths={item.affected_paths}; "
            f"evidence={item.evidence}; required change={item.required_change}"
            for item in accepted
        ],
        f"Unchanged task objective: {task.objective}",
        f"Expected correction must preserve acceptance criteria: {task.acceptance_criteria}",
        f"Allowed paths remain unchanged: {task.allowed_paths}",
        f"Approved validations remain unchanged: {task.validation_commands}",
        f"Current unified patch:\n{current_patch}",
        "Do not perform unrelated refactoring or expand scope.",
    ]
    return "\n".join(lines)


def _finding_fingerprint(finding: ReviewFinding) -> str:
    return finding_identity(finding)


def _review_resolution_states(
    review,
    previous: list[ReviewFinding],
) -> tuple[dict[str, FindingResolution], list[str]]:
    """Validate structured finding resolution without relying on reviewer prose."""
    previous_by_id = {finding_identity(finding): finding for finding in previous}
    current_by_id = {finding_identity(finding): finding for finding in review.findings}
    resolved_ids = review.resolved_finding_ids
    errors: list[str] = []
    if resolved_ids is not None and len(set(resolved_ids)) != len(resolved_ids):
        errors.append("review resolved_finding_ids contains duplicates")
    resolved = set(resolved_ids or [])
    unknown = sorted(resolved - set(previous_by_id))
    if unknown:
        errors.append("review resolved_finding_ids contains an unknown finding")

    states: dict[str, FindingResolution] = {}
    if review.status is ReviewStatus.APPROVE:
        expected = set(previous_by_id)
        if resolved_ids is not None and resolved != expected:
            errors.append("APPROVE did not resolve every prior finding")
        states.update({finding_id: FindingResolution.RESOLVED for finding_id in expected})
        return states, errors

    if review.status is not ReviewStatus.REQUEST_CHANGES:
        return states, errors
    if previous and resolved_ids is None:
        errors.append("REQUEST_CHANGES omitted resolved_finding_ids")
    for finding_id in previous_by_id:
        if finding_id in resolved:
            if finding_id in current_by_id:
                errors.append("a finding cannot be both resolved and retained")
            else:
                states[finding_id] = FindingResolution.RESOLVED
        elif finding_id in current_by_id:
            states[finding_id] = FindingResolution.UNCHANGED
        else:
            states[finding_id] = FindingResolution.UNVERIFIABLE
            errors.append("prior finding was omitted without explicit resolution")
    for finding_id in set(current_by_id) - set(previous_by_id):
        states[finding_id] = FindingResolution.NEW
    return states, errors


def _delivery_id(plan_id: str, task_id: str) -> str:
    return "delivery-" + hashlib.sha256(f"{plan_id}:{task_id}".encode()).hexdigest()[:16]


def _integrity_bound_decision(
    plan: ExecutionPlan,
    task: Task,
    attempt: Attempt,
    review,
    compliance_evidence: list[str],
    risk_assessment: RiskAssessment,
    *,
    project_id: str,
    branch: str,
    delivery_id: str,
    remote_evidence: RemoteBranchEvidence,
    base_sha: str,
    compliance_passed: bool,
) -> tuple[MergeDecision, list[str]]:
    """Bind completed delivery evidence to the active policy before Git mutation."""
    from .authority_context import resolve_authority

    resolved = resolve_authority(project_id)
    if resolved.constitution is None:
        raise ExecutionValidationError("verified Constitution is required for delivery")
    active = resolved.policy
    if active is None:
        raise ExecutionValidationError("verified active policy is required for delivery")
    generation = resolved.snapshot.get("generation") if resolved.snapshot is not None else None
    authority_binding = (
        f"risk-authority:constitution={resolved.constitution.constitution_id}:"
        f"policy={active.policy_hash}:generation={generation}"
    )
    if authority_binding not in risk_assessment.evidence_refs:
        raise ExecutionValidationError("risk evidence authority binding is stale")
    snapshot_refs = [
        item for item in risk_assessment.evidence_refs if item.startswith("risk-snapshot:")
    ]
    if len(snapshot_refs) != 1:
        raise ExecutionValidationError("risk evidence snapshot binding is missing")
    try:
        snapshot_end = datetime.fromisoformat(snapshot_refs[0].split("=", 1)[1])
        now = datetime.now(UTC)
        if snapshot_end.tzinfo is None or snapshot_end > now or now - snapshot_end > timedelta(
            seconds=int(active.freshness_limits["policy_seconds"])
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ExecutionValidationError("risk evidence snapshot binding is stale")
    snapshot_value = snapshot_refs[0].split("=", 1)[1]
    for evidence_ref in (
        item for item in risk_assessment.evidence_refs if item.startswith("risk-evidence:")
    ):
        if "coverage=" not in evidence_ref or snapshot_value not in evidence_ref:
            raise ExecutionValidationError("risk evidence coverage is not snapshot-bound")
    policy = merge_policy_from_verified_active(project_id)
    observed_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    patch_sha = attempt.patch.sha256 if attempt.patch is not None else ""
    evidence_refs = (f"delivery:{delivery_id}", f"patch:{patch_sha}")
    evidence = [*compliance_evidence, risk_evidence(risk_assessment)]
    gate_refs = evidence_refs + (f"observed:{observed_at}",)
    facts = {
        "constitution": resolved.constitution is not None,
        "policy": active is not None,
        "plan": plan.status.value == "READY" and task.status.value == "READY",
        "implementation": (
            attempt.patch is not None
            and attempt.execution_status is ExecutionStatus.COMPLETED
        ),
        "review": review.status is ReviewStatus.APPROVE,
        "compliance": compliance_passed,
        "validation": bool(attempt.validation_results)
        and all("exit_code=0" in item for item in attempt.validation_results),
        "risk": risk_assessment.level.name not in {"CRITICAL", "UNKNOWN"},
        "caller_clean": attempt.caller_clean,
        "base_sha": plan.repository.head_sha == base_sha,
        "authorized_paths": set(attempt.changed_files).issubset(set(task.allowed_paths)),
        "remote_state": remote_evidence.classification is RemoteBranchClassification.ABSENT,
        "delivery_branch": branch not in {"main", "master"}
        and not branch.startswith(("main/", "master/")),
        "kill_switch": not policy.stop_signal.active,
    }
    gates = []
    for name in REQUIRED_GATES:
        refs = gate_refs + (f"fact:{name}={'PASS' if facts[name] else 'FAIL'}",)
        if name == "remote_state":
            refs += (remote_evidence.queried_ref,)
        if name == "kill_switch":
            refs += (f"kill-switch:{policy.stop_signal.event_id}:{policy.stop_signal.generation}",)
        gates.append(
            GateEvidence(
                name,
                GateStatus.PASS if facts[name] else GateStatus.FAIL,
                refs,
                observed_at,
                "delivery-boundary",
                detail=f"authoritative delivery fact: {name}={facts[name]}",
            )
        )
    freshness_seconds = int(active.freshness_limits["policy_seconds"])
    expiry = (datetime.now(UTC) + timedelta(seconds=freshness_seconds)).isoformat()
    decision = MergePolicyEngine(policy).evaluate(
        project_id=project_id,
        task_id=task.task_id,
        base_sha=plan.repository.head_sha,
        delivery_sha=patch_sha,
        constitution_id=resolved.constitution.constitution_id,
        risk_class=RiskClass(risk_assessment.level.name),
        risk_assessment=risk_assessment,
        gates=gates,
        expiry=expiry,
    )
    if decision.decision_status.value != "ELIGIBLE":
        raise ExecutionValidationError(
            "active policy blocked delivery: " + "; ".join(decision.blocking_reasons)
        )
    return decision, evidence


def _risk_assessment_for_attempt(
    plan: ExecutionPlan,
    task: Task,
    attempt: Attempt,
    review,
    *,
    project_id: str,
    delivery_id: str,
) -> RiskAssessment:
    declared_protected = tuple(
        path
        for path in plan.scope.get("protected_paths", ())
        if isinstance(path, str) and path
    )
    discovered_protected = tuple(
        path
        for path in attempt.changed_files
        if any(marker in path.lower() for marker in _PROTECTED_PATH_MARKERS)
    )
    protected_paths = tuple(sorted(set(declared_protected + discovered_protected)))
    patch_sha = attempt.patch.sha256 if attempt.patch is not None else ""
    max_age = 86400
    authority = None
    try:
        authority = resolve_authority(project_id)
        max_age = int(authority.policy.freshness_limits["policy_seconds"])
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    decision_started = datetime.now(UTC).replace(microsecond=0).isoformat()
    rollback_evidence = _load_prospective_evidence(
        project_id, "rollback", decision_started, max_age
    )
    incident_evidence = _load_prospective_evidence(
        project_id, "incident", decision_started, max_age
    )
    for evidence in (rollback_evidence, incident_evidence):
        if evidence is not None:
            verify_current_bindings(evidence, expected_project_id=project_id)
    rollback_difficulty = RollbackDifficulty.UNKNOWN
    if rollback_evidence is not None:
        rollback_difficulty = (
            RollbackDifficulty.EASY
            if rollback_evidence.status is EvidenceStatus.VERIFIED_ZERO
            else RollbackDifficulty.HARD
        )
    incident_count = None
    if incident_evidence is not None:
        incident_count = incident_evidence.count
    authority_fact = (
        f"risk-authority:constitution={authority.constitution.constitution_id}:"
        f"policy={authority.policy.policy_hash}:"
        f"generation={authority.snapshot['generation']}"
        if authority is not None
        and authority.constitution is not None
        and authority.policy is not None
        and authority.snapshot is not None
        else "risk-authority:UNAVAILABLE"
    )
    snapshot_ends = tuple(
        evidence.coverage_end
        for evidence in (rollback_evidence, incident_evidence)
        if evidence is not None
    )
    if snapshot_ends and len(set(snapshot_ends)) != 1:
        raise ExecutionValidationError("historical evidence coverage snapshots disagree")
    facts = (
        f"risk-fact:rollback={rollback_evidence.status.value if rollback_evidence else 'UNKNOWN'}",
        f"risk-fact:incidents={incident_evidence.status.value if incident_evidence else 'UNKNOWN'}",
        authority_fact,
        f"risk-snapshot:historical-coverage-end={snapshot_ends[0] if snapshot_ends else 'NONE'}",
        *((
            f"risk-evidence:rollback:{rollback_evidence.evidence_hash}:"
            f"baseline={rollback_evidence.baseline_id}:"
            f"coverage={rollback_evidence.coverage_start}/{rollback_evidence.coverage_end}:"
            f"prebaseline={rollback_evidence.coverage_before_baseline}",
        ) if rollback_evidence is not None else ()),
        *((
            f"risk-evidence:incident:{incident_evidence.evidence_hash}:"
            f"baseline={incident_evidence.baseline_id}:"
            f"coverage={incident_evidence.coverage_start}/{incident_evidence.coverage_end}:"
            f"prebaseline={incident_evidence.coverage_before_baseline}",
        ) if incident_evidence is not None else ()),
    )
    return assess_risk(
        assessment_id="risk-" + hashlib.sha256(
            f"{project_id}:{delivery_id}:{patch_sha}".encode()
        ).hexdigest()[:24],
        project_id=project_id,
        task_id=task.task_id,
        changed_paths=tuple(attempt.changed_files),
        protected_paths=protected_paths,
        rollback_difficulty=rollback_difficulty,
        incident_count=incident_count,
        reviewer_blockers=sum(
            finding.severity in {"blocker", "major"} for finding in review.findings
        ),
        validation_passed=bool(attempt.validation_results)
        and all("exit_code=0" in item for item in attempt.validation_results),
        evidence_refs=(
            f"delivery:{delivery_id}",
            f"patch:{patch_sha}",
            *facts,
        ),
    )


def _load_prospective_evidence(
    project_id: str, evidence_type: str, decision_started: str, max_age: int
):
    """Accept only owner evidence bound to a persisted prospective baseline.

    The plan may predate the baseline.  That history remains UNKNOWN; only a
    signed record whose baseline covers the current decision can authorize it.
    """
    baseline = load_historical_baseline(project_id, max_age_seconds=max_age)
    evidence = load_historical_evidence(project_id, evidence_type, max_age_seconds=max_age)
    if (
        baseline is None
        or evidence is None
        or evidence.baseline_id != baseline.baseline_id
        or evidence.coverage_start < baseline.coverage_start
        or evidence.coverage_before_baseline != "UNKNOWN"
    ):
        return None
    try:
        start = datetime.fromisoformat(evidence.coverage_start)
        decision = datetime.fromisoformat(decision_started)
        end = datetime.fromisoformat(evidence.coverage_end)
    except ValueError:
        return None
    if start.tzinfo is None or end < start or start > decision:
        return None
    return evidence


def write_delivery_report(report: DeliveryReport, output: str | Path) -> None:
    _atomic_write_text(Path(output), json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")


class DeliveryPipeline:
    def __init__(
        self,
        *,
        adapter: CodexAdapter | None = None,
        reviewer: Reviewer | None = None,
        deterministic_reviewer: DeterministicReviewer | None = None,
        compliance: ComplianceChecker | None = None,
        pr_creator: DraftPRCreator | None = None,
        artifact_dir: str | Path | None = None,
        validation_timeout: float = 60.0,
        max_correction_rounds: int = MAX_CORRECTION_ROUNDS,
    ):
        self.adapter = adapter or CodexAdapter()
        self.deterministic_reviewer = deterministic_reviewer or (
            reviewer if isinstance(reviewer, DeterministicReviewer) else DeterministicReviewer()
        )
        self.reviewer = None if isinstance(reviewer, DeterministicReviewer) else reviewer
        if self.reviewer is None and reviewer is None:
            self.reviewer = CodexReviewerAdapter(self.adapter)
        self.compliance = compliance or ComplianceChecker()
        self.pr_creator = pr_creator or DraftPRCreator()
        self.artifact_dir = Path(artifact_dir or tempfile.gettempdir())
        self.validation_timeout = validation_timeout
        if not 0 <= max_correction_rounds <= MAX_CORRECTION_ROUNDS:
            raise ValueError("maximum correction rounds must be between 0 and 2")
        self.max_correction_rounds = max_correction_rounds

    def deliver(
        self,
        plan: ExecutionPlan,
        task_id: str,
        repository: str,
        *,
        execute: bool,
        merge_decision: MergeDecision | dict[str, object] | None = None,
        project_id: str | None = None,
    ) -> DeliveryReport:
        task = _task(plan, task_id)
        delivery_id = _delivery_id(plan.plan_id, task_id)
        base_sha = plan.repository.head_sha
        architecture_branch = plan.scope.get("delivery_branch")
        branch = (
            sanitize_branch_name(str(architecture_branch), task_id)
            if architecture_branch
            else sanitize_branch_name(plan.plan_id, task_id)
        )
        if not execute:
            return DeliveryReport(
                delivery_id,
                plan.plan_id,
                task_id,
                repository,
                base_sha,
                branch,
                "",
                "DRY_RUN",
                "NOT_RUN",
                [],
                0,
                "NOT_RUN",
                [],
                [],
                None,
                "NOT_REQUESTED",
                None,
                "DRY_RUN",
                [],
                ["dry-run: no model, patch, branch, commit, push, or PR mutation"],
            )
        review = None
        compliance = None
        attempt = None
        correction_rounds = 0
        try:
            context = collect_repository(repository)
            if (
                Path(context.root).resolve() != Path(plan.repository.root).resolve()
                or context.head_sha != base_sha
            ):
                raise ExecutionValidationError("repository context or base SHA does not match plan")
            if not context.clean:
                raise ExecutionValidationError("caller repository must be clean before delivery")
            remote_handler = (
                None
                if project_id is None
                else lambda evidence: persist_remote_uncertainty(
                    evidence, project_id=project_id, task_id=task_id
                )
            )
            remote_evidence = GitDelivery().validate_target(
                repository, base_sha, branch, uncertainty_handler=remote_handler
            )
            previous_findings: list[ReviewFinding] = []
            previous_patch_sha: str | None = None
            previous_patch = ""
            for _round_number in range(self.max_correction_rounds + 1):
                attempt = _run_attempt(
                    plan,
                    task,
                    repository,
                    self.adapter,
                    self.artifact_dir,
                    None
                    if review is None
                    else _correction_request(review.findings, task, previous_patch),
                    self.validation_timeout,
                )
                if (
                    attempt.execution_status is not ExecutionStatus.COMPLETED
                    or attempt.patch is None
                ):
                    raise ExecutionValidationError(
                        "; ".join(attempt.blocking_issues) or "execution failed"
                    )
                deterministic = self.deterministic_reviewer.review(
                    plan,
                    task,
                    attempt.changed_files,
                    attempt.patch.patch,
                    attempt.validation_results,
                    previous_findings,
                    _round_number,
                )
                if deterministic.status is not ReviewStatus.APPROVE:
                    review = deterministic
                elif self.reviewer is None:
                    review = deterministic
                else:
                    review = self.reviewer.review(
                        plan,
                        task,
                        attempt.changed_files,
                        attempt.patch.patch,
                        attempt.validation_results,
                        previous_findings,
                        _round_number,
                    )
                review = replace(
                    review,
                    evidence=[
                        "objective traceability: objective_id="
                        f"{plan.objective_id or 'UNSET'}; requirement_refs="
                        f"{sorted(set(plan.requirement_refs or task.requirement_refs))}",
                        *review.evidence,
                    ],
                )
                if review.status is ReviewStatus.APPROVE:
                    break
                if review.status is ReviewStatus.HUMAN_REQUIRED:
                    reason = "; ".join(review.blocking_issues) or "human judgment is required"
                    raise ExecutionValidationError(f"review requires human judgment: {reason}")
                if review.status is ReviewStatus.REJECT:
                    reason = "; ".join(review.blocking_issues) or "review rejected the change"
                    raise ExecutionValidationError(f"review rejected the change: {reason}")
                if review.status is not ReviewStatus.REQUEST_CHANGES:
                    raise ExecutionValidationError("review returned an unsupported decision")
                _, resolution_errors = _review_resolution_states(review, previous_findings)
                if resolution_errors:
                    raise ExecutionValidationError("; ".join(resolution_errors))
                unresolved = [
                    finding
                    for finding in review.findings
                    if finding.severity in {"blocker", "major"}
                ]
                if previous_patch_sha == attempt.patch.sha256 and set(
                    _finding_fingerprint(item) for item in unresolved
                ) & set(_finding_fingerprint(item) for item in previous_findings):
                    raise ExecutionValidationError(
                        "review non-convergence: unchanged unresolved finding"
                    )
                previous_findings = unresolved
                previous_patch_sha = attempt.patch.sha256
                previous_patch = attempt.patch.patch
                if correction_rounds >= self.max_correction_rounds:
                    raise ExecutionValidationError(
                        "review correction limit exhausted after "
                        f"{correction_rounds} completed correction rounds"
                    )
                correction_rounds += 1
            assert attempt is not None and review is not None and attempt.patch is not None
            decision_evidence = list(attempt.evidence)
            risk_assessment = None
            if project_id is not None:
                active_policy = resolve_authority(project_id).policy
                if active_policy is not None:
                    risk_assessment = _risk_assessment_for_attempt(
                        plan,
                        task,
                        attempt,
                        review,
                        project_id=project_id,
                        delivery_id=delivery_id,
                    )
                    decision_evidence.append(risk_evidence(risk_assessment))
            if merge_decision is not None and merge_decision.risk_assessment is not None:
                risk_assessment = risk_from_dict(merge_decision.risk_assessment)
                if project_id is not None:
                    recomputed = _risk_assessment_for_attempt(
                        plan,
                        task,
                        attempt,
                        review,
                        project_id=project_id,
                        delivery_id=delivery_id,
                    )
                    if risk_assessment.to_dict() != recomputed.to_dict():
                        raise ExecutionValidationError(
                            "supplied merge decision risk evidence does not match current delivery"
                        )
            compliance = self.compliance.check(
                plan,
                task,
                review,
                attempt.changed_files,
                attempt.validation_results,
                decision_evidence,
                attempt.caller_clean,
                base_sha,
                risk_assessment=risk_assessment,
            )
            if compliance.status is not ComplianceStatus.PASS:
                raise ExecutionValidationError("; ".join(compliance.blocking_issues))
            if merge_decision is None and project_id is not None and risk_assessment is not None:
                merge_decision, _ = _integrity_bound_decision(
                    plan,
                    task,
                    attempt,
                    review,
                    decision_evidence,
                    risk_assessment,
                    project_id=project_id,
                    branch=branch,
                    delivery_id=delivery_id,
                    remote_evidence=remote_evidence,
                    base_sha=base_sha,
                    compliance_passed=compliance.status is ComplianceStatus.PASS,
                )
            if merge_decision is not None:
                compliance = self.compliance.check(
                    plan,
                    task,
                    review,
                    attempt.changed_files,
                    attempt.validation_results,
                    decision_evidence,
                    attempt.caller_clean,
                    base_sha,
                    risk_assessment=risk_assessment,
                    merge_decision=merge_decision,
                    expected_project_id=project_id,
                    expected_task_id=task.task_id,
                    expected_base_sha=base_sha,
                    expected_delivery_sha=attempt.patch.sha256,
                    expected_policy=merge_policy_from_verified_active(project_id)
                    if project_id is not None
                    else None,
                    expected_constitution_id=resolve_authority(project_id).constitution.constitution_id
                    if project_id is not None
                    and resolve_authority(project_id).constitution is not None
                    else None,
                )
                if compliance.status is not ComplianceStatus.PASS:
                    raise ExecutionValidationError("; ".join(compliance.blocking_issues))
            if task.task_id == "E6-T2" and merge_decision is None:
                raise ExecutionValidationError(
                    "E6-T2 delivery requires an externally evidenced LOW decision"
                )
            git_result = GitDelivery().deliver(
                repository,
                base_sha,
                branch,
                attempt.patch.path,
                task,
                merge_decision=merge_decision,
                project_id=project_id,
                expected_patch_sha256=attempt.patch.sha256,
                validation_timeout=self.validation_timeout,
            )
            body = self._pr_body(
                plan, task, attempt, review, compliance, correction_rounds, git_result
            )
            try:
                pr_url = self.pr_creator.create(repository, branch, f"AGF: {task.title}", body)
            except GitDeliveryError as exc:
                return DeliveryReport(
                    delivery_id,
                    plan.plan_id,
                    task_id,
                    repository,
                    base_sha,
                    branch,
                    attempt.patch.sha256,
                    attempt.execution_status.value,
                    review.status.value,
                    [finding.to_dict() for finding in review.findings],
                    correction_rounds,
                    compliance.status.value,
                    git_result.changed_files,
                    git_result.validation_results,
                    git_result.commit_sha,
                    git_result.push_status,
                    None,
                    "HUMAN_REQUIRED",
                    [str(exc)],
                    attempt.evidence + compliance.evidence,
                )
            return DeliveryReport(
                delivery_id,
                plan.plan_id,
                task_id,
                repository,
                base_sha,
                branch,
                attempt.patch.sha256,
                attempt.execution_status.value,
                review.status.value,
                [finding.to_dict() for finding in review.findings],
                correction_rounds,
                compliance.status.value,
                git_result.changed_files,
                git_result.validation_results,
                git_result.commit_sha,
                git_result.push_status,
                pr_url,
                "COMPLETED",
                [],
                attempt.evidence + compliance.evidence,
            )
        except (ExecutionValidationError, PreflightError, GitDeliveryError, OSError) as exc:
            implementation_failed = (
                attempt is not None
                and attempt.execution_status is not ExecutionStatus.COMPLETED
            )
            report_review_status = (
                "NOT_RUN" if implementation_failed else review.status.value
                if review is not None else "NOT_APPROVED"
            )
            report_findings = [finding.to_dict() for finding in review.findings] if review else []
            report_execution_status = attempt.execution_status.value if attempt else "FAILED"
            report_changed = attempt.changed_files if attempt else []
            report_validation = attempt.validation_results if attempt else []
            return DeliveryReport(
                delivery_id,
                plan.plan_id,
                task_id,
                repository,
                base_sha,
                branch,
                "",
                report_execution_status,
                report_review_status,
                report_findings,
                correction_rounds,
                "NOT_RUN"
                if implementation_failed
                else compliance.status.value if compliance else "FAIL",
                report_changed,
                report_validation,
                None,
                "NOT_REQUESTED",
                None,
                "HUMAN_REQUIRED"
                if "human" in str(exc).lower() or "non-convergence" in str(exc)
                else "BLOCKED",
                [redact_secrets(str(exc))],
                attempt.evidence if attempt else [],
            )

    @staticmethod
    def _pr_body(plan, task, attempt, review, compliance, rounds, git_result) -> str:
        return "\n".join(
            [
                f"## Objective\n\n{task.objective}",
                f"## Plan and task\n\n- Plan ID: `{plan.plan_id}`\n- Task ID: `{task.task_id}`",
                f"## Allowed paths\n\n{task.allowed_paths}",
                f"## Files changed\n\n{git_result.changed_files}",
                f"## Reviewer\n\n- Result: `{review.status.value}`\n"
                f"- Findings: `{review.findings}`",
                f"## Compliance\n\n- Result: `{compliance.status.value}`",
                f"## Validation\n\n{git_result.validation_results}",
                f"## Evidence\n\n{attempt.evidence}",
                f"## Correction rounds\n\n{rounds}",
                "## Limitations\n\nMerge requires human or later release-manager "
                "authorization. AGF does not merge PRs.",
            ]
        )


def load_delivery_plan(path: str | Path) -> ExecutionPlan:
    return load_plan(path)
