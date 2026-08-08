import base64
import hashlib
import hmac
import json
import sqlite3
import threading
import time

import pytest

from agf_orchestrator.policy_state_store import PolicyStateError, PolicyStateStore

PROJECT = "project-efc8e8ef7be7050b"
POLICY = {
    "project_id": PROJECT, "policy_id": "merge-policy-adr-0003", "version": "1.0",
    "schema_version": "1.0", "compatibility": "test", "signature": "sig",
    "key_id": "key", "created_at": "2026-08-08T00:00:00+00:00",
}
POLICY_HASH = "a" * 64
OWNER_KEY = b"owner-key-material-that-is-at-least-32-bytes-long"


def owner_authorization(root, operation_id, active, reason):
    authority = root / "constitution-authority"
    authority.mkdir(parents=True, exist_ok=True)
    (authority / "owner.key").write_text(base64.b64encode(OWNER_KEY).decode("ascii"))
    unsigned = {
        "project_id": PROJECT, "operation_id": operation_id, "active": active,
        "reason": reason, "key_id": "owner-key-1",
    }
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return {
        **unsigned, "signature": hmac.new(OWNER_KEY, payload, hashlib.sha256).hexdigest()
    }


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


def test_kill_switch_generation_and_clear_invalidate_old_state(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.bootstrap_authority(PROJECT, generation=1)
    assert store.authority_snapshot(PROJECT)["generation"] == 1
    authorization = owner_authorization(tmp_path, "operation-stop-on", True, "owner incident")
    assert store.set_kill_switch(
        PROJECT, operation_id="operation-stop-on", active=True,
        reason="owner incident", authorization=authorization, expected_generation=1,
    ) == 2
    active = store.authority_snapshot(PROJECT)
    assert active["kill_switch_active"] == 1
    with pytest.raises(PolicyStateError, match="stale authority"):
        store.set_kill_switch(
            PROJECT, operation_id="operation-stop-clear", active=False,
            reason="owner cleared", authorization=owner_authorization(
                tmp_path, "operation-stop-clear", False, "owner cleared"
            ), expected_generation=1,
        )
    clear_auth = owner_authorization(tmp_path, "operation-stop-clear", False, "owner cleared")
    assert store.set_kill_switch(
        PROJECT, operation_id="operation-stop-clear", active=False,
        reason="owner cleared", authorization=clear_auth, expected_generation=2,
    ) == 3
    cleared = store.authority_snapshot(PROJECT)
    assert cleared["kill_switch_active"] == 0
    with pytest.raises(PolicyStateError, match="already"):
        store.set_kill_switch(
            PROJECT, operation_id="operation-stop-clear", active=False,
            reason="replay", authorization=clear_auth,
        )


def test_delivery_transaction_wins_or_loses_switch_race_deterministically(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.bootstrap_authority(PROJECT, generation=1)
    entered = threading.Event()
    finished = threading.Event()

    def activate():
        entered.wait()
        store.set_kill_switch(
            PROJECT, operation_id="operation-race-stop", active=True, reason="race",
            authorization=owner_authorization(
                tmp_path, "operation-race-stop", True, "race"
            )
        )
        finished.set()

    thread = threading.Thread(target=activate)
    thread.start()
    store.reserve_delivery(PROJECT, operation_id="decision-race", expected_generation=1)
    token = store.begin_delivery_commit(
        PROJECT, operation_id="decision-race", expected_generation=1
    )
    with store.delivery_transaction(
        PROJECT, operation_id="decision-race", expected_generation=1,
        commit_token=token,
    ):
        entered.set()
        time.sleep(0.05)
        assert not finished.is_set()
    thread.join(timeout=2)
    assert finished.is_set()
    assert store.authority_snapshot(PROJECT)["kill_switch_active"] == 1
    with pytest.raises(PolicyStateError, match="stale|active"):
        with store.delivery_transaction(
            PROJECT, operation_id="decision-race-retry", expected_generation=1,
            commit_token="unused",
        ):
            pass


def test_delivery_commit_crash_leaves_non_replayable_recovery_state(tmp_path):
    store = PolicyStateStore(tmp_path)
    store.bootstrap_authority(PROJECT, generation=1)
    store.reserve_delivery(PROJECT, operation_id="decision-crash", expected_generation=1)
    token = store.begin_delivery_commit(
        PROJECT, operation_id="decision-crash", expected_generation=1
    )
    with pytest.raises(RuntimeError, match="crash"):
        with store.delivery_transaction(
            PROJECT, operation_id="decision-crash", expected_generation=1,
            commit_token=token,
        ):
            raise RuntimeError("crash")
    with pytest.raises(PolicyStateError, match="already"):
        store.reserve_delivery(PROJECT, operation_id="decision-crash", expected_generation=1)
    with pytest.raises(PolicyStateError, match="not reservable|token is invalid"):
        with store.delivery_transaction(
            PROJECT, operation_id="decision-crash", expected_generation=1,
            commit_token="lost-after-crash",
        ):
            pass
