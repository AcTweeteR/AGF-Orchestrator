"""Command-line interface for the Director Runtime MVP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .director import Director
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return run_plan(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
