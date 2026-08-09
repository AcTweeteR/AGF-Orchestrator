from dataclasses import replace

import pytest

from agf_orchestrator.project_pilot import (
    PilotIntake,
    PilotIntakeError,
    PilotIntakeLedger,
    intake_from_dict,
    intake_hash,
)

PROJECT = "project-efc8e8ef7be7050b"
POLICY = "policy-adr-0003"
POLICY_HASH = "a" * 64


def intake(project=PROJECT, policy=POLICY, policy_hash=POLICY_HASH, budget=10):
    item = PilotIntake(
        "1.0", "intake-001", project, "objective-e11-pilot", policy,
        policy_hash, budget, "2026-08-10T12:00:00Z", "0" * 64,
    )
    return replace(item, content_sha256=intake_hash(item))


def test_valid_intake_is_bounded_project_and_policy_bound_and_idempotent():
    ledger = PilotIntakeLedger(PROJECT)
    item = intake()
    first = ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    second = ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    assert first == second
    assert first.bounded and not first.external_mutation
    assert ledger.get(item.intake_id) == item


def test_restart_readback_preserves_hash_bound_state():
    ledger = PilotIntakeLedger(PROJECT)
    item = intake()
    ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    restored = PilotIntakeLedger.from_state(ledger.export_state())
    assert restored.get(item.intake_id) == item
    changed = replace(item, intake_id="intake-002")
    changed = replace(changed, content_sha256=intake_hash(changed))
    with pytest.raises(PilotIntakeError, match="ledger policy binding"):
        restored.record(
            changed,
            expected_policy_id=POLICY, expected_policy_hash="b" * 64,
        )


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"project": "project-other"}, "project binding"),
        ({"policy_hash": "b" * 64}, "policy binding"),
        ({"budget": 0}, "budget"),
    ],
)
def test_invalid_binding_or_bounds_fail_closed(kwargs, expected):
    ledger = PilotIntakeLedger(PROJECT)
    with pytest.raises(PilotIntakeError, match=expected):
        ledger.record(
            intake(**kwargs), expected_policy_id=POLICY,
            expected_policy_hash=POLICY_HASH,
        )


def test_conflicting_replay_and_tampered_state_fail_closed():
    ledger = PilotIntakeLedger(PROJECT)
    item = intake()
    ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    conflicting = replace(item, budget_steps=11)
    conflicting = replace(conflicting, content_sha256=intake_hash(conflicting))
    with pytest.raises(PilotIntakeError, match="conflicting"):
        ledger.record(conflicting, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    tampered = ledger.export_state().replace(item.content_sha256, "b" * 64)
    with pytest.raises(PilotIntakeError, match="hash"):
        PilotIntakeLedger.from_state(tampered)


def test_malformed_and_secret_shaped_state_fail_closed():
    with pytest.raises(PilotIntakeError, match="invalid"):
        PilotIntakeLedger.from_state("null")
    item = intake()
    with pytest.raises(PilotIntakeError, match="schema"):
        intake_from_dict({**item.to_dict(), "token": "secret-value"})


def test_identical_replay_is_idempotent_and_conflicting_replay_is_rejected():
    ledger = PilotIntakeLedger(PROJECT)
    item = intake()
    first = ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    replay = ledger.record(item, expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH)
    assert replay == first
