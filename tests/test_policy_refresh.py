import base64
import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agf_orchestrator.constitution import canonical_json
from agf_orchestrator.policy_authority import PolicyActivationError, PolicyAuthority
from agf_orchestrator.policy_state_store import PolicyStateError, PolicyStateStore
from tools import owner_policy_controller
from tools.owner_policy_controller import OwnerPolicyController

PROJECT = "project-efc8e8ef7be7050b"
KEY = b"owner-key-material-that-is-at-least-32-bytes-long"
POLICY_HASH = "a" * 64
POLICY = {
    "project_id": PROJECT, "policy_id": "merge-policy-adr-0003", "version": "1.0",
    "schema_version": "1.0", "compatibility": "test", "signature": "sig",
    "key_id": "key", "created_at": "2026-08-08T00:00:00Z",
}


def _activation(operation_id: str, activation_time: str, policy_hash: str = POLICY_HASH):
    return {
        "project_id": PROJECT, "policy_id": POLICY["policy_id"],
        "policy_hash": policy_hash, "previous_policy_hash": "b" * 64,
        "activation_time": activation_time, "signature": "sig",
        "operation_id": operation_id,
    }


def _authority_state(home: Path) -> None:
    root = home / ".agf-orchestrator"
    authority = root / "constitution-authority"
    constitution = root / "projects" / PROJECT / "constitution"
    authority.mkdir(parents=True)
    constitution.mkdir(parents=True)
    root.chmod(0o700)
    authority.chmod(0o700)
    owner_key = authority / "owner.key"
    owner_key.write_text(base64.b64encode(KEY).decode("ascii"))
    owner_key.chmod(0o600)
    unsigned = {
        "schema_version": "1.0", "constitution_id": "constitution-v1", "version": "1.0",
        "project_id": PROJECT, "compatibility": "agf-constitution-v1",
        "approval_status": "APPROVED", "body": {"protected": True},
        "key_id": "owner-key-1",
    }
    record = {**unsigned, "signature": hmac.new(
        KEY, canonical_json(unsigned), hashlib.sha256
    ).hexdigest()}
    record_hash = hashlib.sha256(canonical_json(record)).hexdigest()
    (constitution / "constitution-v1.json").write_text(json.dumps(record))
    (constitution / "active.json").write_text(json.dumps({
        "schema_version": "1.0", "project_id": PROJECT,
        "constitution_id": "constitution-v1", "record_hash": record_hash,
    }))


