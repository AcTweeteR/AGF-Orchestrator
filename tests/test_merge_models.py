import pytest

from agf_orchestrator.merge_models import (
    AuthorizationStatus,
    DecisionStatus,
    GateEvidence,
    GateStatus,
    MergeValidationError,
    RiskClass,
    decision_from_dict,
)


def gate(name="plan", status=GateStatus.PASS):
    return GateEvidence(name, status, (f"evidence-{name}",), "2026-08-07T10:00:00Z")


def decision(**overrides):
    values = {
        "schema_version": "1.0",
        "decision_id": "decision-abc123",
        "project_id": "project-agf-orchestrator",
        "task_id": "task-e6-t1",
        "base_sha": "a" * 40,
        "delivery_sha": "b" * 40,
        "risk_class": RiskClass.LOW,
        "gates": (gate(),),
        "policy_id": "policy-agf",
        "policy_version": "1",
        "constitution_id": "constitution-v1",
        "decision_status": DecisionStatus.BLOCKED,
        "authorization_status": AuthorizationStatus.NOT_AUTHORIZED,
        "blocking_reasons": ("missing gate: compliance",),
        "evidence_hash": "c" * 64,
        "integrity_hash": "d" * 64,
    }
    values.update(overrides)
    from agf_orchestrator.merge_models import MergeDecision

    return MergeDecision(**values)


def test_decision_rejects_authorized_blocked_state():
    with pytest.raises(MergeValidationError, match="blocked decisions cannot authorize"):
        decision(authorization_status=AuthorizationStatus.AUTHORIZED).validate()


def test_decision_round_trip_requires_integrity():
    item = decision()
    payload = item.to_dict()
    with pytest.raises(MergeValidationError, match="integrity hash"):
        decision_from_dict(payload)


def test_gate_rejects_secret_shaped_detail():
    with pytest.raises(MergeValidationError, match="detail is invalid"):
        GateEvidence("plan", GateStatus.PASS, ("evidence",), detail="token: hidden").validate()
