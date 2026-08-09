from dataclasses import replace

import pytest

from agf_orchestrator.learning_proposals import (
    LearningProposal,
    LearningProposalError,
    LearningProposalLedger,
    ProposalStatus,
    proposal_from_dict,
    proposal_hash,
)

PROJECT = "project-efc8e8ef7be7050b"


def make_proposal(target="diagnostic_note", status=ProposalStatus.OPEN):
    candidate = LearningProposal(
        "1.0", "proposal-001", PROJECT, "summary-codex", target,
        "owner review only", "retain baseline", True, status,
        "2026-08-10T12:00:00Z", "0" * 64,
    )
    return replace(candidate, content_sha256=proposal_hash(candidate))


def test_valid_proposal_is_non_authoritative_and_round_trips():
    current = make_proposal()
    current.validate()
    assert proposal_from_dict(current.to_dict()) == current


def test_protected_target_is_rejected():
    for target in ("merge_policy", "merge_policy ", "merge_policy.value", "Objective"):
        with pytest.raises(LearningProposalError, match="protected"):
            make_proposal(target=target).validate()


def test_proposals_must_be_reversible_and_secret_safe():
    with pytest.raises(LearningProposalError, match="reversible"):
        replace(make_proposal(), reversible=False).validate()
    with pytest.raises(LearningProposalError, match="secret"):
        make_proposal().__class__(
            **{
                **make_proposal().__dict__,
                "rationale": "token:sk-abcdefghijklmnop",
                "content_sha256": "0" * 64,
            }
        ).validate()


def test_ledger_is_project_isolated_idempotent_and_withdrawable():
    ledger = LearningProposalLedger(PROJECT)
    current = make_proposal()
    assert ledger.record(current)
    assert not ledger.record(current)
    withdrawn = ledger.withdraw(current.proposal_id)
    assert withdrawn.status is ProposalStatus.WITHDRAWN
    with pytest.raises(LearningProposalError, match="project binding"):
        other = replace(current, project_id="project-other", content_sha256="0" * 64)
        ledger.record(replace(other, content_sha256=proposal_hash(other)))


def test_restart_readback_and_conflict_rejection():
    ledger = LearningProposalLedger(PROJECT)
    current = make_proposal()
    ledger.record(current)
    restored = LearningProposalLedger.from_state(ledger.export_state())
    assert restored.get(current.proposal_id) == current
    conflict = replace(current, proposed_value="change", content_sha256="0" * 64)
    with pytest.raises(LearningProposalError, match="conflicting"):
        ledger.record(replace(conflict, content_sha256=proposal_hash(conflict)))


def test_malformed_proposals_fail_with_domain_error():
    payload = make_proposal().to_dict()
    payload["reversible"] = "yes"
    with pytest.raises(LearningProposalError):
        proposal_from_dict(payload)
    payload = make_proposal().to_dict()
    payload["target"] = None
    with pytest.raises(LearningProposalError):
        proposal_from_dict(payload)
