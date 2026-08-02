import json
import os
import subprocess
import sys
from pathlib import Path

from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )


def init_repo(tmp_path):
    git(tmp_path, "init", "-b", "feature")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "allowed.txt").write_text("before\n")
    git(tmp_path, "add", "allowed.txt")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "remote", "add", "origin", "https://example.invalid/repo.git")


def write_plan(repo, path):
    task = Task(
        "task-001",
        "Update allowed file",
        "Update allowed.txt",
        ["allowed.txt"],
        [],
        ["allowed file contains the new value"],
        ["git diff --check -- allowed.txt"],
        "low",
        "Implementer",
        PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0",
        "plan-cli",
        "1970-01-01T00:00:00Z",
        RepositoryContext(
            str(repo),
            "feature",
            "https://example.invalid/repo.git",
            True,
            git(repo, "rev-parse", "HEAD").stdout.strip(),
        ),
        "Update the file",
        {"in": ["allowed.txt"], "out": []},
        [],
        [],
        {"status": "approved", "requires_architect": False},
        [task],
        [],
        [["task-001"]],
        ["Reviewer"],
        ["task outcome"],
        [],
        PlanStatus.READY,
    )
    plan.validate()
    path.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")


def run_cli(repo, plan, *extra):
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "AGF_STATE_DIR": str(plan.parent / f"agf-state-{plan.stem}"),
    }
    register = subprocess.run(
        [
            sys.executable,
            "-m",
            "agf_orchestrator.cli",
            "project",
            "add",
            "--name",
            repo.name,
            "--repository",
            str(repo),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert register.returncode in (0, 2), register.stderr
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agf_orchestrator.cli",
            "execute",
            "--plan",
            str(plan),
            "--task",
            "task-001",
            "--repository",
            str(repo),
            "--adapter",
            "codex",
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_execute_defaults_to_dry_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    plan = tmp_path / "plan.json"
    write_plan(repo, plan)
    output = tmp_path / "execution.json"
    result = run_cli(repo, plan, "--codex-path", str(tmp_path / "missing"), "--output", str(output))
    assert result.returncode == 0
    assert json.loads(output.read_text())["status"] == "DRY_RUN"


def test_cli_requires_both_live_confirmation_flags(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    plan = tmp_path / "plan.json"
    write_plan(repo, plan)
    assert run_cli(repo, plan, "--execute").returncode != 0
    assert run_cli(repo, plan, "--confirm-execution").returncode != 0


def test_cli_invalid_plan_is_nonzero_without_report(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    plan = tmp_path / "invalid.json"
    plan.write_text('{"status": "READY"}\n')
    output = tmp_path / "execution.json"
    result = run_cli(repo, plan, "--output", str(output))
    assert result.returncode != 0
    assert not output.exists()


def test_cli_execute_rejects_openhands_env_opt_in_for_codex(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    plan = tmp_path / "plan.json"
    write_plan(repo, plan)
    result = run_cli(repo, plan, "--allow-openhands-llm-env")
    assert result.returncode != 0
    assert "requires --adapter openhands" in result.stderr
