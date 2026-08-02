import subprocess

import pytest

from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_manager import SessionManager, SessionManagerError
from agf_orchestrator.session_models import SessionStatus


def registered(tmp_path):
    root, origin = tmp_path / "repo", tmp_path / "origin.git"
    tmp_path.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "feature", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "x").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "x"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(origin)], check=True)
    state = tmp_path / "state"
    registry = ProjectRegistry(state)
    registry.add("alpha", root)
    return root, state


def test_start_is_ready_and_resume_is_idempotent(tmp_path):
    root, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Add contributor link validation")
    assert session.status is SessionStatus.READY
    again = manager.resume(session.session_id)
    assert again.session_id == session.session_id
    assert len(again.events) == 1
    subprocess.run(["git", "-C", str(root), "add", "x"], check=True)
    (root / "x").write_text("drift")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-am", "drift"], check=True, capture_output=True
    )
    stale = manager.resume(session.session_id)
    assert stale.status is SessionStatus.STALE
    assert "base SHA" in stale.blocking_issues[0]


def test_invalid_transition_and_cancel_preserve_events(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Add contributor link validation")
    try:
        manager.transition(session.session_id, SessionStatus.COMPLIANCE)
    except SessionManagerError:
        pass
    else:
        raise AssertionError("invalid transition accepted")
    cancelled = manager.cancel(session.session_id)
    assert cancelled.status is SessionStatus.CANCELLED
    assert len(cancelled.events) == 2


def test_missing_or_changed_artifact_marks_session_stale(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    missing = manager.start("alpha", "Validate contributor links")
    import os

    os.unlink(missing.plan_path)
    assert manager.resume(missing.session_id).status is SessionStatus.STALE

    _, state2 = registered(tmp_path / "second")
    manager2 = SessionManager(state2)
    changed = manager2.start("alpha", "Validate contributor links")
    with open(changed.plan_path, "a", encoding="utf-8") as handle:
        handle.write("tampered")
    assert manager2.resume(changed.session_id).status is SessionStatus.STALE


def test_disabled_project_stales_session_and_terminal_cannot_resume(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    manager.registry.set_status("alpha", ProjectStatus.DISABLED)
    assert manager.resume(session.session_id).status is SessionStatus.STALE
    cancelled = manager.cancel(session.session_id)
    with pytest.raises(SessionManagerError):
        manager.resume(cancelled.session_id)
