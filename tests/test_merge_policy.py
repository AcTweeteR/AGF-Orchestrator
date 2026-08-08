import pytest

from agf_orchestrator.git_delivery import GitDeliveryError, _validate_delivery_authorization
from agf_orchestrator.merge_models import GateEvidence, GateStatus, RiskClass
from agf_orchestrator.merge_policy import REQUIRED_GATES, MergePolicy, MergePolicyEngine
from agf_orchestrator.risk_engine import assess_risk
from agf_orchestrator.risk_models import RollbackDifficulty

BASE = "a" * 40
DELIVERY = "b" * 40


def gates(status=GateStatus.PASS):
    return [
        GateEvidence(name, status, (f"ref-{name}",), "2026-08-07T10:00:00Z")
        for name in REQUIRED_GATES
    ]


def engine():
    return MergePolicyEngine(MergePolicy("policy-agf", "1", require_human_merge=False))


def evaluate(gate_values=None, risk=RiskClass.LOW):
    return engine().evaluate(
        project_id="project-efc8e8ef7be7050b",
        task_id="task-e6-t1",
        base_sha=BASE,
        delivery_sha=DELIVERY,
        constitution_id="constitution-v1",
        risk_class=risk,
        gates=gate_values if gate_values is not None else gates(),
        expiry="2026-08-08T00:00:00Z",
    )


def test_complete_low_risk_evidence_is_deterministic_and_serializable():
    first = evaluate()
    second = evaluate(list(reversed(gates())))
    assert first.to_dict() == second.to_dict()
    assert first.decision_status.value == "ELIGIBLE"
    assert first.authorization_status.value == "AUTHORIZED"
    assert first.verify_integrity()


def test_missing_gate_blocks_and_cannot_authorize():
    item = evaluate(gates()[:-1])
    assert item.decision_status.value == "BLOCKED"
    assert item.authorization_status.value == "NOT_AUTHORIZED"
    assert "missing gate: delivery_branch" in item.blocking_reasons


def test_failed_stale_and_contradictory_evidence_block():
    for status in (GateStatus.FAIL, GateStatus.STALE, GateStatus.CONTRADICTORY):
        item = evaluate(
            [gate for gate in gates() if gate.name != "remote_state"]
            + [GateEvidence("remote_state", status, ("ref",))]
        )
        assert item.decision_status.value == "BLOCKED"
        assert any("remote_state" in reason for reason in item.blocking_reasons)


def test_forbidden_risk_classes_never_authorize():
    for risk in (RiskClass.HIGH, RiskClass.CRITICAL, RiskClass.UNKNOWN):
        item = evaluate(risk=risk)
        assert item.authorization_status.value == "NOT_AUTHORIZED"
        assert any("forbidden" in reason for reason in item.blocking_reasons)


def test_human_merge_policy_blocks_even_complete_evidence():
    item = MergePolicyEngine(MergePolicy("policy-agf", "1")).evaluate(
        project_id="project-agf-orchestrator",
        task_id="task-e6-t1",
        base_sha=BASE,
        delivery_sha=DELIVERY,
        constitution_id="constitution-v1",
        risk_class=RiskClass.LOW,
        gates=gates(),
    )
    assert item.decision_status.value == "BLOCKED"
    assert "human merge approval is required by policy" in item.blocking_reasons


def test_risk_engine_assessment_cannot_be_lowered_by_caller():
    assessment = assess_risk(
        assessment_id="risk-protected", project_id="project-efc8e8ef7be7050b",
        task_id="task-e6-t1", changed_paths=("constitution.py",),
        protected_paths=("constitution.py",), rollback_difficulty=RollbackDifficulty.EASY,
        incident_count=0, reviewer_blockers=0, validation_passed=True,
        evidence_refs=("risk-evidence",),
    )
    with pytest.raises(ValueError, match="does not match Risk Engine"):
        engine().evaluate(
            project_id="project-efc8e8ef7be7050b", task_id="task-e6-t1",
            base_sha=BASE, delivery_sha=DELIVERY, constitution_id="constitution-v1",
            risk_class=RiskClass.LOW, gates=gates(), risk_assessment=assessment,
        )


def test_low_risk_authorization_is_accepted_only_with_complete_evidence():
    item = evaluate()
    assert _validate_delivery_authorization(
        item, task_id="task-e6-t1", base_sha=BASE, delivery_sha=DELIVERY
    ) == item


def test_low_risk_authorization_rejects_tampering_and_non_low_decisions():
    item = evaluate()
    tampered = item.to_dict()
    tampered["risk_class"] = "HIGH"
    with pytest.raises(GitDeliveryError, match="integrity|invalid merge authorization"):
        _validate_delivery_authorization(
            tampered, task_id="task-e6-t1", base_sha=BASE, delivery_sha=DELIVERY
        )
    forbidden = evaluate(risk=RiskClass.HIGH)
    with pytest.raises(GitDeliveryError, match="eligible|authorized|LOW"):
        _validate_delivery_authorization(
            forbidden, task_id="task-e6-t1", base_sha=BASE, delivery_sha=DELIVERY
        )
