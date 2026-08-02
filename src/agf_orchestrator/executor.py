"""Safety gates and post-execution verification for one approved task."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .adapters.codex import CodexAdapter, redact_secrets
from .execution_models import ExecutionResult, ExecutionStatus
from .models import ExecutionPlan, PlanStatus, Task, plan_from_dict
from .preflight import PreflightError, collect_repository


class ExecutionValidationError(ValueError):
    """Raised when a plan or task cannot be safely executed."""


def load_plan(path: str | Path) -> ExecutionPlan:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return plan_from_dict(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ExecutionValidationError(f"invalid plan: {exc}") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_id(plan_id: str, task_id: str) -> str:
    return "execution-" + hashlib.sha256(f"{plan_id}:{task_id}".encode()).hexdigest()[:16]


def _status_lines(repository: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", repository, "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _changed_paths(before: list[str], after: list[str]) -> list[str]:
    paths: set[str] = set()
    for line in [*before, *after]:
        value = line[3:] if len(line) >= 3 else line
        if " -> " in value:
            paths.update(value.split(" -> ", 1))
        elif value:
            paths.add(value)
    return sorted(paths)


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    candidate = Path(path)
    return any(
        candidate == Path(allowed) or Path(allowed) in candidate.parents
        for allowed in allowed_paths
    )


def _find_task(plan: ExecutionPlan, task_id: str) -> Task:
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    raise ExecutionValidationError(f"task does not exist: {task_id}")


def _validate_live_gates(plan: ExecutionPlan, task: Task, repository: str):
    if plan.status is not PlanStatus.READY:
        raise ExecutionValidationError(f"plan status must be READY, got {plan.status}")
    if task.status is not PlanStatus.READY:
        raise ExecutionValidationError(f"task status must be READY, got {task.status}")
    context = collect_repository(repository)
    if Path(context.root).resolve() != Path(plan.repository.root).resolve():
        raise ExecutionValidationError("repository does not match the plan repository context")
    if context.branch in {"main", "master"}:
        raise ExecutionValidationError("live execution is blocked on main or master")
    if not context.origin:
        raise ExecutionValidationError("live execution requires an origin remote")
    if not context.head_sha:
        raise ExecutionValidationError("live execution requires a resolvable HEAD")
    if not task.allowed_paths:
        raise ExecutionValidationError("task allowed_paths must not be empty")
    if not task.acceptance_criteria:
        raise ExecutionValidationError("task acceptance_criteria must not be empty")
    if not task.validation_commands:
        raise ExecutionValidationError("task validation_commands must not be empty")
    if plan.human_intervention:
        raise ExecutionValidationError("plan has unresolved human intervention")
    architecture = plan.architecture_impact
    if architecture.get("requires_architect") or architecture.get("status") in {
        "unknown", "pending", "proposed", "to_be_assessed"
    }:
        raise ExecutionValidationError("architecture decision is pending")
    task_map = {candidate.task_id: candidate for candidate in plan.tasks}
    for dependency_id in task.dependencies:
        dependency = task_map.get(dependency_id)
        if dependency is None:
            raise ExecutionValidationError(f"dependency does not exist: {dependency_id}")
        if dependency.status in {PlanStatus.BLOCKED, PlanStatus.HUMAN_REQUIRED}:
            raise ExecutionValidationError(f"dependency is not satisfied: {dependency_id}")
    return context


def _run_validations(commands: list[str], repository: str) -> tuple[list[str], bool]:
    evidence: list[str] = []
    passed = True
    for command in commands:
        try:
            completed = subprocess.run(
                shlex.split(command),
                cwd=repository,
                capture_output=True,
                text=True,
                shell=False,
            )
            evidence.append(f"{command}: exit_code={completed.returncode}")
            if completed.returncode != 0:
                passed = False
        except (OSError, ValueError) as exc:
            evidence.append(f"{command}: failed to start ({redact_secrets(str(exc))})")
            passed = False
    return evidence, passed


class Executor:
    """Execute at most one selected task under the required safety gates."""

    def __init__(self, adapter: CodexAdapter | None = None) -> None:
        self.adapter = adapter or CodexAdapter()

    def execute(
        self,
        plan: ExecutionPlan,
        task_id: str,
        repository: str,
        *,
        dry_run: bool = True,
    ) -> ExecutionResult:
        started = _now()
        execution_id = _execution_id(plan.plan_id, task_id)
        try:
            task = _find_task(plan, task_id)
        except ExecutionValidationError as exc:
            return ExecutionResult(
                execution_id, plan.plan_id, task_id, self.adapter.name, started, _now(),
                repository, plan.repository.branch, "not invoked", None, ExecutionStatus.BLOCKED,
                [], [], "", "", [], [str(exc)],
            )
        if plan.status is not PlanStatus.READY:
            return self._blocked(
                plan, task, repository, started, execution_id, "plan status is not READY"
            )
        if task.status is not PlanStatus.READY:
            return self._blocked(
                plan, task, repository, started, execution_id, "task status is not READY"
            )
        if dry_run:
            finished = _now()
            return ExecutionResult(
                execution_id, plan.plan_id, task.task_id, self.adapter.name, started, finished,
                repository, plan.repository.branch, "dry-run: Codex was not invoked", None,
                ExecutionStatus.DRY_RUN, [], task.validation_commands, "", "",
                ["plan validated", "dry-run performed without subprocess execution"], [],
            )
        try:
            context = _validate_live_gates(plan, task, repository)
        except (ExecutionValidationError, PreflightError) as exc:
            return self._blocked(plan, task, repository, started, execution_id, str(exc))
        before = _status_lines(repository)
        instruction = self.adapter.build_instruction(
            repository=context.root,
            task_id=task.task_id,
            title=task.title,
            objective=task.objective,
            allowed_paths=task.allowed_paths,
            acceptance_criteria=task.acceptance_criteria,
            validation_commands=task.validation_commands,
            stop_conditions=["scope expansion", "missing context", "architecture uncertainty"],
        )
        process = self.adapter.execute(instruction, context.root)
        after = _status_lines(context.root)
        changed = _changed_paths(before, after)
        if process.timed_out or process.exit_code != 0:
            status = ExecutionStatus.FAILED
            blockers = ["Codex process did not complete successfully"]
            evidence = ["process exit code preserved"]
            if process.timed_out:
                evidence.append("timeout reached")
            return self._result(
                plan, task, context.root, context.branch, started, execution_id, process,
                status, changed, task.validation_commands, evidence, blockers,
            )
        unauthorized = [path for path in changed if not _path_allowed(path, task.allowed_paths)]
        if unauthorized:
            return self._result(
                plan, task, context.root, context.branch, started, execution_id, process,
                ExecutionStatus.FAILED, changed, task.validation_commands,
                ["changed-file scope checked"],
                [f"unauthorized changed paths: {', '.join(unauthorized)}"],
            )
        validation_evidence, validations_passed = _run_validations(
            task.validation_commands, context.root
        )
        status = ExecutionStatus.COMPLETED if validations_passed else ExecutionStatus.FAILED
        blockers = [] if validations_passed else ["one or more approved validations failed"]
        return self._result(
            plan, task, context.root, context.branch, started, execution_id, process,
            status, changed, task.validation_commands, validation_evidence, blockers,
        )

    def _blocked(self, plan, task, repository, started, execution_id, reason):
        return ExecutionResult(
            execution_id, plan.plan_id, task.task_id, self.adapter.name, started, _now(),
            repository, plan.repository.branch, "not invoked", None, ExecutionStatus.BLOCKED,
            [], task.validation_commands, "", "", [], [reason],
        )

    def _result(
        self, plan, task, repository, branch, started, execution_id, process, status,
        changed, validations, evidence, blockers,
    ):
        return ExecutionResult(
            execution_id, plan.plan_id, task.task_id, self.adapter.name, started, _now(),
            repository, branch, process.command_summary, process.exit_code, status,
            changed,
            validations,
            process.stdout_summary,
            process.stderr_summary,
            evidence,
            blockers,
        )


def write_execution_result(result: ExecutionResult, output: str | Path) -> None:
    """Atomically write a report, cleaning any temporary file on failure."""
    target = Path(output).expanduser().resolve()
    serialized = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
