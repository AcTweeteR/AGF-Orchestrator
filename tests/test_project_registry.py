import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import (
    ProjectRegistry,
    ProjectRegistryError,
    parse_remote_url,
)


def repo(tmp_path, name="repo", origin_name="origin.git"):
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin.as_uri()], check=True)
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


def test_verify_advancing_head_keeps_project_active_with_bounded_history(tmp_path):
    root, _ = repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "state")
    original = registry.add("alpha", root)
    (root / "file.txt").write_text("after")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-am", "advance"], check=True, capture_output=True
    )
    verified = registry.verify("alpha")
    assert verified.status is ProjectStatus.ACTIVE
    assert verified.current_head_sha != original.current_head_sha
    assert (
        verified.metadata["verification_history"][-1]["previous_sha"] == original.current_head_sha
    )
    for _ in range(8):
        verified = registry.verify("alpha")
    assert len(verified.metadata["verification_history"]) <= 5


def test_remote_parser_accepts_scp_and_rejects_scheme_less_values():
    remote = parse_remote_url("git@github.example.com:owner/repository.git")
    assert remote.host == "github.example.com"
    assert remote.scheme == "ssh"
    assert remote.normalized == "ssh://git@github.example.com/owner/repository.git"
    with pytest.raises(ProjectRegistryError):
        parse_remote_url("relative/path")


def test_concurrent_additions_preserve_both_projects(tmp_path):
    root_a, _ = repo(tmp_path / "a")
    root_b, _ = repo(tmp_path / "b")
    registry = ProjectRegistry(tmp_path / "state")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: registry.add(item[0], item[1]),
                [("alpha", root_a), ("beta", root_b)],
            )
        )
    assert {project.name for project in results} == {"alpha", "beta"}
    assert {project.name for project in registry.list()} == {"alpha", "beta"}