def test_refresh_is_atomic_and_preserves_old_state_on_failure(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    old = _activation("operation-activation-old", "2026-08-08T00:00:00Z")
    store.activate(PROJECT, POLICY, old, expected_generation=0)
    new = _activation("operation-refresh-new", "2026-08-10T12:00:00Z")

    for boundary in ("before_journal", "after_journal", "after_activation", "before_commit"):
        def fail(stage, _connection):
            if stage == boundary:
                raise RuntimeError("injected refresh crash")

        with pytest.raises(RuntimeError, match="injected refresh crash"):
            store.refresh(
                PROJECT, POLICY, new, expected_generation=1,
                expected_active_policy_hash=POLICY_HASH, failure_hook=fail,
            )
        state = store.snapshot(PROJECT)
        assert state["generation"] == 1
        assert state["active_activation_id"] == old["operation_id"]

    assert store.refresh(
        PROJECT, POLICY, new, expected_generation=1,
        expected_active_policy_hash=POLICY_HASH,
    ) == 2
    state = store.snapshot(PROJECT)
    assert state["generation"] == 2
    assert state["active_policy_hash"] == POLICY_HASH
    assert state["active_activation_id"] == new["operation_id"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT operation_type, generation FROM operation_journal "
            "WHERE operation_id=?", (new["operation_id"],)
        ).fetchone() == ("refresh", 2)


def test_refresh_rejects_replay_stale_generation_and_wrong_identity(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    store.activate(PROJECT, POLICY, _activation("operation-refresh-base", "2026-08-08T00:00:00Z"))
    refresh = _activation("operation-refresh-once", "2026-08-10T12:00:00Z")
    store.refresh(PROJECT, POLICY, refresh, expected_generation=1,
                  expected_active_policy_hash=POLICY_HASH)
    with pytest.raises(PolicyStateError, match="already been consumed"):
        store.refresh(PROJECT, POLICY, refresh, expected_generation=2,
                      expected_active_policy_hash=POLICY_HASH)
    with pytest.raises(PolicyStateError, match="stale generation"):
        store.refresh(
            PROJECT, POLICY,
            _activation("operation-refresh-stale", "2026-08-10T12:00:00Z"),
            expected_generation=1, expected_active_policy_hash=POLICY_HASH,
        )
    with pytest.raises(PolicyStateError, match="identity"):
        store.refresh(PROJECT, {**POLICY, "policy_id": "other"},
                      _activation("operation-refresh-wrong-policy", "2026-08-10T12:00:00Z"),
                      expected_generation=2, expected_active_policy_hash=POLICY_HASH)


def test_concurrent_refresh_has_one_committed_winner(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    store.activate(PROJECT, POLICY, _activation("operation-refresh-base", "2026-08-08T00:00:00Z"))
    results = []

    def run(operation_id):
        try:
            results.append(store.refresh(
                PROJECT, POLICY, _activation(operation_id, "2026-08-10T12:00:00Z"),
                expected_generation=1, expected_active_policy_hash=POLICY_HASH,
            ))
        except (PolicyStateError, sqlite3.OperationalError) as exc:
            results.append(str(exc))

    threads = [threading.Thread(target=run, args=(f"operation-refresh-race-{i}",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(result, int) for result in results) == 1
    assert store.snapshot(PROJECT)["generation"] == 2


def test_controller_refresh_keeps_policy_hash_and_revalidates_fresh_activation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _authority_state(tmp_path)
    controller = OwnerPolicyController()
    controller._now = lambda: "2026-08-08T00:00:00Z"
    policy = controller.prepare(PROJECT)
    policy_hash = owner_policy_controller.canonical_hash(policy)
    monkeypatch.setattr(owner_policy_controller, "AGF_0003_POLICY_HASH", policy_hash)
    controller.activate(PROJECT, "operation-refresh-initial")
    old_activation_hash = owner_policy_controller.canonical_hash(
        PolicyStateStore(tmp_path / ".agf-orchestrator", read_only=True)
        .snapshot(PROJECT)["activation"]
    )
    with pytest.raises(PolicyActivationError, match="invalid activation time"):
        PolicyAuthority().resolve(PROJECT)

    controller._now = lambda: datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    result = controller.refresh(PROJECT, "operation-refresh-authorized")
    active = PolicyAuthority().resolve(PROJECT)
    assert result["generation"] == 2
    assert active.policy_hash == policy_hash
    assert active.activation_hash != old_activation_hash
    assert active.activation_hash == result["activation_hash"]
    assert active.allows_autonomous_merge("HIGH")


def test_runtime_has_no_refresh_mutation_api():
    assert not hasattr(PolicyAuthority, "refresh")


def test_refresh_accepts_project_bound_adr0003_hash_without_global_hash(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _authority_state(tmp_path)
    controller = OwnerPolicyController()
    controller._now = lambda: "2026-08-08T00:00:00Z"
    policy = controller.prepare(PROJECT)
    project_policy_hash = owner_policy_controller.canonical_hash(policy)
    assert project_policy_hash != owner_policy_controller.AGF_0003_POLICY_HASH
    controller.activate(PROJECT, "operation-project-bound-refresh")
    controller._now = lambda: datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    refreshed = controller.refresh(PROJECT, "operation-project-bound-refresh-2")
    assert refreshed["policy_hash"] == project_policy_hash
