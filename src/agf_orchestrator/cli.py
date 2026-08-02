"""Command-line interface for the Director Runtime MVP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .adapters.codex import CodexAdapter
from .director import Director
from .execution_models import ExecutionStatus
from .executor import ExecutionValidationError, Executor, load_plan, write_execution_result
from .models import PlanStatus
from .preflight import PreflightError, collect_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agf-orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="create a validated execution plan")
    plan.add_argument("--repository", required=True, help="path to the target Git repository")
    plan.add_argument("--goal", required=True, help="high-level project goal")
    plan.add_argument("--output", required=True, help="JSON output path")
    plan.add_argument("--allow-dirty", action="store_true", help="allow a dirty target repository")
    execute = commands.add_parser("execute", help="execute one approved task under safety gates")
    execute.add_argument("--plan", required=True, help="validated execution plan JSON")
    execute.add_argument("--task", required=True, help="selected task ID")
    execute.add_argument("--repository", required=True, help="target Git repository")
    execute.add_argument("--adapter", choices=["codex"], default="codex")
    execute.add_argument("--dry-run", action="store_true", help="explicitly request dry-run")
    execute.add_argument("--execute", action="store_true", help="allow live execution")
    execute.add_argument(
        "--confirm-execution", action="store_true", help="confirm live execution explicitly"
    )
    execute.add_argument("--codex-path", default="codex", help="Codex executable path")
    execute.add_argument("--timeout", type=float, default=300.0, help="Codex timeout in seconds")
    execute.add_argument("--output", help="optional report path outside the target repository")
    return parser


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
        repository = collect_repository(args.repository, allow_dirty=args.allow_dirty)
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
        target_root = Path(args.repository).expanduser().resolve()
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    if args.command == "execute":
        return run_execute(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
