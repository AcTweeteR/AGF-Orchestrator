"""Disposable end-to-end self-audit and controlled-learning pilot."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .learning_evidence import LearningEvidence, LearningEvidenceLedger
from .learning_proposals import (
    LearningProposal,
    LearningProposalLedger,
    ProposalStatus,
    proposal_hash,
)
from .learning_summary import LearningSummary, LearningSummaryError, summarize_evidence


class LearningPilotError(ValueError):
    """Raised when the disposable learning pilot cannot prove its invariants."""


@dataclass(frozen=True)
class LearningPilotEvent:
    name: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class LearningPilotReport:
    project_id: str
    summary_id: str
    proposal_id: str
    restart_verified: bool
    rollback_verified: bool
    events: tuple[LearningPilotEvent, ...]


class LearningPilot:
    """Run bounded evidence->summary->proposal->withdrawal in memory only."""

    def run(
        self,
        evidence: tuple[LearningEvidence, ...],
        *,
        project_id: str,
        now: str,
        prior_score: int | None = None,
    ) -> LearningPilotReport:
        if not evidence:
            raise LearningPilotError("pilot requires evidence")
        evidence_ledger = LearningEvidenceLedger(project_id)
        events: list[LearningPilotEvent] = []
        for item in evidence:
            try:
                evidence_ledger.record(item)
            except ValueError as exc:
                raise LearningPilotError(str(exc)) from exc
        events.append(LearningPilotEvent("evidence", "PASS", f"count={len(evidence)}"))
        summary = self._summarize(evidence, now, prior_score)
        events.append(LearningPilotEvent("summary", "PASS", summary.summary_id))
        proposal = self._proposal(summary, now)
        proposal_ledger = LearningProposalLedger(project_id)
        proposal_ledger.record(proposal)
        events.append(LearningPilotEvent("proposal", "PASS", proposal.proposal_id))
        restored_evidence = LearningEvidenceLedger.from_state(evidence_ledger.export_state())
        restored_proposals = LearningProposalLedger.from_state(proposal_ledger.export_state())
        restored_records = tuple(restored_evidence.get(item.observation_id) for item in evidence)
        restored_summary = self._summarize(restored_records, now, prior_score)
        restart_verified = (
            restored_records == evidence
            and restored_summary == summary
            and restored_proposals.get(proposal.proposal_id) == proposal
        )
        if not restart_verified:
            raise LearningPilotError("restart/readback changed pilot state")
        events.append(LearningPilotEvent("restart", "PASS", "state restored"))
        withdrawn = restored_proposals.withdraw(proposal.proposal_id)
        withdrawn_restored = LearningProposalLedger.from_state(restored_proposals.export_state())
        try:
            withdrawn_restored.record(proposal)
        except ValueError:
            replay_rejected = True
        else:
            replay_rejected = False
        rollback_verified = (
            withdrawn.status is ProposalStatus.WITHDRAWN
            and withdrawn_restored.get(proposal.proposal_id).status is ProposalStatus.WITHDRAWN
            and replay_rejected
        )
        if not rollback_verified:
            raise LearningPilotError("proposal withdrawal did not rollback state")
        events.append(LearningPilotEvent("rollback", "PASS", withdrawn.proposal_id))
        return LearningPilotReport(
            project_id, summary.summary_id, proposal.proposal_id,
            restart_verified, rollback_verified, tuple(events),
        )

    @staticmethod
    def _summarize(
        evidence: tuple[LearningEvidence, ...], now: str, prior_score: int | None
    ) -> LearningSummary:
        try:
            return summarize_evidence(evidence, now=now, prior_score=prior_score)
        except LearningSummaryError as exc:
            raise LearningPilotError(str(exc)) from exc

    @staticmethod
    def _proposal(summary: LearningSummary, now: str) -> LearningProposal:
        proposal = LearningProposal(
            "1.0", f"proposal-{summary.summary_id.removeprefix('summary-')}",
            summary.project_id, summary.summary_id, "diagnostic_note",
            "owner review only", "retain baseline", True, ProposalStatus.OPEN, now, "0" * 64,
        )
        return replace(proposal, content_sha256=proposal_hash(proposal))
