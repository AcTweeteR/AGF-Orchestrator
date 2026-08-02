from agf_orchestrator.inbox import build_inbox
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_manager import SessionManager
from agf_orchestrator.session_models import SessionStatus
from agf_orchestrator.session_store import SessionStore


def test_inbox_contains_attention_items_only(tmp_path):
    root = tmp_path / "repo"
    origin = tmp_path / "origin.git"
    import subprocess

    root.mkdir()
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "feature", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    for key, value in (("user.name", "T"), ("user.email", "t@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "x").write_text("x")
    subprocess.run(["git", "-C", str(root), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "i"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin.as_uri()], check=True)
    state = tmp_path / "state"
    ProjectRegistry(state).add("alpha", root)
    manager = SessionManager(state)
    session = manager.start("alpha", "Add contributor link validation")
    manager.transition(
        session.session_id,
        SessionStatus.HUMAN_REQUIRED,
        summary="human decision required",
        actor="SYSTEM",
        blocking_issues=["clarify scope"],
    )
    items = build_inbox(SessionStore(state), ProjectRegistry(state))
    assert len(items) == 1 and items[0].status == SessionStatus.HUMAN_REQUIRED.value
