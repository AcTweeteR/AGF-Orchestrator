import json
import os
import subprocess
import sys
from pathlib import Path


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


def run_cli(repo, output, goal):
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    return subprocess.run(
        [
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
        ],
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
