import subprocess

import pytest

from agf_orchestrator.preflight import DirtyRepositoryError, PreflightError, collect_repository


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def init_repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("test\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-m", "initial")


def test_clean_git_repository(tmp_path):
    init_repo(tmp_path)
    context = collect_repository(tmp_path)
    assert context.root == str(tmp_path)
    assert context.branch == "main"
    assert context.clean is True
    assert len(context.head_sha) == 40


def test_non_git_directory_rejected(tmp_path):
    with pytest.raises(PreflightError):
        collect_repository(tmp_path)


def test_dirty_repository_blocked_by_default(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("dirty\n")
    with pytest.raises(DirtyRepositoryError):
        collect_repository(tmp_path)


def test_dirty_repository_allowed_explicitly(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("dirty\n")
    context = collect_repository(tmp_path, allow_dirty=True)
    assert context.clean is False
