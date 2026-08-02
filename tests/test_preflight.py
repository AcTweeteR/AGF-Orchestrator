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


def init_repo(tmp_path, *, add_origin=True):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("test\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-m", "initial")
    if add_origin:
        git(tmp_path, "remote", "add", "origin", "https://example.invalid/repo.git")


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


def test_missing_origin_rejected(tmp_path):
    init_repo(tmp_path, add_origin=False)
    with pytest.raises(PreflightError, match="origin remote is required"):
        collect_repository(tmp_path)


def test_detached_head_rejected(tmp_path):
    init_repo(tmp_path)
    git(tmp_path, "checkout", "--detach")
    with pytest.raises(PreflightError, match="detached HEAD"):
        collect_repository(tmp_path)


def test_head_resolution_failure_is_actionable(tmp_path, monkeypatch):
    init_repo(tmp_path)
    from agf_orchestrator import preflight

    original_git = preflight._git

    def fail_head(path, *args):
        if args == ("rev-parse", "HEAD"):
            raise PreflightError("HEAD cannot be resolved")
        return original_git(path, *args)

    monkeypatch.setattr(preflight, "_git", fail_head)
    with pytest.raises(PreflightError, match="HEAD cannot be resolved"):
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
