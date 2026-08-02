import json
import os
import subprocess
import sys
from pathlib import Path

from test_delivery import plan_for, setup_repo


def run_cli(repo, plan, output, *extra):
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    env["AGF_STATE_DIR"] = str(output.parent / f"agf-state-{output.stem}")
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
            "deliver",
            "--plan",
            str(plan),
            "--task",
            "task-001",
            "--repository",
            str(repo),
            "--adapter",
            "codex",
            "--output",
            str(output),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_deliver_defaults_to_dry_run(tmp_path):
    repo = setup_repo(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(plan_for(repo).to_dict()))
    output = tmp_path / "delivery.json"
    result = run_cli(repo, plan, output)
    assert result.returncode == 0
    assert json.loads(output.read_text())["status"] == "DRY_RUN"


def test_cli_live_delivery_requires_all_confirmation_flags(tmp_path):
    repo = setup_repo(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(plan_for(repo).to_dict()))
    output = tmp_path / "delivery.json"
    result = run_cli(repo, plan, output, "--execute", "--confirm-execution")
    assert result.returncode != 0
    assert not output.exists()
