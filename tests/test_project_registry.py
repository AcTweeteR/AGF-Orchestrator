import os
import subprocess

import pytest

from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import ProjectRegistry, ProjectRegistryError


def repo(tmp_path, name="repo", origin_name="origin.git"):
    root = tmp_path / name
    origin = tmp_path / origin_name
    root.mkdir()
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "feature", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    (root / "file.txt").write_text("before")
    subprocess.run(["git", "-C", str(root), "add", "file.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(origin)], check=True)
    return root, origin


def test_add_list_verify_and_policy_are_safe(tmp_path):
    root, _ = repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "state")
    project = registry.add("alpha", root)
    assert project.status is ProjectStatus.ACTIVE
    assert project.policy.allow_live_execution is False
    assert registry.verify("alpha").status is ProjectStatus.ACTIVE


def test_registry_rejects_non_git_duplicate_and_nested_projects(tmp_path):
    root, _ = repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "state")
    registry.add("alpha", root)
    with pytest.raises(ProjectRegistryError):
        registry.add("duplicate", root)
    (root / "child").mkdir()
    with pytest.raises(ProjectRegistryError):
        registry.add("nested", root / "child")


def test_registry_rejects_missing_origin_detached_and_credentials(tmp_path):
    root, _ = repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "state")
    subprocess.run(["git", "-C", str(root), "remote", "remove", "origin"], check=True)
    with pytest.raises(ProjectRegistryError, match="origin"):
        registry.add("missing-origin", root)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://user:pass@example.com/x.git"],
        check=True,
    )
    with pytest.raises(ProjectRegistryError, match="credentials"):
        registry.add("credentials", root)
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--detach"], check=True, capture_output=True
    )
    with pytest.raises(ProjectRegistryError, match="detached"):
        registry.add("detached", root)


def test_registry_rejects_duplicate_origin_and_symlink_path(tmp_path):
    root, origin = repo(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    subprocess.run(["git", "init", "-b", "feature", str(second)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(second), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(second), "config", "user.email", "test@example.invalid"], check=True
    )
    (second / "other.txt").write_text("other")
    subprocess.run(["git", "-C", str(second), "add", "other.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(second), "commit", "-m", "init"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(second), "remote", "add", "origin", str(origin)], check=True)
    registry = ProjectRegistry(tmp_path / "state")
    registry.add("alpha", root)
    with pytest.raises(ProjectRegistryError, match="origin"):
        registry.add("same-origin", second)
    alias = tmp_path / "alias"
    os.symlink(root, alias)
    with pytest.raises(ProjectRegistryError, match="symlink"):
        registry.add("alias", alias)


def test_verify_marks_changed_origin_stale(tmp_path):
    root, _ = repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "state")
    registry.add("alpha", root)
    subprocess.run(
        ["git", "-C", str(root), "remote", "set-url", "origin", "file:///changed"], check=True
    )
    verified = registry.verify("alpha")
    assert verified.status is ProjectStatus.STALE
