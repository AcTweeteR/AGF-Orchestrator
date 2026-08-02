"""End-to-end autonomous delivery pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .adapters.codex import CodexAdapter, CodexProcessResult, redact_secrets
from .adapters.openhands import parse_openhands_output
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
from .git_delivery import DraftPRCreator, GitDelivery, GitDeliveryError, sanitize_branch_name
from .models import ExecutionPlan, Task
from .preflight import PreflightError, collect_repository
from .review_models import ComplianceStatus, DeliveryReport, ReviewFinding, ReviewStatus
from .reviewer import CodexReviewerAdapter, DeterministicReviewer, Reviewer

MAX_CORRECTION_ROUNDS = 2


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
    context, allowed_paths, gate_evidence = _validate_gates(plan, task, repository)
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
            blockers.append("Codex invocation could not be verified")
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
    normalized = "|".join(
        [
            finding.category.lower().strip(),
            ",".join(sorted(path.lower().strip() for path in finding.affected_paths)),
            " ".join(finding.required_change.lower().split()),
        ]
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def _previous_findings_resolved(review, previous: list[ReviewFinding]) -> bool:
    if not previous:
        return True
    text = " ".join([review.summary, *(review.checks_performed or [])]).lower()
    return all(
        finding.finding_id.lower() in text
        and any(marker in text for marker in ("resolved", "still open", "open"))
        for finding in previous
    )


def _delivery_id(plan_id: str, task_id: str) -> str:
    return "delivery-" + hashlib.sha256(f"{plan_id}:{task_id}".encode()).hexdigest()[:16]


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
        self, plan: ExecutionPlan, task_id: str, repository: str, *, execute: bool
    ) -> DeliveryReport:
        task = _task(plan, task_id)
        delivery_id = _delivery_id(plan.plan_id, task_id)
        base_sha = plan.repository.head_sha
        branch = sanitize_branch_name(plan.plan_id, task_id)
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
        try:
            context = collect_repository(repository)
            if context.root != plan.repository.root or context.head_sha != base_sha:
                raise ExecutionValidationError("repository context or base SHA does not match plan")
            if not context.clean:
                raise ExecutionValidationError("caller repository must be clean before delivery")
            review = None
            compliance = None
            attempt = None
            correction_rounds = 0
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
                    )
                if not _previous_findings_resolved(review, previous_findings):
                    raise ExecutionValidationError(
                        "review did not mark every previous finding resolved or still open"
                    )
                if review.status is ReviewStatus.APPROVE:
                    break
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
                correction_rounds += 1
                if (
                    review.status is not ReviewStatus.REQUEST_CHANGES
                    or correction_rounds > self.max_correction_rounds
                ):
                    raise ExecutionValidationError("review did not approve within correction limit")
            assert attempt is not None and review is not None and attempt.patch is not None
            compliance = self.compliance.check(
                plan,
                task,
                review,
                attempt.changed_files,
                attempt.validation_results,
                attempt.evidence,
                attempt.caller_clean,
                base_sha,
            )
            if compliance.status is not ComplianceStatus.PASS:
                raise ExecutionValidationError("; ".join(compliance.blocking_issues))
            git_result = GitDelivery().deliver(
                repository,
                base_sha,
                branch,
                attempt.patch.path,
                task,
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
            report_review_status = review.status.value if review is not None else "NOT_APPROVED"
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
                compliance.status.value if compliance else "FAIL",
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
