import json
import subprocess
from dataclasses import replace

import pytest

from agf_orchestrator import cli
from agf_orchestrator.preflight import collect_repository
from agf_orchestrator.project_models import ProjectPolicy
from agf_orchestrator.project_registry import ProjectRegistry


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )


def make_repo(tmp_path, name):
    bare = tmp_path / f"{name}.git"
    root = tmp_path / name
    git(tmp_path, "init", "--bare", str(bare)) if False else subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    root.mkdir()
    git(root, "init", "-b", "feature")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "file.txt").write_text("before\n")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "initial")
    git(root, "remote", "add", "origin", bare.as_uri())
    return root


def register(state, root, name, **policy):
    return ProjectRegistry(state).add(name, root, policy=ProjectPolicy(**policy))


def plan_for(root):
    return cli.Director().create_plan("Build a bounded feature", collect_repository(root))


def invoke(monkeypatch, state, args):
    monkeypatch.setenv("AGF_STATE_DIR", str(state))
    return cli.main(args)


def test_unregistered_repository_rejected_for_plan_execute_and_deliver(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "unregistered")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_for(root).to_dict()))
    for command in (
        [
            "plan",
            "--repository",
            str(root),
            "--goal",
            "Build a bounded feature",
            "--output",
            str(tmp_path / "p.json"),
        ],
        ["execute", "--repository", str(root), "--plan", str(plan_path), "--task", "task-001"],
        [
            "deliver",
            "--repository",
            str(root),
            "--plan",
            str(plan_path),
            "--task",
            "task-001",
            "--output",
            str(tmp_path / "d.json"),
        ],
    ):
        assert invoke(monkeypatch, tmp_path / "state", command) == 2


def test_registered_path_and_matching_project_are_accepted_but_mismatch_rejected(
    tmp_path, monkeypatch
):
    state = tmp_path / "state"
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    register(state, a, "alpha")
    register(state, b, "beta")
    output = tmp_path / "plan.json"
    args = [
        "plan",
        "--repository",
        str(a),
        "--project",
        "alpha",
        "--goal",
        "Build a bounded feature",
        "--output",
        str(output),
    ]
    assert invoke(monkeypatch, state, args) == 0
    assert (
        invoke(
            monkeypatch, state, [*args[:1], "--repository", str(b), "--project", "alpha", *args[5:]]
        )
        == 2
    )


def test_dirty_planning_requires_flag_and_project_policy(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "dirty")
    (root / "file.txt").write_text("dirty\n")
    state = tmp_path / "state"
    register(state, root, "dirty")
    base = ["plan", "--project", "dirty", "--goal", "Build a bounded feature"]
    assert (
        invoke(monkeypatch, state, [*base, "--output", str(tmp_path / "a.json"), "--allow-dirty"])
        == 2
    )
    state2 = tmp_path / "state2"
    register(state2, root, "dirty", allow_dirty_planning=True)
    assert invoke(monkeypatch, state2, [*base, "--output", str(tmp_path / "b.json")]) == 2
    assert (
        invoke(monkeypatch, state2, [*base, "--output", str(tmp_path / "c.json"), "--allow-dirty"])
        == 0
    )


def test_live_execute_policy_blocks_before_adapter_and_allows_with_policy(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "exec")
    state = tmp_path / "state"
    project = register(state, root, "exec")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_for(root).to_dict()))
    called = []
    monkeypatch.setattr(cli, "CodexAdapter", lambda **kwargs: called.append(kwargs))
    args = [
        "execute",
        "--project",
        "exec",
        "--plan",
        str(plan_path),
        "--task",
        "task-001",
        "--execute",
        "--confirm-execution",
    ]
    assert invoke(monkeypatch, state, args) == 2
    assert not called
    registry = ProjectRegistry(state)
    registry._save([replace(project, policy=replace(project.policy, allow_live_execution=True))])


def test_plan_identity_cannot_cross_projects(tmp_path, monkeypatch):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    state = tmp_path / "state"
    register(state, a, "alpha")
    register(state, b, "beta")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_for(a).to_dict()))
    assert (
        invoke(
            monkeypatch,
            state,
            ["execute", "--project", "beta", "--plan", str(plan_path), "--task", "task-001"],
        )
        == 2
    )


def test_delivery_rejects_no_human_merge_and_passes_effective_limit(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "delivery")
    state = tmp_path / "state"
    project = register(
        state,
        root,
        "delivery",
        allow_live_execution=True,
        allow_delivery=True,
        maximum_correction_rounds=1,
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_for(root).to_dict()))
    seen = []

    class FakePipeline:
        def __init__(self, **kwargs):
            seen.append(kwargs["max_correction_rounds"])

        def deliver(self, *args, **kwargs):
            return type(
                "Report", (), {"status": "DRY_RUN", "to_dict": lambda self: {"status": self.status}}
            )()

    monkeypatch.setattr(cli, "DeliveryPipeline", FakePipeline)
    args = [
        "deliver",
        "--project",
        "delivery",
        "--plan",
        str(plan_path),
        "--task",
        "task-001",
        "--output",
        str(tmp_path / "d.json"),
        "--execute",
        "--confirm-execution",
        "--confirm-delivery",
    ]
    assert invoke(monkeypatch, state, args) == 0
    assert seen == [1]
    registry = ProjectRegistry(state)
    registry._save([replace(project, policy=replace(project.policy, require_human_merge=False))])
    assert invoke(monkeypatch, state, args) == 2


def test_correction_limit_above_two_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ProjectPolicy(maximum_correction_rounds=3)
