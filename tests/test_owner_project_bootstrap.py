import base64
import hashlib
import hmac
import sqlite3
import subprocess
from pathlib import Path

import pytest

from agf_orchestrator.constitution import ConstitutionAuthority, canonical_hash, canonical_json
from agf_orchestrator.policy_authority import PolicyActivationError, PolicyAuthority
from agf_orchestrator.project_registry import ProjectRegistryError
from tools.owner_policy_controller import OwnerPolicyController
from tools.owner_project_bootstrap import OwnerBootstrapError, OwnerProjectBootstrapper

KEY = b"owner-key-material-that-is-at-least-32-bytes-long"
SOURCE_PROJECT = "project-efc8e8ef7be7050b"


def repository(tmp_path, name="target", remote="https://github.com/AcTweeteR/target.git"):
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    (root / "README.md").write_text("target\n")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", remote], check=True)
    return root


def owner_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    state = tmp_path / ".agf-orchestrator"
    authority = state / "constitution-authority"
    source = state / "projects" / SOURCE_PROJECT / "constitution"
    authority.mkdir(parents=True)
    source.mkdir(parents=True)
    state.chmod(0o700)
    authority.chmod(0o700)
    (authority / "owner.key").write_text(base64.b64encode(KEY).decode())
    (authority / "owner.key").chmod(0o600)
    unsigned = {
        "schema_version": "1.0", "constitution_id": "constitution-v1", "version": "1.0",
        "project_id": SOURCE_PROJECT, "compatibility": "agf-constitution-v1",
        "approval_status": "APPROVED", "body": {"protected": True}, "key_id": "owner-key-1",
    }
    record = {
        **unsigned,
        "signature": hmac.new(KEY, canonical_json(unsigned), hashlib.sha256).hexdigest(),
    }
    (source / "constitution-v1.json").write_bytes(canonical_json(record))
    (source / "active.json").write_bytes(canonical_json({
        "schema_version": "1.0", "project_id": SOURCE_PROJECT,
        "constitution_id": "constitution-v1", "record_hash": canonical_hash(record),
    }))
    return state


def test_bootstrap_verifies_constitution_policy_and_rollback_baseline(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path)
    result = OwnerProjectBootstrapper(state).bootstrap(root, name="target")
    project_id = result["project_id"]
    assert result["registration"] == "ACTIVE"
    assert result["constitution"]["status"] == "VERIFIED"
    assert result["policy"]["generation"] == 1
    assert result["rollback_baseline"] == "VERIFIED"
    assert ConstitutionAuthority().resolve(project_id).project_id == project_id
    assert PolicyAuthority().resolve(project_id).project_id == project_id


def test_bootstrap_is_idempotent_and_inspect_is_metadata_only(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path)
    bootstrap = OwnerProjectBootstrapper(state)
    first = bootstrap.bootstrap(root, name="target")
    inspected = bootstrap.inspect(root)
    registry_before = (state / "projects.json").read_bytes()
    second = bootstrap.bootstrap(root, name="target")
    assert inspected["registered"]
    assert first == second
    assert (state / "projects.json").read_bytes() == registry_before
    assert bootstrap.policy.store.authority_snapshot(first["project_id"])["generation"] == 1


