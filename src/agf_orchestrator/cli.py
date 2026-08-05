"""Command-line interface for the Director Runtime MVP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from .adapters.codex import CodexAdapter
from .adapters.openhands import OpenHandsSDKAdapter
from .compliance import ComplianceChecker
from .constitution import ConstitutionAuthority, ConstitutionVerificationError
from .delivery import DeliveryPipeline, write_delivery_report
from .director import Director
from .execution_models import ExecutionStatus
from .executor import ExecutionValidationError, Executor, load_plan, write_execution_result
from .git_delivery import DraftPRCreator
from .inbox import build_inbox
from .locking import LockError
from .models import PlanStatus
from .preflight import PreflightError, collect_repository
from .project_models import ProjectPolicy
from .project_registry import ProjectRegistry, ProjectRegistryError, parse_remote_url
from .reviewer import CodexReviewerAdapter, DeterministicReviewer
from .session_manager import SessionManager, SessionManagerError
from .session_store import SessionStore, SessionStoreError

AGF_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def load_cli_environment() -> None:
    """Load only an approved AGF .env without overriding process environment values."""
    configured = os.environ.get("AGF_ENV_FILE")
    selected = Path(configured).expanduser() if configured else AGF_PACKAGE_ROOT / ".env"
    if not _approved_dotenv_path(selected, allow_registry_failure=not configured):
        return
    load_dotenv(dotenv_path=selected.resolve(), override=False)


def _approved_dotenv_path(selected: Path, *, allow_registry_failure: bool = False) -> bool:
    """Validate a dotenv path without exposing its contents or following escapes."""
    candidate = selected if selected.is_absolute() else Path.cwd() / selected
    if _contains_symlink(candidate):
        return False
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return False
    if not resolved.is_file() or not candidate.exists():
        return False
    try:
        managed_roots = [
            Path(project.repository_root).resolve()
            for project in ProjectRegistry().list()
        ]
    except (OSError, ProjectRegistryError, ValueError):
        return allow_registry_failure
    return not any(
        resolved == root or root in resolved.parents
        for root in managed_roots
    )


def _contains_symlink(path: Path) -> bool:
    """Reject symlink components so dotenv cannot escape an approved location."""
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agf-orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="create a validated execution plan")
    plan.add_argument("--repository", help="path to the target Git repository")
    plan.add_argument("--project", help="registered project name or ID")
    plan.add_argument("--goal", required=True, help="high-level project goal")
    plan.add_argument("--output", required=True, help="JSON output path")
    plan.add_argument("--allow-dirty", action="store_true", help="allow a dirty target repository")
    execute = commands.add_parser("execute", help="execute one approved task under safety gates")
    execute.add_argument("--plan", required=True, help="validated execution plan JSON")
    execute.add_argument("--task", required=True, help="selected task ID")
    execute.add_argument("--repository", help="target Git repository")
    execute.add_argument("--project", help="registered project name or ID")
    execute.add_argument("--adapter", choices=["codex", "openhands"], default="codex")
    execute.add_argument("--allow-openhands-llm-env", action="store_true")
    execute.add_argument("--dry-run", action="store_true", help="explicitly request dry-run")
    execute.add_argument("--execute", action="store_true", help="allow live execution")
    execute.add_argument(
        "--confirm-execution", action="store_true", help="confirm live execution explicitly"
    )
    execute.add_argument("--codex-path", default=None, help="Codex executable path")
    execute.add_argument("--openhands-path", default="openhands", help="OpenHands executable path")
    execute.add_argument("--timeout", type=float, default=300.0, help="Codex timeout in seconds")
    execute.add_argument("--output", help="optional report path outside the target repository")
    deliver = commands.add_parser("deliver", help="run the autonomous delivery pipeline")
    deliver.add_argument("--plan", required=True)
    deliver.add_argument("--task", required=True)
    deliver.add_argument("--repository")
    deliver.add_argument("--project", help="registered project name or ID")
    deliver.add_argument("--adapter", choices=["codex", "openhands"], default="codex")
    deliver.add_argument("--allow-openhands-llm-env", action="store_true")
    deliver.add_argument("--output", required=True)
    deliver.add_argument("--execute", action="store_true")
    deliver.add_argument("--confirm-execution", action="store_true")
    deliver.add_argument("--confirm-delivery", action="store_true")
    deliver.add_argument("--reviewer", choices=["deterministic", "codex"], default="deterministic")
    deliver.add_argument("--simulate-pr", action="store_true")
    deliver.add_argument("--codex-path", default=None)
    deliver.add_argument("--openhands-path", default="openhands")
    deliver.add_argument("--timeout", type=float, default=300.0)
    project = commands.add_parser("project", help="manage explicitly registered projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_add = project_commands.add_parser("add")
    project_add.add_argument("--name", required=True)
    project_add.add_argument("--repository", required=True)
    project_add.add_argument("--allow-dirty-planning", action="store_true")
    project_add.add_argument("--allow-live-execution", action="store_true")
    project_add.add_argument("--allow-delivery", action="store_true")
    project_add.add_argument("--no-human-merge", action="store_true")
    project_add.add_argument("--maximum-correction-rounds", type=int, default=2)
    project_add.add_argument("--allowed-remote-host", action="append", default=[])
    project_add.add_argument("--json", action="store_true")
    for command in ("list", "show", "verify", "remove"):
        item = project_commands.add_parser(command)
        if command != "list":
            item.add_argument("--project", required=True)
        item.add_argument("--json", action="store_true")
    session = commands.add_parser("session", help="manage persistent workflow sessions")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_start = session_commands.add_parser("start")
    session_start.add_argument("--project", required=True)
    session_start.add_argument("--goal", required=True)
    session_start.add_argument("--json", action="store_true")
    for command in ("list",):
        item = session_commands.add_parser(command)
        item.add_argument("--json", action="store_true")
    for command in ("show", "resume", "cancel"):
        item = session_commands.add_parser(command)
        item.add_argument("--session", required=True)
        item.add_argument("--json", action="store_true")
    resume = session_commands.choices["resume"]
    resume.add_argument("--project", help="registered project name or ID")
    resume.add_argument("--execute", action="store_true")
    resume.add_argument("--confirm-execution", action="store_true")
    resume.add_argument("--confirm-delivery", action="store_true")
    lock = session_commands.add_parser("lock-status")
    lock.add_argument("--session", required=True)
    lock.add_argument("--json", action="store_true")
    inbox = commands.add_parser("inbox", help="show only items requiring attention")
    inbox.add_argument("--json", action="store_true")
    return parser


def _output(value, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    elif isinstance(value, list):
        for item in value:
            print(item)
    else:
        print(value)


def _resolve_project(args: argparse.Namespace, *, verify: bool = True):
    registry = ProjectRegistry()
    selected = registry.get(args.project) if getattr(args, "project", None) else None
    requested = (
        Path(args.repository).expanduser().resolve() if getattr(args, "repository", None) else None
    )
    if requested is not None:
        try:
            context = collect_repository(requested, allow_dirty=True)
            canonical = Path(context.root).resolve()
        except PreflightError as exc:
            raise ProjectRegistryError(f"repository selection failed: {exc}") from exc
        if requested != canonical:
            raise ProjectRegistryError("repository path must be the canonical repository root")
        projects = registry.list()
        nested = [p for p in projects if canonical in Path(p.repository_root).resolve().parents]
        if nested:
            raise ProjectRegistryError("repository path is nested inside a registered project")
        matches = [p for p in projects if Path(p.repository_root).resolve() == canonical]
        if len(matches) != 1:
            raise ProjectRegistryError("repository path must match exactly one registered project")
        if selected and selected.project_id != matches[0].project_id:
            raise ProjectRegistryError("repository does not match the selected project")
        selected = matches[0]
    if selected is None:
        raise ProjectRegistryError("an exactly one registered project selection is required")
    if verify:
        selected = registry.verify(selected.project_id)
    if selected.status.value != "ACTIVE":
        raise ProjectRegistryError(f"project status is {selected.status.value}")
    return selected, Path(selected.repository_root).resolve()


def _validate_plan_project(plan, project, repository: Path) -> None:
    context = plan.repository
    if Path(context.root).resolve() != repository:
        raise ProjectRegistryError("plan repository root does not match the registered project")
    if parse_remote_url(context.origin).identity != parse_remote_url(project.origin_url).identity:
        raise ProjectRegistryError("plan origin does not match the registered project")
    if context.branch != project.default_branch:
        raise ProjectRegistryError("plan branch does not match the registered project")
    if context.head_sha != project.current_head_sha:
        raise ProjectRegistryError("plan HEAD does not match the verified project HEAD")


def _dirty_policy(project, repository: Path, allow_dirty: bool) -> None:
    dirty = bool(collect_repository(repository, allow_dirty=True).clean is False)
    if dirty and (not allow_dirty or not project.policy.allow_dirty_planning):
        raise ProjectRegistryError(
            "dirty planning requires --allow-dirty and project policy allow_dirty_planning"
        )


def _verify_constitution(project) -> None:
    try:
        ConstitutionAuthority().resolve(project.project_id)
    except ConstitutionVerificationError as exc:
        raise ProjectRegistryError(str(exc)) from exc


def run_project(args: argparse.Namespace) -> int:
    registry = ProjectRegistry()
    try:
        if args.project_command == "add":
            project = registry.add(
                args.name,
                args.repository,
                policy=ProjectPolicy(
                    allowed_remote_hosts=args.allowed_remote_host,
                    allow_dirty_planning=args.allow_dirty_planning,
                    allow_live_execution=args.allow_live_execution,
                    allow_delivery=args.allow_delivery,
                    require_human_merge=not args.no_human_merge,
                    maximum_correction_rounds=args.maximum_correction_rounds,
                ),
            )
            _output(project.to_dict(), args.json)
        elif args.project_command == "list":
            _output([p.to_dict() for p in registry.list()], args.json)
        elif args.project_command == "show":
            _output(registry.get(args.project).to_dict(), args.json)
        elif args.project_command == "verify":
            _output(registry.verify(args.project).to_dict(), args.json)
        elif args.project_command == "remove":
            registry.remove(args.project)
            _output({"removed": args.project}, args.json)
        return 0
    except (ProjectRegistryError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_session(args: argparse.Namespace) -> int:
    manager = SessionManager()
    try:
        if args.session_command == "start":
            session = manager.start(args.project, args.goal)
            _output(session.to_dict(), args.json)
        elif args.session_command == "list":
            _output([s.to_dict() for s in manager.list()], args.json)
        elif args.session_command == "show":
            _output(manager.get(args.session).to_dict(), args.json)
        elif args.session_command == "resume":
            session = manager.resume(
                args.session,
                project_name=args.project,
                execute=args.execute,
                confirm_execution=args.confirm_execution,
                confirm_delivery=args.confirm_delivery,
            )
            _output(session.to_dict(), args.json)
        elif args.session_command == "cancel":
            _output(manager.cancel(args.session).to_dict(), args.json)
        elif args.session_command == "lock-status":
            _output(manager.lock_status(args.session), args.json)
        return 0
    except (
        SessionManagerError,
        ProjectRegistryError,
        SessionStoreError,
        LockError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_inbox(args: argparse.Namespace) -> int:
    try:
        items = build_inbox(SessionStore(), ProjectRegistry())
        if args.json:
            _output([item.to_dict() for item in items], True)
        else:
            for item in items:
                print(f"{item.priority} {item.project}/{item.session_id}: {item.required_action}")
        return 0
    except (ProjectRegistryError, SessionStoreError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _write_plan(plan, output: str, repository_root: str) -> None:
    target = Path(output).expanduser().resolve()
    root = Path(repository_root).resolve()
    if target == root or root in target.parents:
        raise ValueError("output must not be written inside the target repository")
    serialized = json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
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


def run_plan(args: argparse.Namespace) -> int:
    try:
        project, target = _resolve_project(args)
        _dirty_policy(project, target, args.allow_dirty)
        repository = collect_repository(target, allow_dirty=True)
        repository = replace(
            repository,
            branch=project.default_branch,
            origin=project.origin_url,
            head_sha=project.current_head_sha,
        )
        plan = Director().create_plan(args.goal, repository)
        _write_plan(plan, args.output, repository.root)
    except (ProjectRegistryError, PreflightError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if plan.status is PlanStatus.HUMAN_REQUIRED:
        print("HUMAN_REQUIRED: plan written; clarify the goal before execution", file=sys.stderr)
        return 3
    print(f"plan written: {args.output}")
    return 0


def run_execute(args: argparse.Namespace) -> int:
    if args.allow_openhands_llm_env and args.adapter != "openhands":
        print("ERROR: --allow-openhands-llm-env requires --adapter openhands", file=sys.stderr)
        return 2
    if args.execute != args.confirm_execution:
        print("ERROR: --execute and --confirm-execution must be supplied together", file=sys.stderr)
        return 2
    if args.execute and args.dry_run:
        print("ERROR: --dry-run cannot be combined with live execution", file=sys.stderr)
        return 2
    try:
        plan = load_plan(args.plan)
        project, target_root = _resolve_project(args)
        _validate_plan_project(plan, project, target_root)
        if args.execute and not project.policy.allow_live_execution:
            raise ProjectRegistryError("project policy denies live execution")
        if args.execute:
            _verify_constitution(project)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            if output == target_root or target_root in output.parents:
                raise ExecutionValidationError(
                    "execution report must not be written inside the target repository"
                )
        adapter = (
            OpenHandsSDKAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=args.allow_openhands_llm_env,
            )
            if args.adapter == "openhands"
            else CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        )
        result = Executor(adapter=adapter).execute(
            plan, args.task, str(target_root), dry_run=not args.execute
        )
        if args.output:
            write_execution_result(result, args.output)
        else:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    except (
        ProjectRegistryError,
        ExecutionValidationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if result.status in {
        ExecutionStatus.BLOCKED,
        ExecutionStatus.FAILED,
        ExecutionStatus.HUMAN_REQUIRED,
    }:
        return 2
    return 0


def run_deliver(args: argparse.Namespace) -> int:
    if args.allow_openhands_llm_env and args.adapter != "openhands":
        print("ERROR: --allow-openhands-llm-env requires --adapter openhands", file=sys.stderr)
        return 2
    live_flags = (args.execute, args.confirm_execution, args.confirm_delivery)
    if any(live_flags) and not all(live_flags):
        print(
            "ERROR: --execute, --confirm-execution, and --confirm-delivery "
            "must be supplied together",
            file=sys.stderr,
        )
        return 2
    try:
        plan = load_plan(args.plan)
        project, target_root = _resolve_project(args)
        _validate_plan_project(plan, project, target_root)
        if args.execute:
            if not project.policy.allow_live_execution or not project.policy.allow_delivery:
                raise ProjectRegistryError("project policy denies live delivery")
            if not project.policy.require_human_merge:
                raise ProjectRegistryError("delivery requires human merge approval")
            _verify_constitution(project)
        output = Path(args.output).expanduser().resolve()
        if output == target_root or target_root in output.parents:
            raise ExecutionValidationError(
                "delivery report must not be written inside the target repository"
            )
        adapter = (
            OpenHandsSDKAdapter(
                executable=args.openhands_path,
                timeout=args.timeout,
                allow_llm_env=args.allow_openhands_llm_env,
            )
            if args.adapter == "openhands"
            else CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        )
        reviewer = (
            CodexReviewerAdapter(CodexAdapter(executable=args.codex_path, timeout=args.timeout))
            if args.reviewer == "codex"
            else DeterministicReviewer()
        )
        pipeline = DeliveryPipeline(
            adapter=adapter,
            reviewer=reviewer,
            compliance=ComplianceChecker(),
            pr_creator=DraftPRCreator(simulate=args.simulate_pr),
            max_correction_rounds=project.policy.maximum_correction_rounds,
        )
        report = pipeline.deliver(plan, args.task, str(target_root), execute=args.execute)
        write_delivery_report(report, output)
    except (
        ProjectRegistryError,
        ExecutionValidationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if report.status in {"BLOCKED", "FAILED", "HUMAN_REQUIRED"}:
        return 2
    print(f"delivery report written: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_cli_environment()
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    if args.command == "execute":
        return run_execute(args)
    if args.command == "deliver":
        return run_deliver(args)
    if args.command == "project":
        return run_project(args)
    if args.command == "session":
        return run_session(args)
    if args.command == "inbox":
        return run_inbox(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
