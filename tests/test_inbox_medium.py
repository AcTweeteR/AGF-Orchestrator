import pytest

from agf_orchestrator.inbox import (
    build_medium_risk_summary,
    persist_medium_risk_summary,
)
from agf_orchestrator.merge_models import GateEvidence, GateStatus, RiskClass
from agf_orchestrator.merge_policy import REQUIRED_GATES, MergePolicy, MergePolicyEngine
from agf_orchestrator.risk_models import (
    RiskAssessment,
    RiskLevel,
    RiskSignal,
    RollbackDifficulty,
    SignalLevel,
)
from agf_orchestrator.scheduler_journal import SchedulerJournal, SchedulerJournalError

PROJECT = "project-efc8e8ef7be7050b"


def assessment():
    return RiskAssessment(
        "1.0", "risk-medium-summary", PROJECT, "task-medium",
        RiskLevel.MEDIUM,
        (RiskSignal("signal-medium", "scope", SignalLevel.MEDIUM, "bounded", ("risk-ref",)),),
        RollbackDifficulty.EASY, 0, (), ("risk-ref",),
    )


def decision(status=GateStatus.PASS):
    gates = [GateEvidence(name, status, (f"evidence-{name}",)) for name in REQUIRED_GATES]
    return MergePolicyEngine(MergePolicy("policy-test", "1", (RiskClass.MEDIUM,), False,
                                         (), "a" * 64)).evaluate(
        project_id=PROJECT, task_id="task-medium", base_sha="a" * 40,
        delivery_sha="b" * 40, constitution_id="constitution-v1", risk_class=RiskClass.MEDIUM,
        risk_assessment=assessment(), gates=gates,
    )


def test_medium_summary_is_bounded_and_deterministic():
    first = build_medium_risk_summary(
        decision(GateStatus.FAIL), inbox_id="inbox-000001", scheduler_id="scheduler-main"
    )
    second = build_medium_risk_summary(
        decision(GateStatus.FAIL), inbox_id="inbox-000001", scheduler_id="scheduler-other"
    )
    assert first.to_dict() == second.to_dict()
    assert first.risk_class == "MEDIUM"
    assert set(first.failed_gates) == set(REQUIRED_GATES)
    assert first.pending_gates == ()
    assert "transcript" not in first.summary.lower()
    assert "secret" not in first.summary.lower()


def test_medium_summary_persists_restart_and_idempotent_retry(tmp_path):
    journal = SchedulerJournal(tmp_path, PROJECT, "scheduler-main")
    item = persist_medium_risk_summary(journal, decision(), inbox_id="inbox-000001")
    retried = persist_medium_risk_summary(journal, decision(), inbox_id="inbox-000001")
    reopened = SchedulerJournal(tmp_path, PROJECT, "scheduler-main")
    assert item == retried
    stored = reopened.open_inbox()[0]
    assert stored.decision_id == item.decision_id
    assert stored.risk_class == "MEDIUM"
    assert stored.evidence_refs == tuple(f"evidence-{name}" for name in sorted(REQUIRED_GATES))


def test_medium_summary_requires_medium_and_preserves_project_isolation(tmp_path):
    low = MergePolicyEngine(MergePolicy("policy-test", "1", (RiskClass.LOW,), False)).evaluate(
        project_id=PROJECT, task_id="task-medium", base_sha="a" * 40,
        delivery_sha="b" * 40, constitution_id="constitution-v1", risk_class=RiskClass.LOW,
        gates=[GateEvidence(name, GateStatus.PASS, ("ref",)) for name in REQUIRED_GATES],
    )
    with pytest.raises(ValueError, match="only MEDIUM"):
        build_medium_risk_summary(low, inbox_id="inbox-000002", scheduler_id="scheduler-main")
    foreign = SchedulerJournal(tmp_path, "project-other", "scheduler-main")
    with pytest.raises(SchedulerJournalError, match="identity"):
        persist_medium_risk_summary(foreign, decision(), inbox_id="inbox-000003")


def test_summary_rejects_secret_gate_detail():
    gates = [GateEvidence(name, GateStatus.PASS, ("ref",), detail="token: hidden")
             for name in REQUIRED_GATES]
    with pytest.raises(ValueError, match="invalid"):
        MergePolicyEngine(MergePolicy("policy-test", "1", (RiskClass.MEDIUM,), False)).evaluate(
            project_id=PROJECT, task_id="task-medium", base_sha="a" * 40,
            delivery_sha="b" * 40, constitution_id="constitution-v1",
            risk_class=RiskClass.MEDIUM, risk_assessment=assessment(), gates=gates,
        )