def test_conflicting_origin_and_missing_owner_authority_fail_closed(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    first = repository(tmp_path, "first")
    second = repository(tmp_path, "second")
    bootstrap = OwnerProjectBootstrapper(state)
    bootstrap.bootstrap(first, name="first")
    with pytest.raises(ProjectRegistryError, match="origin"):
        bootstrap.bootstrap(second, name="second")
    (state / "constitution-authority" / "owner.key").unlink()
    with pytest.raises(OwnerBootstrapError, match="target|owner key"):
        bootstrap.verify(bootstrap.registry.get("first").project_id)


def test_canonical_timestamp_is_utc_and_stale_activation_is_rejected():
    timestamp = OwnerPolicyController._now()
    assert timestamp.endswith("Z")
    with pytest.raises(PolicyActivationError, match="invalid activation time"):
        PolicyAuthority._validate_time("2020-01-01T00:00:00Z", 86400)


def test_partial_bootstrap_is_compensated_and_symlink_directory_is_rejected(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path)
    bootstrap = OwnerProjectBootstrapper(state)
    original = bootstrap._bootstrap_constitution
    bootstrap._bootstrap_constitution = lambda _project: (_ for _ in ()).throw(
        OwnerBootstrapError("injected failure")
    )
    with pytest.raises(OwnerBootstrapError, match="injected"):
        bootstrap.bootstrap(root, name="partial")
    assert bootstrap.inspect(root)["registered"] is False
    bootstrap._bootstrap_constitution = original
    project = bootstrap.registry.add("symlink", root, accept_duplicate_origin=True)
    constitution_parent = state / "projects" / project.project_id
    constitution_parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside-constitution"
    target.mkdir()
    constitution_dir = constitution_parent / "constitution"
    constitution_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(OwnerBootstrapError, match="symlink"):
        bootstrap._bootstrap_constitution(project.project_id)


def test_late_failure_cleans_new_state_but_preserves_existing_state(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "late")
    bootstrap = OwnerProjectBootstrapper(state)
    project_id = "project-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    original_verify = bootstrap._verify_policy
    bootstrap._verify_policy = lambda _project: (_ for _ in ()).throw(
        OwnerBootstrapError("late injected failure")
    )
    with pytest.raises(OwnerBootstrapError, match="late injected"):
        bootstrap.bootstrap(root, name="late")
    assert bootstrap.inspect(root)["registered"] is False
    assert not (state / "projects" / project_id).exists()
    with sqlite3.connect(state / "policy-state.sqlite3") as database:
        assert database.execute(
            "select count(*) from policies where project_id=?", (project_id,)
        ).fetchone()[0] == 0
    bootstrap._verify_policy = original_verify
    existing = repository(tmp_path, "existing", "https://github.com/AcTweeteR/existing.git")
    bootstrap.bootstrap(existing, name="existing")
    bootstrap._verify_policy = lambda _project: (_ for _ in ()).throw(
        OwnerBootstrapError("existing injected failure")
    )
    with pytest.raises(OwnerBootstrapError, match="existing injected"):
        bootstrap.bootstrap(existing, name="existing")
    inspected = bootstrap.inspect(existing)
    assert inspected["registered"] and inspected["constitution"]["status"] == "VERIFIED"
    assert inspected["policy"]["active_present"]


def test_existing_unverified_orphan_state_is_not_overwritten_or_deleted(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "orphan")
    project_id = OwnerProjectBootstrapper._project_id(root.resolve())
    orphan = state / "projects" / project_id / "constitution"
    orphan.mkdir(parents=True)
    marker = orphan / "marker.json"
    marker.write_text("orphan evidence")
    bootstrap = OwnerProjectBootstrapper(state)
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        bootstrap.bootstrap(root, name="orphan")
    assert marker.read_text() == "orphan evidence"


def test_all_project_state_path_symlinks_are_rejected(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "symlink-parent")
    projects = state / "projects"
    projects_real = state / "projects-real"
    projects.rename(projects_real)
    outside = tmp_path / "outside-projects"
    outside.mkdir()
    projects.symlink_to(projects_real, target_is_directory=True)
    with pytest.raises(OwnerBootstrapError, match="symlink"):
        OwnerProjectBootstrapper(state).bootstrap(root, name="symlink-parent")


@pytest.mark.parametrize(
    "table",
    [
        "policies", "activations", "rollback_records", "operation_journal",
        "active_state", "authority_state",
    ],
)
def test_all_orphan_transactional_rows_block_bootstrap(tmp_path, monkeypatch, table):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, f"orphan-{table}")
    project_id = OwnerProjectBootstrapper._project_id(root.resolve())
    store = OwnerPolicyController(state).store
    store.initialize()
    with sqlite3.connect(store.path) as database:
        if table == "policies":
            database.execute(
                "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, "orphan", "a" * 64, "1", "1", "test", "sig", "key", "{}", "now"),
            )
        elif table == "activations":
            database.execute(
                "INSERT INTO activations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id, "operation-orphan", "orphan", "a" * 64, "b" * 64,
                    "now", "sig", "{}", 1,
                ),
            )
        elif table == "rollback_records":
            database.execute(
                "INSERT INTO rollback_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, "operation-orphan", "a" * 64, "b" * 64, "c" * 64, "sig", "{}", 1),
            )
        elif table == "operation_journal":
            database.execute(
                "INSERT INTO operation_journal VALUES (?, ?, ?, ?, ?, ?)",
                ("operation-orphan", project_id, "orphan", 1, "a" * 64, "now"),
            )
        elif table == "active_state":
            database.execute(
                "INSERT INTO active_state VALUES (?, ?, ?, ?, ?)",
                (project_id, None, None, None, 0),
            )
        else:
            database.execute(
                "INSERT INTO authority_state VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, 0, 0, "bootstrap", "now", "orphan"),
            )
        database.commit()
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        OwnerProjectBootstrapper(state).bootstrap(root, name=table)


