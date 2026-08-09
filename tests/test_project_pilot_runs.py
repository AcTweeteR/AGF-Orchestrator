import hashlib
import json
from dataclasses import replace

import pytest

from agf_orchestrator.project_pilot import (
    PilotIntake,
    PilotRunError,
    PilotRunEvent,
    PilotRunLedger,
    event_hash,
    intake_hash,
)

PROJECT = "project-efc8e8ef7be7050b"
POLICY = "policy-adr-0003"
POLICY_HASH = "a" * 64


def make_intake(budget=3):
    item = PilotIntake(
        "1.0", "intake-001", PROJECT, "objective-e11-pilot", POLICY,
        POLICY_HASH, budget, "2026-08-10T12:00:00Z", "0" * 64,
    )
    return replace(item, content_sha256=intake_hash(item))


def test_bounded_run_is_idempotent_restartable_and_completes():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake())
    assert ledger.start(make_intake()) == run
    advanced = ledger.apply(
        run.run_id, "operation-001", "STEP", "bounded step",
        "2026-08-10T12:00:01Z",
    )
    assert ledger.apply(
        run.run_id, "operation-001", "STEP", "bounded step",
        "2026-08-10T12:00:01Z",
    ) == advanced
    done = ledger.apply(
        run.run_id, "operation-002", "COMPLETE", "pilot complete",
        "2026-08-10T12:00:02Z",
    )
    restored = PilotRunLedger.from_state(ledger.export_state())
    assert done.status == "COMPLETED"
    assert restored.get(run.run_id) == done


def test_failure_is_terminal_and_budget_is_bounded():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake(budget=1))
    failed = ledger.apply(
        run.run_id, "operation-001", "FAIL", "controlled failure",
        "2026-08-10T12:00:01Z",
    )
    assert failed.status == "FAILED"
    with pytest.raises(PilotRunError, match="terminal"):
        ledger.apply(run.run_id, "operation-002", "STEP", "after failure",
                     "2026-08-10T12:00:02Z")


def test_isolation_replay_conflict_and_policy_binding_fail_closed():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    with pytest.raises(PilotRunError, match="policy binding"):
        wrong = replace(make_intake(), policy_hash="b" * 64)
        ledger.start(replace(wrong, content_sha256=intake_hash(wrong)))
    run = ledger.start(make_intake())
    with pytest.raises(PilotRunError, match="conflicting operation"):
        ledger.apply(run.run_id, "operation-001", "STEP", "one",
                     "2026-08-10T12:00:01Z")
        ledger.apply(run.run_id, "operation-001", "STEP", "two",
                     "2026-08-10T12:00:01Z")


def test_malformed_and_unknown_run_state_fail_closed():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    ledger.start(make_intake())
    with pytest.raises(PilotRunError, match="invalid"):
        PilotRunLedger.from_state("[]")
    tampered = ledger.export_state().replace('"status":"RUNNING"', '"status":"FAILED"')
    with pytest.raises(PilotRunError, match="hash"):
        PilotRunLedger.from_state(tampered)


def test_wrong_project_and_malformed_run_or_event_fail_closed():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake())
    payload = json.loads(ledger.export_state())
    payload["runs"][0]["project_id"] = "project-other"
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="invariants"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    ledger.apply(run.run_id, "operation-001", "STEP", "step", "2026-08-10T12:00:01Z")
    payload = json.loads(ledger.export_state())
    del payload["events"][0]["kind"]
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="invalid"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def test_impossible_terminal_history_and_timestamp_fail_closed():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake(budget=2))
    ledger.apply(run.run_id, "operation-001", "COMPLETE", "done",
                 "2026-08-10T12:00:01Z")
    payload = json.loads(ledger.export_state())
    payload["runs"][0]["status"] = "RUNNING"
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="terminal"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake())
    ledger.apply(run.run_id, "operation-001", "STEP", "step", "2026-08-10T12:00:01Z")
    payload = json.loads(ledger.export_state())
    event = PilotRunEvent(**payload["events"][0])
    event = replace(event, observed_at="2026-99-10T12:00:01Z", content_sha256="0" * 64)
    payload["events"][0] = replace(event, content_sha256=event_hash(event)).to_dict()
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="timestamp"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def test_duplicate_run_and_operation_identity_fail_closed():
    ledger = PilotRunLedger(PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH)
    run = ledger.start(make_intake())
    payload = json.loads(ledger.export_state())
    payload["runs"].append(payload["runs"][0].copy())
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="duplicate run"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    ledger.apply(run.run_id, "operation-001", "STEP", "step", "2026-08-10T12:00:01Z")
    payload = json.loads(ledger.export_state())
    payload["events"].append(payload["events"][0].copy())
    payload["state_sha256"] = hashlib.sha256(
        json.dumps(
            {key: payload[key] for key in payload if key != "state_sha256"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(PilotRunError, match="duplicate operation"):
        PilotRunLedger.from_state(json.dumps(payload, separators=(",", ":"), sort_keys=True))
