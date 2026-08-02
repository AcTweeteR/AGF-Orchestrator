from agf_orchestrator.session_models import Session, SessionStatus
from agf_orchestrator.session_store import SessionStore, SessionStoreError


def test_atomic_session_and_artifact_storage(tmp_path):
    store = SessionStore(tmp_path / "state")
    session = Session(
        "session-1", "project-1", "goal", "t", "t", "sha", "READY", SessionStatus.READY
    )
    store.save(session)
    assert store.load("session-1").session_id == "session-1"
    path, digest = store.write_artifact("session-1", "plan.json", "{}\n")
    assert store.artifact_hash(path) == digest
    try:
        store.load("../escape")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("path traversal was accepted")