def test_registered_project_with_orphan_policy_state_fails_closed(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "registered-orphan")
    bootstrap = OwnerProjectBootstrapper(state)
    project = bootstrap.registry.add("registered-orphan", root, accept_duplicate_origin=True)
    store = bootstrap.policy.store
    store.initialize()
    with sqlite3.connect(store.path) as database:
        database.execute(
            "INSERT INTO operation_journal VALUES (?, ?, ?, ?, ?, ?)",
            ("operation-registered-orphan", project.project_id, "orphan", 1, "a" * 64, "now"),
        )
        database.commit()
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        bootstrap.bootstrap(root, name="registered-orphan")


def test_registered_verified_constitution_with_incomplete_policy_fails_closed(
    tmp_path, monkeypatch
):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "registered-verified-orphan")
    bootstrap = OwnerProjectBootstrapper(state)
    project = bootstrap.registry.add(
        "registered-verified-orphan", root, accept_duplicate_origin=True
    )
    bootstrap._bootstrap_constitution(project.project_id)
    store = bootstrap.policy.store
    store.initialize()
    with sqlite3.connect(store.path) as database:
        database.execute(
            "INSERT INTO operation_journal VALUES (?, ?, ?, ?, ?, ?)",
            (
                "operation-verified-orphan", project.project_id, "orphan", 1,
                "a" * 64, "now",
            ),
        )
        database.commit()
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        bootstrap.bootstrap(root, name="registered-verified-orphan")


def test_registered_project_with_active_state_only_fails_closed(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "registered-active-orphan")
    bootstrap = OwnerProjectBootstrapper(state)
    project = bootstrap.registry.add(
        "registered-active-orphan", root, accept_duplicate_origin=True
    )
    bootstrap._bootstrap_constitution(project.project_id)
    store = bootstrap.policy.store
    store.initialize()
    with sqlite3.connect(store.path) as database:
        database.execute(
            "INSERT INTO active_state VALUES (?, ?, ?, ?, ?)",
            (project.project_id, None, None, None, 0),
        )
        database.commit()
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        bootstrap.bootstrap(root, name="registered-active-orphan")


def test_active_policy_without_authority_baseline_fails_closed(tmp_path, monkeypatch):
    state = owner_state(tmp_path, monkeypatch)
    root = repository(tmp_path, "missing-authority")
    bootstrap = OwnerProjectBootstrapper(state)
    bootstrap.bootstrap(root, name="missing-authority")
    project_id = bootstrap.registry.get("missing-authority").project_id
    with sqlite3.connect(bootstrap.policy.store.path) as database:
        database.execute("DELETE FROM authority_state WHERE project_id=?", (project_id,))
        database.commit()
    with pytest.raises(OwnerBootstrapError, match="pre-existing authority state"):
        bootstrap.bootstrap(root, name="missing-authority")
