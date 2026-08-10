import pytest

from agf_orchestrator.project_pilot import BoundaryPilot, BoundaryPilotError

PROJECT = "project-efc8e8ef7be7050b"
POLICY = "policy-adr-0003"
POLICY_HASH = "a" * 64
ROLLBACK_HASH = "b" * 64


def evidence(active=False):
    authority = {
        "project_id": PROJECT, "generation": 3, "kill_switch_active": active,
        "operation_id": "operation-stop-on" if active else "operation-stop-clear",
    }
    rollback = {
        "project_id": PROJECT, "operation_id": "operation-rollback-one",
        "superseded_policy_hash": POLICY_HASH, "restored_policy_hash": ROLLBACK_HASH,
        "previous_generation": 2, "generation": 3, "tombstone_hash": "c" * 64,
    }
    authorization = {
        "project_id": PROJECT, "generation": 2,
        "policy_hash": POLICY_HASH, "status": "HUMAN_REQUIRED", "risk_class": "CRITICAL",
    }
    return authority, rollback, authorization


def test_boundary_pilot_proves_rollback_stale_invalidation_and_no_mutation():
    authority, rollback, authorization = evidence()
    original = (authority.copy(), rollback.copy(), authorization.copy())
    report = BoundaryPilot().audit(
        project_id=PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH,
        authority=authority, rollback=rollback, stale_authorization=authorization,
        expected_rollback_hash=ROLLBACK_HASH,
    )
    assert report.rollback_verified
    assert report.kill_switch_verified
    assert report.stale_authorization_rejected
    assert report.human_boundary_verified
    assert not report.blocked
    assert (authority, rollback, authorization) == original


def test_active_kill_switch_blocks_disposable_pilot():
    authority, rollback, authorization = evidence(active=True)
    assert BoundaryPilot().audit(
        project_id=PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH,
        authority=authority, rollback=rollback, stale_authorization=authorization,
        expected_rollback_hash=ROLLBACK_HASH,
    ).blocked


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda a, r, z: a.update(project_id="project-other"), "project binding"),
        (lambda a, r, z: r.update(generation=4), "generation"),
        (lambda a, r, z: r.update(restored_policy_hash="d" * 64), "pinned"),
        (lambda a, r, z: z.update(status="AUTHORIZED"), "stale authorization"),
        (lambda a, r, z: z.update(risk_class="HIGH"), "stale authorization"),
    ],
)
def test_boundary_mismatch_fails_closed(change, expected):
    authority, rollback, authorization = evidence()
    change(authority, rollback, authorization)
    with pytest.raises(BoundaryPilotError, match=expected):
        BoundaryPilot().audit(
            project_id=PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH,
            authority=authority, rollback=rollback, stale_authorization=authorization,
            expected_rollback_hash=ROLLBACK_HASH,
        )


def test_incomplete_or_wrong_policy_boundary_evidence_fails_closed():
    authority, rollback, authorization = evidence()
    del rollback["tombstone_hash"]
    with pytest.raises(BoundaryPilotError, match="incomplete"):
        BoundaryPilot().audit(
            project_id=PROJECT, policy_id=POLICY, policy_hash=POLICY_HASH,
            authority=authority, rollback=rollback, stale_authorization=authorization,
            expected_rollback_hash=ROLLBACK_HASH,
        )
    authority, rollback, authorization = evidence()
    with pytest.raises(BoundaryPilotError, match="stale authorization"):
        BoundaryPilot().audit(
            project_id=PROJECT, policy_id=POLICY, policy_hash="d" * 64,
            authority=authority, rollback=rollback, stale_authorization=authorization,
            expected_rollback_hash=ROLLBACK_HASH,
        )
