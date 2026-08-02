import json
import os
import subprocess
import sys
from pathlib import Path

from agf_orchestrator import cli
from agf_orchestrator.models import PlanValidationError


def init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", "https://example.invalid/repo.git"],
        check=True,
    )


def run_cli(repo, output, goal, *, allow_dirty=False):
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    command = [
        sys.executable,
        "-m",
        "agf_orchestrator.cli",
        "plan",
        "--repository",
        str(repo),
        "--goal",
        goal,
        "--output",
        str(output),
    ]
    if allow_dirty:
        command.append("--allow-dirty")
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_cli_success_and_deterministic_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert run_cli(repo, first, "Build a bounded feature").returncode == 0
    assert run_cli(repo, second, "Build a bounded feature").returncode == 0
    assert json.loads(first.read_text())["status"] == "READY"
    assert first.read_text() == second.read_text()


def test_cli_failure_for_non_git_directory(tmp_path):
    output = tmp_path / "plan.json"
    result = run_cli(tmp_path, output, "Build a bounded feature")
    assert result.returncode != 0
    assert not output.exists()


def test_cli_ambiguous_goal_requires_human(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    output = tmp_path / "plan.json"
    result = run_cli(repo, output, "fix it")
    assert result.returncode != 0
    assert json.loads(output.read_text())["status"] == "HUMAN_REQUIRED"


def test_cli_rejects_output_at_repository_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    result = run_cli(repo, repo, "Build a bounded feature")
    assert result.returncode != 0


def test_cli_rejects_output_nested_in_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    output = repo / "nested" / "plan.json"
    result = run_cli(repo, output, "Build a bounded feature")
    assert result.returncode != 0
    assert not output.exists()
    assert not output.parent.exists()


def test_cli_dirty_repository_requires_override_and_records_risk(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "README.md").write_text("dirty\n")
    blocked_output = tmp_path / "blocked.json"
    assert run_cli(repo, blocked_output, "Build a bounded feature").returncode != 0
    assert not blocked_output.exists()
    allowed_output = tmp_path / "allowed.json"
    assert (
        run_cli(repo, allowed_output, "Build a bounded feature", allow_dirty=True).returncode == 0
    )
    payload = json.loads(allowed_output.read_text())
    assert payload["repository"]["clean"] is False
    assert any("dirty" in risk for risk in payload["risks"])
    assert any("uncommitted" in evidence for evidence in payload["required_evidence"])


def test_cli_invalid_plan_leaves_no_partial_output(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    output = tmp_path / "invalid.json"

    class InvalidDirector:
        def create_plan(self, goal, repository):
            raise PlanValidationError("invalid generated plan")

    monkeypatch.setattr(cli, "Director", InvalidDirector)
    args = cli.build_parser().parse_args(
        [
            "plan",
            "--repository",
            str(repo),
            "--goal",
            "Build a bounded feature",
            "--output",
            str(output),
        ]
    )
    assert cli.run_plan(args) != 0
    assert not output.exists()
