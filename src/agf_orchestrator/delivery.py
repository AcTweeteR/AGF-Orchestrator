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
from .reviewer import DeterministicReviewer, Reviewer

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
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
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
        check=True, capture_output=True, text=True, shell=False,
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
        evidence.append("Codex invoked: yes")
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


def _correction_request(findings: list[ReviewFinding], task: Task) -> str:
    accepted = [finding for finding in findings if finding.accepted]
    lines = [
        "Correct only the following accepted review findings:",
        *[
            f"- {item.code}: {item.message}; affected paths={item.affected_paths}"
            for item in accepted
        ],
        f"Expected correction must preserve acceptance criteria: {task.acceptance_criteria}",
        f"Allowed paths remain unchanged: {task.allowed_paths}",
        f"Approved validations remain unchanged: {task.validation_commands}",
        "Do not perform unrelated refactoring or expand scope.",
    ]
    return "\n".join(lines)


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
        compliance: ComplianceChecker | None = None,
        pr_creator: DraftPRCreator | None = None,
        artifact_dir: str | Path | None = None,
        validation_timeout: float = 60.0,
    ):
        self.adapter = adapter or CodexAdapter()
        self.reviewer = reviewer or DeterministicReviewer()
        self.compliance = compliance or ComplianceChecker()
        self.pr_creator = pr_creator or DraftPRCreator()
        self.artifact_dir = Path(artifact_dir or tempfile.gettempdir())
        self.validation_timeout = validation_timeout

    def deliver(
        self, plan: ExecutionPlan, task_id: str, repository: str, *, execute: bool
    ) -> DeliveryReport:
        task = _task(plan, task_id)
        delivery_id = _delivery_id(plan.plan_id, task_id)
        base_sha = plan.repository.head_sha
        branch = sanitize_branch_name(plan.plan_id, task_id)
        if not execute:
            return DeliveryReport(
                delivery_id, plan.plan_id, task_id, repository, base_sha, branch, "",
                "DRY_RUN", "NOT_RUN", [], 0, "NOT_RUN", [], [], None, "NOT_REQUESTED", None,
                "DRY_RUN", [], ["dry-run: no model, patch, branch, commit, push, or PR mutation"],
            )
        try:
            context = collect_repository(repository)
            if context.root != plan.repository.root or context.head_sha != base_sha:
                raise ExecutionValidationError("repository context or base SHA does not match plan")
            if not context.clean:
                raise ExecutionValidationError("caller repository must be clean before delivery")
            review = None
            attempt = None
            correction_rounds = 0
            for round_number in range(MAX_CORRECTION_ROUNDS + 1):
                attempt = _run_attempt(
                    plan, task, repository, self.adapter, self.artifact_dir,
                    None if review is None else _correction_request(review.findings, task),
                    self.validation_timeout,
                )
                if (
                    attempt.execution_status is not ExecutionStatus.COMPLETED
                    or attempt.patch is None
                ):
                    raise ExecutionValidationError(
                        "; ".join(attempt.blocking_issues) or "execution failed"
                    )
                review = self.reviewer.review(
                    plan,
                    task,
                    attempt.changed_files,
                    attempt.patch.patch,
                    attempt.validation_results,
                )
                if review.status is ReviewStatus.APPROVE:
                    break
                correction_rounds += 1
                if (
                    review.status is not ReviewStatus.REQUEST_CHANGES
                    or correction_rounds > MAX_CORRECTION_ROUNDS
                ):
                    raise ExecutionValidationError("review did not approve within correction limit")
            assert attempt is not None and review is not None and attempt.patch is not None
            compliance = self.compliance.check(
                plan, task, review, attempt.changed_files, attempt.validation_results,
                attempt.evidence, attempt.caller_clean, base_sha,
            )
            if compliance.status is not ComplianceStatus.PASS:
                raise ExecutionValidationError("; ".join(compliance.blocking_issues))
            git_result = GitDelivery().deliver(
                repository, base_sha, branch, attempt.patch.path, task,
                expected_patch_sha256=attempt.patch.sha256,
                validation_timeout=self.validation_timeout,
            )
            body = self._pr_body(
                plan, task, attempt, review, compliance, correction_rounds, git_result
            )
            try:
                pr_url = self.pr_creator.create(
                    repository, branch, f"AGF: {task.title}", body
                )
            except GitDeliveryError as exc:
                return DeliveryReport(
                    delivery_id, plan.plan_id, task_id, repository, base_sha, branch,
                    attempt.patch.sha256, attempt.execution_status.value, review.status.value,
                    [finding.__dict__ for finding in review.findings], correction_rounds,
                    compliance.status.value,
                    git_result.changed_files,
                    git_result.validation_results,
                    git_result.commit_sha, git_result.push_status, None, "HUMAN_REQUIRED",
                    [str(exc)], attempt.evidence + compliance.evidence,
                )
            return DeliveryReport(
                delivery_id, plan.plan_id, task_id, repository, base_sha, branch,
                attempt.patch.sha256, attempt.execution_status.value, review.status.value,
                [finding.__dict__ for finding in review.findings], correction_rounds,
                compliance.status.value, git_result.changed_files, git_result.validation_results,
                git_result.commit_sha, git_result.push_status, pr_url, "COMPLETED", [],
                attempt.evidence + compliance.evidence,
            )
        except (ExecutionValidationError, PreflightError, GitDeliveryError, OSError) as exc:
            return DeliveryReport(
                delivery_id, plan.plan_id, task_id, repository, base_sha, branch, "",
                "FAILED", "NOT_APPROVED", [], 0, "FAIL", [], [], None, "NOT_REQUESTED", None,
                "HUMAN_REQUIRED" if "human" in str(exc).lower() else "BLOCKED",
                [redact_secrets(str(exc))], [],
            )

    @staticmethod
    def _pr_body(plan, task, attempt, review, compliance, rounds, git_result) -> str:
        return "\n".join([
            f"## Objective\n\n{task.objective}",
            f"## Plan and task\n\n- Plan ID: `{plan.plan_id}`\n- Task ID: `{task.task_id}`",
            f"## Allowed paths\n\n{task.allowed_paths}",
            f"## Files changed\n\n{git_result.changed_files}",
            f"## Reviewer\n\n- Result: `{review.status.value}`\n- Findings: `{review.findings}`",
            f"## Compliance\n\n- Result: `{compliance.status.value}`",
            f"## Validation\n\n{git_result.validation_results}",
            f"## Evidence\n\n{attempt.evidence}",
            f"## Correction rounds\n\n{rounds}",
            "## Limitations\n\nMerge requires human or later release-manager "
            "authorization. AGF does not merge PRs.",
        ])


def load_delivery_plan(path: str | Path) -> ExecutionPlan:
    return load_plan(path)
