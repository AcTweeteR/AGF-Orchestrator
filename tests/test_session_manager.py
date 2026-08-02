import subprocess

import pytest

from agf_orchestrator.locking import LockError, project_lock
from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import ProjectRegistry, ProjectRegistryError
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
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin.as_uri()], check=True)
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


def test_invalid_resume_flags_and_project_lock_do_not_mutate_session(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    with pytest.raises(SessionManagerError, match="confirm-execution"):
        manager.resume(session.session_id, confirm_execution=True)
    assert len(manager.get(session.session_id).events) == 1
    project = manager.registry.get("alpha")
    with project_lock(state, project.project_id, "test-holder"):
        with pytest.raises(LockError):
            manager.resume(session.session_id)
    assert len(manager.get(session.session_id).events) == 1


def test_execution_checkpoint_requires_both_policy_permissions(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    manager.registry.set_status("alpha", ProjectStatus.ACTIVE)
    project = manager.registry.get("alpha")
    from dataclasses import replace

    project = replace(project, policy=replace(project.policy, allow_live_execution=True))
    manager.registry._save([project])
    session = manager.start("alpha", "Validate contributor links")
    with pytest.raises(SessionManagerError, match="delivery"):
        manager.resume(
            session.session_id, execute=True, confirm_execution=True, confirm_delivery=True
        )
    assert manager.get(session.session_id).status is SessionStatus.READY
    authorized = manager.resume(session.session_id, execute=True, confirm_execution=True)
    assert authorized.status is SessionStatus.EXECUTING
    assert authorized.execution_report_path is None


def test_project_removal_blocks_nonterminal_and_preserves_terminal_evidence(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    active = manager.start("alpha", "Validate contributor links")
    with pytest.raises(ProjectRegistryError, match="active sessions"):
        manager.registry.remove("alpha")
    manager.cancel(active.session_id)
    manager.registry.remove("alpha")
    assert manager.store.load(active.session_id).status is SessionStatus.CANCELLED


def test_repeated_operation_id_adds_one_event_and_uncertain_save_is_human_required(tmp_path):
    _, state = registered(tmp_path)
    manager = SessionManager(state)
    session = manager.start("alpha", "Validate contributor links")
    first = manager.transition(
        session.session_id,
        SessionStatus.EXECUTING,
        operation_id="operation-1",
        summary="authorization checkpoint",
    )
    second = manager.transition(
        session.session_id,
        SessionStatus.EXECUTING,
        operation_id="operation-1",
        summary="duplicate retry",
    )
    assert len(second.events) == len(first.events)
    manager.store.save = lambda _: (_ for _ in ()).throw(OSError("simulated interrupted save"))
    with pytest.raises(SessionManagerError, match="HUMAN_REQUIRED"):
        manager.transition(
            session.session_id,
            SessionStatus.REVIEWING,
            operation_id="operation-2",
        )
