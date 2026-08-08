import sqlite3
import threading

import pytest

from agf_orchestrator.policy_state_store import PolicyStateError, PolicyStateStore

PROJECT = "project-efc8e8ef7be7050b"
POLICY = {
    "project_id": PROJECT, "policy_id": "merge-policy-adr-0003", "version": "1.0",
    "schema_version": "1.0", "compatibility": "test", "signature": "sig",
    "key_id": "key", "created_at": "2026-08-08T00:00:00+00:00",
}
POLICY_HASH = "a" * 64


def activation(operation_id="operation-activation-one", policy_hash=POLICY_HASH):
    return {
        "project_id": PROJECT, "policy_id": POLICY["policy_id"],
        "policy_hash": policy_hash, "previous_policy_hash": "b" * 64,
        "activation_time": "2026-08-08T00:00:00+00:00", "signature": "sig",
        "operation_id": operation_id,
    }


def test_activation_failure_at_each_boundary_is_atomic(tmp_path):
    for boundary in ("before_journal", "after_journal", "after_activation", "before_commit"):
        store = PolicyStateStore(tmp_path / boundary)
        store.prepare(POLICY, POLICY_HASH)

        def fail(stage, _connection):
            if stage == boundary:
                raise RuntimeError("injected crash")

        with pytest.raises(RuntimeError, match="injected crash"):
            store.activate(PROJECT, POLICY, activation(f"operation-{boundary}"),
                           failure_hook=fail)
        assert store.snapshot(PROJECT) is None


def test_commit_restart_and_duplicate_are_deterministic(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    record = activation()
    assert store.activate(PROJECT, POLICY, record, expected_generation=0) == 1
    reopened = PolicyStateStore(tmp_path, read_only=True).snapshot(PROJECT)
    assert reopened["generation"] == 1
    assert reopened["active_policy_hash"] == POLICY_HASH
    with pytest.raises(PolicyStateError, match="already"):
        store.activate(PROJECT, POLICY, record)


def test_stale_generation_wrong_project_and_hash_fail_without_state_change(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    with pytest.raises(PolicyStateError, match="stale generation"):
        store.activate(PROJECT, POLICY, activation(), expected_generation=4)
    with pytest.raises(PolicyStateError, match="policy artifact"):
        store.activate(PROJECT, POLICY, activation("operation-wrong-hash", "c" * 64))
    with pytest.raises(PolicyStateError, match="policy artifact"):
        store.activate("project-0000000000000001", POLICY, activation("operation-wrong-project"))
    assert store.snapshot(PROJECT) is None


def test_rollback_failure_is_atomic_and_invalidates_active_generation(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    store.activate(PROJECT, POLICY, activation())
    rollback = {
        "project_id": PROJECT, "operation_id": "operation-rollback-one",
        "superseded_policy_hash": POLICY_HASH, "restored_policy_hash": "b" * 64,
        "rollback_target": {"policy_id": "constitutional", "policy_hash": "b" * 64},
        "tombstone_hash": "d" * 64, "signature": "sig",
    }

    def fail(stage, _connection):
        if stage == "after_journal":
            raise RuntimeError("injected rollback crash")

    with pytest.raises(RuntimeError, match="rollback crash"):
        store.rollback(PROJECT, rollback, failure_hook=fail)
    assert store.snapshot(PROJECT)["active_policy_hash"] == POLICY_HASH
    assert store.rollback(PROJECT, rollback, expected_generation=1) == 2
    state = store.snapshot(PROJECT)
    assert state["active_policy_hash"] is None
    assert state["generation"] == 2
    with pytest.raises(PolicyStateError, match="superseded"):
        store.activate(PROJECT, POLICY, activation("operation-replay-after-rollback"))


def test_concurrent_activation_has_one_committed_winner(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.prepare(POLICY, POLICY_HASH)
    results = []

    def run(operation_id):
        try:
            results.append((
                operation_id, store.activate(PROJECT, POLICY, activation(operation_id))
            ))
        except (PolicyStateError, sqlite3.OperationalError) as exc:
            results.append((operation_id, str(exc)))

    threads = [
        threading.Thread(target=run, args=(f"operation-concurrent-{index}",))
        for index in ("one", "two")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(value, int) for _, value in results) == 1
    assert store.snapshot(PROJECT)["generation"] == 1
