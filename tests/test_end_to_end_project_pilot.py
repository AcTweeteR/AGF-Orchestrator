from dataclasses import replace

import pytest

from agf_orchestrator.project_pilot import (
    EndToEndPilot,
    EndToEndPilotError,
    PilotIntake,
    intake_hash,
)

PROJECT = "project-efc8e8ef7be7050b"
POLICY = "policy-adr-0003"
POLICY_HASH = "a" * 64
ROLLBACK_HASH = "b" * 64


def intake(budget=3):
    item = PilotIntake(
        "1.0", "intake-e2e", PROJECT, "objective-e11-pilot", POLICY,
        POLICY_HASH, budget, "2026-08-10T12:00:00Z", "0" * 64,
    )
    return replace(item, content_sha256=intake_hash(item))


def boundary():
    return (
        {"project_id": PROJECT, "generation": 3, "kill_switch_active": False,
         "operation_id": "operation-stop-clear"},
        {"project_id": PROJECT, "operation_id": "operation-rollback-e2e",
         "superseded_policy_hash": POLICY_HASH, "restored_policy_hash": ROLLBACK_HASH,
         "previous_generation": 2, "generation": 3, "tombstone_hash": "c" * 64},
        {"project_id": PROJECT, "generation": 2, "policy_hash": POLICY_HASH,
         "status": "HUMAN_REQUIRED", "risk_class": "CRITICAL"},
    )


def test_end_to_end_pilot_proves_composition_without_external_mutation():
    authority, rollback, authorization = boundary()
    report = EndToEndPilot().run(
        intake(), expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH,
        authority=authority, rollback=rollback, stale_authorization=authorization,
        expected_rollback_hash=ROLLBACK_HASH,
    )
    assert report.restart_verified and report.failure_verified
    assert report.rollback_verified and report.kill_switch_verified
    assert not report.blocked
    assert not report.external_mutation and not report.authoritative


def test_end_to_end_pilot_is_deterministic():
    args = {"expected_policy_id": POLICY, "expected_policy_hash": POLICY_HASH}
    authority, rollback, authorization = boundary()
    args.update(authority=authority, rollback=rollback, stale_authorization=authorization,
                expected_rollback_hash=ROLLBACK_HASH)
    assert EndToEndPilot().run(intake(), **args) == EndToEndPilot().run(intake(), **args)


def test_end_to_end_pilot_rejects_missing_budget_or_boundary_evidence():
    authority, rollback, authorization = boundary()
    with pytest.raises(EndToEndPilotError, match="two bounded"):
        EndToEndPilot().run(
            intake(budget=1), expected_policy_id=POLICY,
            expected_policy_hash=POLICY_HASH, authority=authority,
            rollback=rollback, stale_authorization=authorization,
            expected_rollback_hash=ROLLBACK_HASH,
        )
    del rollback["tombstone_hash"]
    with pytest.raises(EndToEndPilotError, match="incomplete"):
        EndToEndPilot().run(
            intake(), expected_policy_id=POLICY,
            expected_policy_hash=POLICY_HASH, authority=authority,
            rollback=rollback, stale_authorization=authorization,
            expected_rollback_hash=ROLLBACK_HASH,
        )


def test_end_to_end_pilot_rejects_kill_switch_boundary_failure():
    authority, rollback, authorization = boundary()
    authority["kill_switch_active"] = True
    report = EndToEndPilot().run(
        intake(), expected_policy_id=POLICY, expected_policy_hash=POLICY_HASH,
        authority=authority, rollback=rollback, stale_authorization=authorization,
        expected_rollback_hash=ROLLBACK_HASH,
    )
    assert report.kill_switch_verified and report.blocked
