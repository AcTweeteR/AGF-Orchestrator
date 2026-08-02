import hashlib
import subprocess

import pytest

from agf_orchestrator import git_delivery as git_delivery_module
from agf_orchestrator.git_delivery import GitDelivery, GitDeliveryError, sanitize_branch_name
from agf_orchestrator.models import PlanStatus, Task


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=check,
        capture_output=True, text=True,
    )


def repo(tmp_path):
    bare = tmp_path / "origin.git"
    root = tmp_path / "repo"
    git(tmp_path, "init", "--bare", str(bare)) if False else subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True
    )
    root.mkdir()
    git(root, "init", "-b", "feature")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "allowed.txt").write_text("before\n")
    git(root, "add", "allowed.txt")
    git(root, "commit", "-m", "initial")
    git(root, "remote", "add", "origin", str(bare))
    return root


def task():
    return Task(
        "task-001", "Update allowed", "Update allowed", ["allowed.txt"], [],
        ["allowed is after"], ["python -B -c \"assert True\""],
        "low", "Implementer", PlanStatus.READY,
    )


def patch_file(root, tmp_path):
    (root / "allowed.txt").write_text("after\n")
    patch = tmp_path / "change.patch"
    patch.write_text(git(root, "diff").stdout)
    git(root, "restore", "allowed.txt")
    return patch


def test_branch_name_is_sanitized():
    assert sanitize_branch_name("plan with spaces", "task/one") == "agf/plan-with-spaces/task-one"


def test_delivery_applies_commits_and_pushes_to_local_bare_remote(tmp_path):
    root = repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    patch = patch_file(root, tmp_path)
    result = GitDelivery().deliver(str(root), base, "agf/plan/task-001", str(patch), task())
    assert result.commit_sha
    assert result.push_status == "PUSHED"
    assert result.changed_files == ["allowed.txt"]
    assert git(root, "status", "--porcelain").stdout == ""
    assert git(
        root, "show-ref", "--verify", "refs/remotes/origin/agf/plan/task-001"
    ).returncode == 0


def test_existing_branch_and_base_drift_are_blocked(tmp_path):
    root = repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    patch = patch_file(root, tmp_path)
    git(root, "branch", "agf/plan/task-001")
    with pytest.raises(GitDeliveryError, match="already exists"):
        GitDelivery().deliver(str(root), base, "agf/plan/task-001", str(patch), task())
    root2 = repo(tmp_path / "drift")
    drift_base = "0" * 40
    drift_patch = patch_file(root2, tmp_path / "drift")
    with pytest.raises(GitDeliveryError, match="base SHA drifted"):
        GitDelivery().deliver(str(root2), drift_base, "agf/plan/task-002", str(drift_patch), task())


def test_patch_hash_mismatch_is_rejected(tmp_path):
    root = repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    patch = patch_file(root, tmp_path)
    with pytest.raises(GitDeliveryError, match="hash mismatch"):
        GitDelivery().deliver(
            str(root), base, "agf/plan/task-001", str(patch), task(),
            expected_patch_sha256=hashlib.sha256(b"wrong").hexdigest(),
        )


def test_failed_push_is_reported_and_branch_is_retained(tmp_path, monkeypatch):
    root = repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    patch = patch_file(root, tmp_path)
    original = git_delivery_module._git

    def fail_push(repository, *args, **kwargs):
        if args and args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 1, "", "denied")
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(git_delivery_module, "_git", fail_push)
    with pytest.raises(GitDeliveryError, match="push failed"):
        GitDelivery().deliver(str(root), base, "agf/plan/task-003", str(patch), task())
    assert git(root, "show-ref", "--verify", "refs/heads/agf/plan/task-003").returncode == 0


def test_validation_failure_blocks_commit(tmp_path):
    root = repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    patch = patch_file(root, tmp_path)
    invalid = Task(
        "task-001", "Update allowed", "Update allowed", ["allowed.txt"], [],
        ["allowed is after"], ["false"], "low", "Implementer", PlanStatus.READY,
    )
    with pytest.raises(GitDeliveryError, match="validation failed"):
        GitDelivery().deliver(str(root), base, "agf/plan/task-004", str(patch), invalid)
    branch_sha = git(root, "rev-parse", "refs/heads/agf/plan/task-004").stdout.strip()
    assert branch_sha == base
