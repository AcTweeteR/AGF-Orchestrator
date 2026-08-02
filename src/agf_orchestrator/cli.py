"""Command-line interface for the Director Runtime MVP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .adapters.codex import CodexAdapter
from .compliance import ComplianceChecker
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
from .project_registry import ProjectRegistry, ProjectRegistryError
from .reviewer import CodexReviewerAdapter, DeterministicReviewer
from .session_manager import SessionManager, SessionManagerError
from .session_store import SessionStore, SessionStoreError


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
    execute.add_argument("--adapter", choices=["codex"], default="codex")
    execute.add_argument("--dry-run", action="store_true", help="explicitly request dry-run")
    execute.add_argument("--execute", action="store_true", help="allow live execution")
    execute.add_argument(
        "--confirm-execution", action="store_true", help="confirm live execution explicitly"
    )
    execute.add_argument("--codex-path", default="codex", help="Codex executable path")
    execute.add_argument("--timeout", type=float, default=300.0, help="Codex timeout in seconds")
    execute.add_argument("--output", help="optional report path outside the target repository")
    deliver = commands.add_parser("deliver", help="run the autonomous delivery pipeline")
    deliver.add_argument("--plan", required=True)
    deliver.add_argument("--task", required=True)
    deliver.add_argument("--repository")
    deliver.add_argument("--project", help="registered project name or ID")
    deliver.add_argument("--adapter", choices=["codex"], default="codex")
    deliver.add_argument("--output", required=True)
    deliver.add_argument("--execute", action="store_true")
    deliver.add_argument("--confirm-execution", action="store_true")
    deliver.add_argument("--confirm-delivery", action="store_true")
    deliver.add_argument("--reviewer", choices=["deterministic", "codex"], default="deterministic")
    deliver.add_argument("--simulate-pr", action="store_true")
    deliver.add_argument("--codex-path", default="codex")
    deliver.add_argument("--timeout", type=float, default=300.0)
    project = commands.add_parser("project", help="manage explicitly registered projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_add = project_commands.add_parser("add")
    project_add.add_argument("--name", required=True)
    project_add.add_argument("--repository", required=True)
    project_add.add_argument("--allow-dirty-planning", action="store_true")
    project_add.add_argument("--allow-live-execution", action="store_true")
    project_add.add_argument("--allow-delivery", action="store_true")
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


def _target_repository(args: argparse.Namespace) -> str:
    registered = None
    if getattr(args, "project", None):
        registered = ProjectRegistry().get(args.project)
        if registered.status.value != "ACTIVE":
            raise ProjectRegistryError(f"project status is {registered.status.value}")
    if getattr(args, "repository", None):
        repository = str(Path(args.repository).expanduser().resolve())
        if registered and repository != registered.repository_root:
            raise ProjectRegistryError("repository does not match the selected project")
        return repository
    if registered:
        return registered.repository_root
    raise ProjectRegistryError("explicit --project or --repository selection is required")


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
        target = _target_repository(args)
        repository = collect_repository(target, allow_dirty=args.allow_dirty)
        plan = Director().create_plan(args.goal, repository)
        _write_plan(plan, args.output, repository.root)
    except (PreflightError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if plan.status is PlanStatus.HUMAN_REQUIRED:
        print("HUMAN_REQUIRED: plan written; clarify the goal before execution", file=sys.stderr)
        return 3
    print(f"plan written: {args.output}")
    return 0


def run_execute(args: argparse.Namespace) -> int:
    if args.execute != args.confirm_execution:
        print("ERROR: --execute and --confirm-execution must be supplied together", file=sys.stderr)
        return 2
    if args.execute and args.dry_run:
        print("ERROR: --dry-run cannot be combined with live execution", file=sys.stderr)
        return 2
    try:
        plan = load_plan(args.plan)
        target_root = Path(_target_repository(args))
        if args.output:
            output = Path(args.output).expanduser().resolve()
            if output == target_root or target_root in output.parents:
                raise ExecutionValidationError(
                    "execution report must not be written inside the target repository"
                )
        adapter = CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        result = Executor(adapter=adapter).execute(
            plan, args.task, str(target_root), dry_run=not args.execute
        )
        if args.output:
            write_execution_result(result, args.output)
        else:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    except (ExecutionValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
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
        target_root = Path(_target_repository(args))
        output = Path(args.output).expanduser().resolve()
        if output == target_root or target_root in output.parents:
            raise ExecutionValidationError(
                "delivery report must not be written inside the target repository"
            )
        adapter = CodexAdapter(executable=args.codex_path, timeout=args.timeout)
        reviewer = (
            CodexReviewerAdapter(adapter) if args.reviewer == "codex" else DeterministicReviewer()
        )
        pipeline = DeliveryPipeline(
            adapter=adapter,
            reviewer=reviewer,
            compliance=ComplianceChecker(),
            pr_creator=DraftPRCreator(simulate=args.simulate_pr),
        )
        report = pipeline.deliver(plan, args.task, str(target_root), execute=args.execute)
        write_delivery_report(report, output)
    except (ExecutionValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if report.status in {"BLOCKED", "FAILED", "HUMAN_REQUIRED"}:
        return 2
    print(f"delivery report written: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
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
