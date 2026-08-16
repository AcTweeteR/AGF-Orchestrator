from agf_orchestrator.causal_findings import CausalFindingStore, finding_identity

PROJECT = "project-0123456789abcdef"
BASELINE = "a" * 40
PROPOSAL = {
    "rationale": "A reproduced runtime defect has a bounded fix.",
    "tasks": [{"task_id": "task-001", "objective": "repair resolver"}],
    "prohibited_paths": [".env"],
    "required_evidence": ["baseline reproduction"],
    "validation_requirements": ["python3 -m pytest"],
    "risk_indicators": ["runtime compatibility"],
}


def record(store):
    return store.record(
        project_id=PROJECT,
        baseline_sha=BASELINE,
        target_identity="https://example.test/agf.git",
        symptom="validation executable cannot be resolved: python",
        reproduction="validate_commands([python -m pytest])",
        observed_error="python unavailable; python3 available",
        evidence_refs=("baseline-reproduction.json", "architect-response.json"),
        proposal=PROPOSAL,
    )


def test_reproduced_finding_survives_no_justified_work_and_restart(tmp_path):
    first = record(CausalFindingStore(tmp_path))
    restarted = CausalFindingStore(tmp_path)
    active = restarted.active(PROJECT, baseline_sha=BASELINE)
    assert [item.finding_id for item in active] == [first.finding_id]
    assert active[0].proposal == PROPOSAL


def test_record_is_idempotent_and_does_not_duplicate_tasks_or_intents(tmp_path):
    store = CausalFindingStore(tmp_path)
    first = record(store)
    second = record(store)
    assert first.finding_id == second.finding_id
    assert len(store.active(PROJECT)) == 1
    assert first.finding_id == finding_identity(
        PROJECT,
        BASELINE,
        "validation executable cannot be resolved: python",
        "validate_commands([python -m pytest])",
    )


def test_delivered_finding_closes_and_is_idempotent(tmp_path):
    store = CausalFindingStore(tmp_path)
    finding = record(store)
    closed = store.close(
        PROJECT, finding.finding_id, reason="DELIVERED", evidence_refs=("receipt.json",)
    )
    assert closed.status == "CLOSED"
    assert store.active(PROJECT) == []
    assert store.close(
        PROJECT, finding.finding_id, reason="DELIVERED", evidence_refs=("receipt.json",)
    ) == closed


def test_independent_non_reproduction_closes_with_evidence(tmp_path):
    store = CausalFindingStore(tmp_path)
    finding = record(store)
    closed = store.close(
        PROJECT,
        finding.finding_id,
        reason="NO_LONGER_REPRODUCIBLE",
        evidence_refs=("independent-reproduction.json",),
    )
    assert closed.status == "CLOSED"


def test_closed_finding_cannot_be_reopened_implicitly(tmp_path):
    store = CausalFindingStore(tmp_path)
    finding = record(store)
    store.close(PROJECT, finding.finding_id, reason="DELIVERED", evidence_refs=("receipt",))
    try:
        record(store)
    except ValueError as exc:
        assert "cannot be reopened" in str(exc)
    else:
        raise AssertionError("closed finding was reopened")
