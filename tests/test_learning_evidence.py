from dataclasses import replace

import pytest

from agf_orchestrator.learning_evidence import (
    LearningEvidence,
    LearningEvidenceError,
    LearningEvidenceLedger,
    OutcomeStatus,
    evidence_from_dict,
    evidence_hash,
)

PROJECT = "project-efc8e8ef7be7050b"


def make_evidence(
    observation="observation-001", outcome=OutcomeStatus.SUCCESS, source="pilot:test"
):
    candidate = LearningEvidence(
        "1.0", "learning-001", PROJECT, observation, "subject-codex", outcome,
        1, source, "2026-08-10T12:00:00Z", "0" * 64,
    )
    return replace(candidate, content_sha256=evidence_hash(candidate))


def test_valid_evidence_accepts_and_round_trips():
    current = make_evidence()
    current.validate()
    assert evidence_from_dict(current.to_dict()) == current


def test_ledger_is_idempotent_and_project_isolated():
    ledger = LearningEvidenceLedger(PROJECT)
    current = make_evidence()
    assert ledger.record(current)
    assert not ledger.record(current)
    other_project = replace(current, project_id="project-other", content_sha256="0" * 64)
    other_project = replace(other_project, content_sha256=evidence_hash(other_project))
    with pytest.raises(LearningEvidenceError, match="project binding"):
        ledger.record(other_project)


def test_contradictory_evidence_is_rejected():
    ledger = LearningEvidenceLedger(PROJECT)
    ledger.record(make_evidence())
    conflict = make_evidence(outcome=OutcomeStatus.FAILURE)
    with pytest.raises(LearningEvidenceError, match="contradictory"):
        ledger.record(conflict)


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"},
    {"score_delta": 11},
    {"observed_at": "invalid"},
    {"outcome": "UNKNOWN"},
])
def test_malformed_unsupported_or_unbounded_evidence_is_rejected(change):
    payload = make_evidence().to_dict()
    payload.update(change)
    with pytest.raises(LearningEvidenceError):
        evidence_from_dict(payload)


def test_wrongly_typed_identifiers_fail_with_domain_error():
    payload = make_evidence().to_dict()
    payload["evidence_id"] = None
    with pytest.raises(LearningEvidenceError, match="evidence_id"):
        evidence_from_dict(payload)
    payload["evidence_id"] = "learning-001"
    payload["observed_at"] = None
    with pytest.raises(LearningEvidenceError, match="observed_at"):
        evidence_from_dict(payload)
    payload["observed_at"] = "2026-08-10T12:00:00Z"
    payload["content_sha256"] = None
    with pytest.raises(LearningEvidenceError, match="content_sha256"):
        evidence_from_dict(payload)
    with pytest.raises(LearningEvidenceError, match="project_id"):
        LearningEvidenceLedger(None)


def test_secret_shaped_source_is_rejected():
    candidate = make_evidence(source="token:sk-abcdefghijklmnop")
    with pytest.raises(LearningEvidenceError, match="secret"):
        candidate.validate()


def test_content_mutation_cannot_reuse_hash():
    current = make_evidence()
    mutated = replace(current, score_delta=10)
    with pytest.raises(LearningEvidenceError, match="content_sha256"):
        mutated.validate()


def test_restart_readback_preserves_records_and_hash_binding():
    ledger = LearningEvidenceLedger(PROJECT)
    current = make_evidence()
    ledger.record(current)
    restored = LearningEvidenceLedger.from_state(ledger.export_state())
    assert restored.get(current.observation_id) == current
    tampered = ledger.export_state().replace(current.content_sha256, "0" * 64)
    with pytest.raises(LearningEvidenceError, match="state hash"):
        LearningEvidenceLedger.from_state(tampered)
