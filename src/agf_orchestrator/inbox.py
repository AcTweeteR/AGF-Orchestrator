"""Concise Director inbox derived from persisted sessions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .merge_models import GateStatus, MergeDecision, MergeValidationError
from .project_registry import ProjectRegistry
from .scheduler_journal import InboxItem as JournalInboxItem
from .scheduler_journal import SchedulerJournal
from .session_models import SessionStatus
from .session_store import SessionStore


@dataclass(frozen=True)
class InboxItem:
    priority: str
    project: str
    session_id: str
    status: str
    summary: str
    blocking_reason: str
    required_action: str
    pr_url: str | None
    updated_at: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MediumRiskSummary:
    """Bounded human-action summary derived from one MEDIUM decision."""

    inbox_id: str
    decision_id: str
    project_id: str
    task_id: str
    risk_class: str
    failed_gates: tuple[str, ...]
    pending_gates: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_id: str
    policy_hash: str
    summary: str
    required_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "inbox_id": self.inbox_id,
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "risk_class": self.risk_class,
            "failed_gates": list(self.failed_gates),
            "pending_gates": list(self.pending_gates),
            "evidence_refs": list(self.evidence_refs),
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "summary": self.summary,
            "required_action": self.required_action,
        }


@dataclass(frozen=True)
class HumanEscalation:
    """Bounded, non-authorizing escalation for unresolved risk."""

    inbox_id: str
    decision_id: str
    project_id: str
    task_id: str
    risk_class: str
    failed_gates: tuple[str, ...]
    pending_gates: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_id: str
    policy_hash: str
    summary: str
    required_action: str

    def to_dict(self) -> dict[str, Any]:
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class ExecutiveDecisionSummary:
    """Stable, bounded summary of one persisted merge decision."""

    inbox_id: str
    decision_id: str
    project_id: str
    task_id: str
    risk_class: str
    decision_status: str
    authorization_status: str
    blocking_reasons: tuple[str, ...]
    failed_gates: tuple[str, ...]
    pending_gates: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy_id: str
    policy_hash: str
    summary: str
    required_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in self.__dict__.items()
        }


def build_executive_summary(
    decision: MergeDecision,
    *,
    inbox_id: str,
    scheduler_id: str,
) -> ExecutiveDecisionSummary:
    """Build a deterministic, secret-safe summary without merge authority."""
    decision.validate()
    if not decision.verify_integrity():
        raise MergeValidationError("decision integrity is invalid")
    if not re.fullmatch(r"inbox-[0-9]{6,80}", inbox_id):
        raise MergeValidationError("inbox identity is invalid")
    if not re.fullmatch(r"scheduler-[a-z0-9][a-z0-9-]{0,79}", scheduler_id):
        raise MergeValidationError("scheduler identity is invalid")
    failed = tuple(sorted(
        gate.name for gate in decision.gates
        if gate.status in {
            GateStatus.FAIL, GateStatus.STALE, GateStatus.CONTRADICTORY,
            GateStatus.UNKNOWN,
        }
    ))
    pending = tuple(sorted(
        gate.name for gate in decision.gates if gate.status is GateStatus.MISSING
    ))
    blockers = tuple(sorted(set(decision.blocking_reasons)))
    refs = tuple(sorted({ref for gate in decision.gates for ref in gate.evidence_refs}))
    blockers_text = ", ".join(blockers) or "none"
    failed_text = ", ".join(failed) or "none"
    pending_text = ", ".join(pending) or "none"
    summary = (
        f"{decision.risk_class.value} decision {decision.decision_id} for task "
        f"{decision.task_id}: status={decision.decision_status.value}; "
        f"authorization status {decision.authorization_status.value}; blockers={blockers_text}; "
        f"failed gates={failed_text}; pending gates={pending_text}."
    )
    if len(summary) > 4000:
        raise MergeValidationError("executive summary exceeds bounded size")
    if decision.authorization_status.value == "AUTHORIZED":
        action = "Retain the decision record; delivery remains separately governed."
    else:
        action = "Review the bounded evidence and resolve all blockers before proceeding."
    return ExecutiveDecisionSummary(
        inbox_id=inbox_id, decision_id=decision.decision_id,
        project_id=decision.project_id, task_id=decision.task_id,
        risk_class=decision.risk_class.value,
        decision_status=decision.decision_status.value,
        authorization_status=decision.authorization_status.value,
        blocking_reasons=blockers, failed_gates=failed, pending_gates=pending,
        evidence_refs=refs, policy_id=decision.policy_id,
        policy_hash=decision.policy_hash, summary=summary, required_action=action,
    )


def persist_executive_summary(
    journal: SchedulerJournal,
    decision: MergeDecision,
    *,
    inbox_id: str,
) -> ExecutiveDecisionSummary:
    """Persist an idempotent project-isolated executive decision summary."""
    summary = build_executive_summary(
        decision, inbox_id=inbox_id, scheduler_id=journal.scheduler_id
    )
    journal.add_inbox(JournalInboxItem(
        inbox_id=summary.inbox_id, project_id=summary.project_id,
        scheduler_id=journal.scheduler_id, title="Executive merge decision summary",
        summary=summary.summary, required_action=summary.required_action,
        decision_id=summary.decision_id, task_id=summary.task_id,
        risk_class=summary.risk_class, failed_gates=summary.failed_gates,
        pending_gates=summary.pending_gates, evidence_refs=summary.evidence_refs,
        policy_id=summary.policy_id, policy_hash=summary.policy_hash,
        decision_status=summary.decision_status,
        authorization_status=summary.authorization_status,
        blocking_reasons=summary.blocking_reasons,
    ))
    return summary


def build_medium_risk_summary(
    decision: MergeDecision,
    *,
    inbox_id: str,
    scheduler_id: str,
) -> MediumRiskSummary:
    """Convert an evidenced MEDIUM decision into bounded inbox context."""
    try:
        decision.validate()
    except MergeValidationError:
        raise
    if decision.risk_class.value != "MEDIUM":
        raise MergeValidationError("only MEDIUM decisions produce this summary")
    if not re.fullmatch(r"inbox-[0-9]{6,80}", inbox_id):
        raise MergeValidationError("inbox identity is invalid")
    if not re.fullmatch(r"scheduler-[a-z0-9][a-z0-9-]{0,79}", scheduler_id):
        raise MergeValidationError("scheduler identity is invalid")
    failed = tuple(sorted(
        gate.name for gate in decision.gates
        if gate.status in {
            GateStatus.FAIL, GateStatus.STALE, GateStatus.CONTRADICTORY,
            GateStatus.UNKNOWN,
        }
    ))
    pending = tuple(sorted(
        gate.name for gate in decision.gates if gate.status is GateStatus.MISSING
    ))
    refs = tuple(sorted({ref for gate in decision.gates for ref in gate.evidence_refs}))
    summary = (
        f"MEDIUM decision {decision.decision_id} for task {decision.task_id} "
        f"requires human action; failed gates: {', '.join(failed) or 'none'}; "
        f"pending gates: {', '.join(pending) or 'none'}."
    )
    if len(summary) > 4000:
        raise MergeValidationError("medium summary exceeds bounded size")
    return MediumRiskSummary(
        inbox_id=inbox_id, decision_id=decision.decision_id,
        project_id=decision.project_id, task_id=decision.task_id,
        risk_class=decision.risk_class.value, failed_gates=failed,
        pending_gates=pending, evidence_refs=refs, policy_id=decision.policy_id,
        policy_hash=decision.policy_hash, summary=summary,
        required_action="Review the bounded evidence and record an explicit human decision.",
    )


def persist_medium_risk_summary(
    journal: SchedulerJournal,
    decision: MergeDecision,
    *,
    inbox_id: str,
) -> MediumRiskSummary:
    """Persist an idempotent MEDIUM summary in the project-isolated journal."""
    summary = build_medium_risk_summary(
        decision, inbox_id=inbox_id, scheduler_id=journal.scheduler_id
    )
    journal.add_inbox(JournalInboxItem(
        inbox_id=summary.inbox_id, project_id=summary.project_id,
        scheduler_id=journal.scheduler_id, title="MEDIUM merge decision",
        summary=summary.summary, required_action=summary.required_action,
        decision_id=summary.decision_id, task_id=summary.task_id,
        risk_class=summary.risk_class, failed_gates=summary.failed_gates,
        pending_gates=summary.pending_gates, evidence_refs=summary.evidence_refs,
        policy_id=summary.policy_id, policy_hash=summary.policy_hash,
    ))
    return summary


def build_human_escalation(
    decision: MergeDecision,
    *,
    inbox_id: str,
    scheduler_id: str,
) -> HumanEscalation:
    """Route HIGH, CRITICAL, and UNKNOWN decisions to explicit human action."""
    decision.validate()
    if decision.risk_class.value not in {"HIGH", "CRITICAL", "UNKNOWN"}:
        raise MergeValidationError("only HIGH, CRITICAL, or UNKNOWN decisions escalate")
    if not re.fullmatch(r"inbox-[0-9]{6,80}", inbox_id):
        raise MergeValidationError("inbox identity is invalid")
    if not re.fullmatch(r"scheduler-[a-z0-9][a-z0-9-]{0,79}", scheduler_id):
        raise MergeValidationError("scheduler identity is invalid")
    failed = tuple(sorted(
        gate.name for gate in decision.gates
        if gate.status in {
            GateStatus.FAIL, GateStatus.STALE, GateStatus.CONTRADICTORY,
            GateStatus.UNKNOWN,
        }
    ))
    pending = tuple(sorted(gate.name for gate in decision.gates
                           if gate.status is GateStatus.MISSING))
    refs = tuple(sorted({ref for gate in decision.gates for ref in gate.evidence_refs}))
    summary = (
        f"{decision.risk_class.value} decision {decision.decision_id} for task "
        f"{decision.task_id} requires explicit human escalation; failed gates: "
        f"{', '.join(failed) or 'none'}; pending gates: {', '.join(pending) or 'none'}."
    )
    if len(summary) > 4000:
        raise MergeValidationError("human escalation exceeds bounded size")
    return HumanEscalation(
        inbox_id=inbox_id, decision_id=decision.decision_id,
        project_id=decision.project_id, task_id=decision.task_id,
        risk_class=decision.risk_class.value, failed_gates=failed,
        pending_gates=pending, evidence_refs=refs, policy_id=decision.policy_id,
        policy_hash=decision.policy_hash, summary=summary,
        required_action="A human must review the bounded evidence and explicitly decide.",
    )


def persist_human_escalation(
    journal: SchedulerJournal,
    decision: MergeDecision,
    *,
    inbox_id: str,
) -> HumanEscalation:
    escalation = build_human_escalation(
        decision, inbox_id=inbox_id, scheduler_id=journal.scheduler_id
    )
    journal.add_inbox(JournalInboxItem(
        inbox_id=escalation.inbox_id, project_id=escalation.project_id,
        scheduler_id=journal.scheduler_id, title="Human risk escalation",
        summary=escalation.summary, required_action=escalation.required_action,
        decision_id=escalation.decision_id, task_id=escalation.task_id,
        risk_class=escalation.risk_class, failed_gates=escalation.failed_gates,
        pending_gates=escalation.pending_gates, evidence_refs=escalation.evidence_refs,
        policy_id=escalation.policy_id, policy_hash=escalation.policy_hash,
    ))
    return escalation


def build_inbox(store: SessionStore, registry: ProjectRegistry) -> list[InboxItem]:
    projects = {p.project_id: p for p in registry.list()}
    items = []
    for session in store.list():
        if session.status not in {
            SessionStatus.HUMAN_REQUIRED,
            SessionStatus.STALE,
            SessionStatus.BLOCKED,
            SessionStatus.PR_READY,
            SessionStatus.FAILED,
        }:
            continue
        project = projects.get(session.project_id)
        if not project:
            continue
        if session.status is SessionStatus.PR_READY:
            priority, action = "NORMAL", "human merge decision required"
        elif session.status in {SessionStatus.STALE, SessionStatus.HUMAN_REQUIRED}:
            priority, action = "HIGH", "inspect the blocking reason and resume explicitly"
        else:
            priority, action = "HIGH", "resolve the blocked or failed stage"
        items.append(
            InboxItem(
                priority,
                project.name,
                session.session_id,
                session.status.value,
                session.goal,
                "; ".join(session.blocking_issues),
                action,
                session.pr_url,
                session.updated_at,
            )
        )
    ranks = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
    return sorted(items, key=lambda item: (ranks[item.priority], item.project, item.session_id))
