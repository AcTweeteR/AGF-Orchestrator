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


def test_artifacts_are_write_once_and_same_content_is_idempotent(tmp_path):
    store = SessionStore(tmp_path / "state")
    path, digest = store.write_artifact("session-1", "assessment-v3.json", "{}\n")
    assert store.write_artifact("session-1", "assessment-v3.json", "{}\n") == (path, digest)
    try:
        store.write_artifact("session-1", "assessment-v3.json", "{\"changed\":true}\n")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("artifact overwrite was accepted")


def test_artifact_session_namespace_rejects_path_escape(tmp_path):
    store = SessionStore(tmp_path / "state")
    try:
        store.write_artifact("../escape", "plan.json", "{}\n")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("artifact session escape was accepted")

    try:
        store.write_artifact("..", "plan.json", "{}\n")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("dot-dot artifact session escape was accepted")


def test_artifact_session_namespace_rejects_symlink_escape(tmp_path):
    store = SessionStore(tmp_path / "state")
    outside = tmp_path / "outside"
    outside.mkdir()
    store.artifacts_dir.mkdir(parents=True)
    (store.artifacts_dir / "session-link").symlink_to(outside, target_is_directory=True)
    try:
        store.write_artifact("session-link", "plan.json", "{}\n")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("symlink artifact session escape was accepted")


def test_artifact_file_symlink_is_rejected(tmp_path):
    store = SessionStore(tmp_path / "state")
    directory = store.artifacts_dir / "session-1"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n")
    (directory / "plan.json").symlink_to(outside)
    try:
        store.write_artifact("session-1", "plan.json", "{}\n")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("artifact file symlink was accepted")


def test_session_namespace_symlink_is_rejected(tmp_path):
    store = SessionStore(tmp_path / "state")
    outside = tmp_path / "outside-sessions"
    outside.mkdir()
    store.sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    store.sessions_dir.symlink_to(outside, target_is_directory=True)
    try:
        store.load("session-1")
    except SessionStoreError:
        pass
    else:
        raise AssertionError("session namespace symlink was accepted")


def test_artifact_hash_rejects_symlink_read(tmp_path):
    store = SessionStore(tmp_path / "state")
    directory = store.artifacts_dir / "session-1"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n")
    link = directory / "plan.json"
    link.symlink_to(outside)
    try:
        store.artifact_hash(str(link))
    except SessionStoreError:
        pass
    else:
        raise AssertionError("artifact hash followed a symlink")


def test_artifact_hash_rejects_parent_symlink_read(tmp_path):
    store = SessionStore(tmp_path / "state")
    outside = tmp_path / "outside-artifacts" / "session-1"
    outside.mkdir(parents=True)
    target = outside / "plan.json"
    target.write_text("{}\n")
    store.state_dir.mkdir(parents=True)
    store.artifacts_dir.symlink_to(outside.parent, target_is_directory=True)
    try:
        store.artifact_hash(str(store.artifacts_dir / "session-1" / "plan.json"))
    except SessionStoreError:
        pass
    else:
        raise AssertionError("artifact hash followed a parent symlink")


def test_state_root_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside-state"
    outside.mkdir()
    link = tmp_path / "state-link"
    link.symlink_to(outside, target_is_directory=True)
    store = SessionStore(link)
    try:
        store.list()
    except SessionStoreError:
        pass
    else:
        raise AssertionError("state root symlink was accepted")


def test_state_parent_symlink_is_rejected(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    store = SessionStore(parent_link / "state")
    try:
        store.list()
    except SessionStoreError:
        pass
    else:
        raise AssertionError("state parent symlink was accepted")


def test_state_path_traversal_is_rejected(tmp_path):
    store = SessionStore(tmp_path / "state")
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n")
    try:
        store.ensure_safe_path(store.state_dir / ".." / outside.name)
    except SessionStoreError:
        pass
    else:
        raise AssertionError("state path traversal was accepted")


def test_session_list_symlink_is_rejected(tmp_path):
    store = SessionStore(tmp_path / "state")
    outside = tmp_path / "outside-sessions"
    outside.mkdir()
    store.state_dir.mkdir(parents=True)
    store.sessions_dir.symlink_to(outside, target_is_directory=True)
    try:
        store.list()
    except SessionStoreError:
        pass
    else:
        raise AssertionError("session list followed a symlink")
