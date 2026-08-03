"""Safety gates, isolated execution, and post-execution verification."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .adapters.codex import CodexAdapter, CodexProcessResult, redact_secrets
from .adapters.openhands import parse_openhands_output
from .execution_models import ExecutionResult, ExecutionStatus
from .models import ExecutionPlan, PlanStatus, Task, plan_from_dict
from .preflight import PreflightError, collect_repository

CONTROL_SYNTAX = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n")
SHELL_CONTROL_TOKENS = {";", "&&", "||", "|", ">", "<"}


class ExecutionValidationError(ValueError):
    """Raised when a plan or task cannot be safely executed."""


class GateFailure(ExecutionValidationError):
    """A gate failure with the checks completed before it failed."""

    def __init__(self, message: str, evidence: list[str]):
        super().__init__(message)
        self.evidence = evidence


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
        shell=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def _changed_paths(before: list[str], after: list[str]) -> list[str]:
    paths: set[str] = set()
    for line in [*before, *after]:
        value = line[3:] if len(line) >= 3 else line
        if " -> " in value:
            paths.update(value.split(" -> ", 1))
        elif value:
            paths.add(value.replace("\\", "/"))
    return sorted(paths)


def _find_task(plan: ExecutionPlan, task_id: str) -> Task:
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    raise ExecutionValidationError(f"task does not exist: {task_id}")


def _normalize_allowed_paths(paths: list[str], repository: str) -> list[str]:
    if not paths:
        raise ExecutionValidationError("task allowed_paths must not be empty")
    root = Path(repository).resolve()
    normalized: list[str] = []
    for raw in paths:
        value = raw.replace("\\", "/").strip()
        if not value:
            raise ExecutionValidationError("allowed_paths cannot contain empty paths")
        if value == "." or value.startswith("/") or (len(value) > 1 and value[1] == ":"):
            raise ExecutionValidationError(f"invalid unrestricted or absolute allowed path: {raw}")
        parts = PurePosixPath(value).parts
        if ".." in parts:
            raise ExecutionValidationError(f"allowed path cannot contain '..': {raw}")
        if ".git" in parts:
            raise ExecutionValidationError(f"allowed path cannot use .git: {raw}")
        resolved = (root / Path(*parts)).resolve()
        if resolved == root or root not in resolved.parents:
            raise ExecutionValidationError(f"allowed path resolves outside repository: {raw}")
        normalized_path = "/".join(parts)
        if normalized_path not in normalized:
            normalized.append(normalized_path)
    return normalized


def _validate_commands(commands: list[str]) -> list[str]:
    if not commands:
        raise ExecutionValidationError("task validation_commands must not be empty")
    parsed: list[str] = []
    for command in commands:
        if not command.strip():
            raise ExecutionValidationError("validation commands cannot be empty")
        if any(token in command for token in ("`", "$(", "\n")):
            raise ExecutionValidationError(
                f"validation command contains shell control syntax: {command}"
            )
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
            lexer.whitespace_split = True
            tokens = list(lexer)
            if any(token in SHELL_CONTROL_TOKENS for token in tokens):
                raise ExecutionValidationError(
                    f"validation command contains shell control syntax: {command}"
                )
            argv = shlex.split(command)
        except ValueError as exc:
            raise ExecutionValidationError(f"invalid validation command: {exc}") from exc
        if not argv or shutil.which(argv[0]) is None:
            executable = argv[0] if argv else command
            raise ExecutionValidationError(
                f"validation executable cannot be resolved: {executable}"
            )
        parsed.append(command)
    return parsed


def _validate_gates(plan: ExecutionPlan, task: Task, repository: str):
    evidence: list[str] = []

    def checked(name: str) -> None:
        evidence.append(f"gate checked: {name}")

    if plan.status is PlanStatus.HUMAN_REQUIRED:
        raise GateFailure("plan requires human intervention", evidence)
    checked("plan status READY")
    if plan.status is not PlanStatus.READY:
        raise GateFailure(f"plan status must be READY, got {plan.status}", evidence)
    checked("task status READY")
    if task.status is not PlanStatus.READY:
        raise GateFailure(f"task status must be READY, got {task.status}", evidence)

    try:
        context = collect_repository(repository)
    except PreflightError as exc:
        evidence.append("gate checked: repository preflight")
        raise GateFailure(str(exc), evidence) from exc
    checked("repository identity")
    if Path(context.root).resolve() != Path(plan.repository.root).resolve():
        raise GateFailure("repository does not match the plan repository context", evidence)
    checked("named non-default branch")
    if context.branch in {"main", "master"}:
        raise GateFailure("live execution is blocked on main or master", evidence)
    checked("clean repository")
    checked("origin present")
    checked("HEAD resolvable")

    allowed_paths = _normalize_allowed_paths(task.allowed_paths, context.root)
    evidence.append(f"gate checked: allowed paths valid ({len(allowed_paths)})")
    if not task.acceptance_criteria:
        raise GateFailure("task acceptance_criteria must not be empty", evidence)
    checked("acceptance criteria present")
    _validate_commands(task.validation_commands)
    checked("validation commands safe and resolvable")
    if plan.human_intervention:
        raise GateFailure("plan has unresolved human intervention", evidence)
    checked("human intervention clear")
    architecture = plan.architecture_impact
    if architecture.get("requires_architect") or architecture.get("status") != "approved":
        raise GateFailure("architecture decision is not approved", evidence)
    checked("architecture approved")
    if task.dependencies:
        raise GateFailure(
            "dependency completion cannot yet be verified; non-empty dependencies are blocked",
            evidence,
        )
    checked("dependencies satisfied")
    return context, allowed_paths, evidence


def _run_validations(
    commands: list[str], repository: str, timeout: float
) -> tuple[list[str], bool, list[str]]:
    evidence: list[str] = []
    blockers: list[str] = []
    passed = True
    for command in commands:
        try:
            argv = shlex.split(command)
            completed = subprocess.run(
                argv,
                cwd=repository,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            evidence.append(
                f"validation {redact_secrets(command)}: exit_code={completed.returncode}; "
                f"stdout={redact_secrets(completed.stdout, limit=500)}; "
                f"stderr={redact_secrets(completed.stderr, limit=500)}"
            )
            if completed.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            evidence.append(f"validation {redact_secrets(command)}: timeout")
            blockers.append(f"validation command timed out: {redact_secrets(command)}")
            passed = False
        except (OSError, ValueError) as exc:
            evidence.append(f"validation {redact_secrets(command)}: failed to start")
            blockers.append(f"validation command failed: {redact_secrets(str(exc))}")
            passed = False
    return evidence, passed, blockers


def _create_worktree(repository: str, head_sha: str) -> str:
    worktree = tempfile.mkdtemp(prefix="agf-execution-")
    os.rmdir(worktree)
    try:
        subprocess.run(
            ["git", "-C", repository, "worktree", "add", "--detach", worktree, head_sha],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
        raise
    return worktree


def _remove_worktree(repository: str, worktree: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", repository, "worktree", "remove", "--force", worktree],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        return not Path(worktree).exists()
    except (OSError, subprocess.CalledProcessError):
        return False


class Executor:
    """Execute at most one selected task under shared safety gates."""

    def __init__(self, adapter: CodexAdapter | None = None, validation_timeout: float = 60.0):
        self.adapter = adapter or CodexAdapter()
        self.validation_timeout = validation_timeout

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
            return self._blocked(plan, task_id, repository, started, execution_id, str(exc), [])
        try:
            context, allowed_paths, gate_evidence = _validate_gates(plan, task, repository)
        except GateFailure as exc:
            status = (
                ExecutionStatus.HUMAN_REQUIRED
                if plan.status is PlanStatus.HUMAN_REQUIRED
                else ExecutionStatus.BLOCKED
            )
            return self._blocked(
                plan,
                task.task_id,
                repository,
                started,
                execution_id,
                str(exc),
                exc.evidence,
                status,
            )
        except ExecutionValidationError as exc:
            return self._blocked(
                plan, task.task_id, repository, started, execution_id, str(exc), []
            )

        invocation_label = f"adapter invoked: {self.adapter.name}"
        if dry_run:
            evidence = [
                *gate_evidence,
                f"{invocation_label}: no",
                "task validation commands executed: no",
                "caller repository changes applied: no",
                "cleanup: not applicable (dry-run)",
            ]
            return ExecutionResult(
                execution_id,
                plan.plan_id,
                task.task_id,
                self.adapter.name,
                started,
                _now(),
                context.root,
                context.branch,
                f"dry-run: {self.adapter.name} was not invoked",
                None,
                ExecutionStatus.DRY_RUN,
                [],
                task.validation_commands,
                "",
                "",
                evidence,
                [],
            )

        worktree: str | None = None
        process: CodexProcessResult | None = None
        changed: list[str] = []
        evidence = [
            *gate_evidence,
            f"{invocation_label}: pending",
            "caller repository changes applied: no",
        ]
        blockers: list[str] = []
        status = ExecutionStatus.FAILED
        try:
            worktree = _create_worktree(context.root, context.head_sha)
            evidence.append("isolated worktree: temporary path redacted")
            before = _status_lines(worktree)
            instruction = self.adapter.build_instruction(
                repository=worktree,
                task_id=task.task_id,
                title=task.title,
                objective=task.objective,
                allowed_paths=allowed_paths,
                acceptance_criteria=task.acceptance_criteria,
                validation_commands=task.validation_commands,
                stop_conditions=["scope expansion", "missing context", "architecture uncertainty"],
            )
            process = self.adapter.execute(instruction, worktree)
            evidence.append(f"{invocation_label}: yes")
            if self.adapter.name == "openhands":
                if getattr(self.adapter, "uses_typed_events", False):
                    evidence.extend(
                        [
                            process.stdout_summary,
                            "OpenHands selected transport: sdk-callback",
                            "OpenHands terminal event found: "
                            f"{'yes' if process.transport_error is None else 'no'}",
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
            changed = _changed_paths(before, after)
            if process.human_required:
                status = ExecutionStatus.HUMAN_REQUIRED
                blockers.append(
                    process.transport_error
                    or (
                        "Codex invocation syntax could not be verified"
                        if self.adapter.name == "codex"
                        else "adapter invocation could not be verified"
                    )
                )
            elif process.transport_error and self.adapter.name == "openhands":
                blockers.append(process.transport_error)
                if process.transport_error in {
                    "OPENHANDS_CONFIGURATION_REQUIRED",
                    "OPENHANDS_INTERACTION_REQUIRED",
                    "OPENHANDS_CONTRADICTORY_TERMINAL_STATE",
                    "OPENHANDS_NO_TERMINAL_STATE",
                    "OPENHANDS_JSON_INVALID",
                    "OPENHANDS_STRUCTURED_OUTPUT_MISSING",
                    "OPENHANDS_STRUCTURED_OUTPUT_CONFLICT",
                    "OPENHANDS_STDERR_EVENT_STREAM_INVALID",
                    "OPENHANDS_MACHINE_INTERFACE_UNAVAILABLE",
                    "OPENHANDS_SDK_INITIALIZATION_FAILED",
                    "OPENHANDS_EVENT_STREAM_MISSING",
                    "OPENHANDS_EVENT_STREAM_CONFLICT",
                }:
                    status = ExecutionStatus.HUMAN_REQUIRED
                else:
                    status = ExecutionStatus.FAILED
            elif process.timed_out or process.exit_code != 0:
                blockers.append("Codex process did not complete successfully")
                evidence.append("process exit code preserved")
                if process.timed_out:
                    evidence.append("Codex timeout reached")
            elif not changed:
                blockers.append("Codex produced no changed files")
                if self.adapter.name == "openhands":
                    blockers[-1] = "OPENHANDS_NO_CHANGES"
            else:
                unauthorized = [path for path in changed if not _path_allowed(path, allowed_paths)]
                evidence.append("changed-file scope checked")
                if unauthorized:
                    blockers.append(f"unauthorized changed paths: {', '.join(unauthorized)}")
                else:
                    validation_evidence, validations_passed, validation_blockers = _run_validations(
                        task.validation_commands, worktree, self.validation_timeout
                    )
                    evidence.extend(validation_evidence)
                    blockers.extend(validation_blockers)
                    evidence.append(f"validations passed: {'yes' if validations_passed else 'no'}")
                    if not validations_passed and not validation_blockers:
                        blockers.append("one or more approved validations failed")
                    if validations_passed:
                        status = ExecutionStatus.COMPLETED
                        evidence.append("validated changes remain unapplied to caller repository")
        except (OSError, subprocess.CalledProcessError) as exc:
            blockers.append(f"isolated execution failed: {redact_secrets(str(exc))}")
        finally:
            cleanup_success = True
            if worktree is not None:
                cleanup_success = _remove_worktree(context.root, worktree)
            evidence.append(f"cleanup succeeded: {'yes' if cleanup_success else 'no'}")
            if not cleanup_success:
                blockers.append("temporary worktree cleanup failed")
            try:
                caller_clean = not _status_lines(context.root)
            except (OSError, subprocess.CalledProcessError) as exc:
                caller_clean = False
                blockers.append(
                    f"caller repository status could not be verified: {redact_secrets(str(exc))}"
                )
            evidence.append(f"caller repository clean: {'yes' if caller_clean else 'no'}")
            if not caller_clean:
                blockers.append("caller repository was modified unexpectedly")
            if status is ExecutionStatus.COMPLETED and (not cleanup_success or not caller_clean):
                status = ExecutionStatus.FAILED
        if process is None:
            process = CodexProcessResult("codex not invoked", None, "", "")
        return self._result(
            plan,
            task,
            context.root,
            context.branch,
            started,
            execution_id,
            process,
            status,
            changed,
            task.validation_commands,
            evidence,
            blockers,
        )

    def _blocked(
        self,
        plan,
        task_id,
        repository,
        started,
        execution_id,
        reason,
        evidence,
        status=ExecutionStatus.BLOCKED,
    ):
        return ExecutionResult(
            execution_id,
            plan.plan_id,
            task_id,
            self.adapter.name,
            started,
            _now(),
            repository,
            plan.repository.branch,
            "not invoked",
            None,
            status,
            [],
            [],
            "",
            "",
            evidence,
            [reason],
        )

    def _result(
        self,
        plan,
        task,
        repository,
        branch,
        started,
        execution_id,
        process,
        status,
        changed,
        validations,
        evidence,
        blockers,
    ):
        return ExecutionResult(
            execution_id,
            plan.plan_id,
            task.task_id,
            self.adapter.name,
            started,
            _now(),
            repository,
            branch,
            process.command_summary,
            process.exit_code,
            status,
            changed,
            validations,
            process.stdout_summary,
            process.stderr_summary,
            evidence,
            blockers,
        )


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    candidate = PurePosixPath(path.replace("\\", "/"))
    return any(
        candidate == PurePosixPath(allowed) or PurePosixPath(allowed) in candidate.parents
        for allowed in allowed_paths
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
