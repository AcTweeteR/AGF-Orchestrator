"""Atomic bounded scheduler event journal and human inbox."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .locking import project_lock
from .scheduler_loop import SchedulerEvent


class SchedulerJournalError(RuntimeError):
    """Raised when journal or inbox data cannot be safely persisted."""


_ID = re.compile(r"^(?:event|inbox)-[0-9]{6,80}$")
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")
_MAX_TEXT = 4000
_MAX_RECORDS = 500


class InboxStatus:
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class InboxItem:
    inbox_id: str
    project_id: str
    scheduler_id: str
    title: str
    summary: str
    required_action: str
    status: str = InboxStatus.OPEN
    decision_id: str = ""
    task_id: str = ""
    risk_class: str = ""
    failed_gates: tuple[str, ...] = ()
    pending_gates: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    policy_id: str = ""
    policy_hash: str = ""
    uncertainty_kind: str = ""
    decision_status: str = ""
    authorization_status: str = ""
    blocking_reasons: tuple[str, ...] = ()
    resolution_actor: str = ""
    resolution_outcome: str = ""
    resolved_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "inbox_id": self.inbox_id,
            "project_id": self.project_id,
            "scheduler_id": self.scheduler_id,
            "title": self.title,
            "summary": self.summary,
            "required_action": self.required_action,
            "status": self.status,
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "risk_class": self.risk_class,
            "failed_gates": list(self.failed_gates),
            "pending_gates": list(self.pending_gates),
            "evidence_refs": list(self.evidence_refs),
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "uncertainty_kind": self.uncertainty_kind,
            "decision_status": self.decision_status,
            "authorization_status": self.authorization_status,
            "blocking_reasons": list(self.blocking_reasons),
            "resolution_actor": self.resolution_actor,
            "resolution_outcome": self.resolution_outcome,
            "resolved_at": self.resolved_at,
        }


class SchedulerJournal:
    schema_version = "1.0"

    def __init__(self, state_dir: str | Path, project_id: str, scheduler_id: str):
        if not project_id.startswith("project-") or "/" in project_id or "\\" in project_id:
            raise SchedulerJournalError("project identity is invalid")
        if not scheduler_id.startswith("scheduler-") or "/" in scheduler_id or "\\" in scheduler_id:
            raise SchedulerJournalError("scheduler identity is invalid")
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_id = project_id
        self.scheduler_id = scheduler_id
        self.path = self.state_dir / "schedulers" / project_id / f"{scheduler_id}.journal.json"

    def append_event(self, event: SchedulerEvent) -> SchedulerEvent:
        if not _ID.fullmatch(event.event_id) or event.sequence < 1:
            raise SchedulerJournalError("event is invalid")
        self._bounded_text(event.summary)
        with project_lock(self.state_dir, self.project_id, "scheduler-event", timeout=5.0):
            events, inbox = self._load_unlocked()
            existing = next((item for item in events if item.event_id == event.event_id), None)
            if existing is not None:
                if existing == event:
                    return existing
                raise SchedulerJournalError("event ID already exists with different content")
            if events and event.sequence != events[-1].sequence + 1:
                raise SchedulerJournalError("event sequence is not monotonic")
            self._save_unlocked(events + [event], inbox)
        return event

    def add_inbox(self, item: InboxItem) -> InboxItem:
        self._validate_inbox(item)
        with project_lock(self.state_dir, self.project_id, "scheduler-inbox", timeout=5.0):
            events, inbox = self._load_unlocked()
            existing = next(
                (candidate for candidate in inbox if candidate.inbox_id == item.inbox_id), None
            )
            if existing is not None:
                if existing == item:
                    return existing
                raise SchedulerJournalError("inbox ID already exists with different content")
            self._save_unlocked(events, inbox + [item])
        return item

    def audit(self, *, limit: int = 50) -> tuple[SchedulerEvent, ...]:
        self._validate_limit(limit)
        with project_lock(self.state_dir, self.project_id, "scheduler-audit", timeout=5.0):
            events, _ = self._load_unlocked()
        return tuple(events[-limit:])

    def open_inbox(self, *, limit: int = 50) -> tuple[InboxItem, ...]:
        self._validate_limit(limit)
        with project_lock(self.state_dir, self.project_id, "scheduler-inbox-list", timeout=5.0):
            _, inbox = self._load_unlocked()
        return tuple(
            sorted(
                (item for item in inbox if item.status == InboxStatus.OPEN),
                key=lambda item: item.inbox_id,
            )[:limit]
        )

    def resolve_inbox(self, inbox_id: str, *, actor: str, outcome: str) -> InboxItem:
        """Record an explicit human resolution idempotently."""
        if not isinstance(inbox_id, str) or not inbox_id.startswith("inbox-"):
            raise SchedulerJournalError("inbox ID is invalid")
        self._bounded_text(actor)
        self._bounded_text(outcome)
        with project_lock(self.state_dir, self.project_id, "scheduler-inbox-resolve", timeout=5.0):
            events, inbox = self._load_unlocked()
            existing = next((item for item in inbox if item.inbox_id == inbox_id), None)
            if existing is None:
                raise SchedulerJournalError("inbox ID does not exist")
            if existing.status == InboxStatus.RESOLVED:
                if (existing.resolution_actor, existing.resolution_outcome) != (actor, outcome):
                    raise SchedulerJournalError("inbox resolution conflicts with existing result")
                return existing
            resolved = replace(
                existing, status=InboxStatus.RESOLVED, resolution_actor=actor,
                resolution_outcome=outcome, resolved_at=_now(),
            )
            self._save_unlocked(
                events, [resolved if item.inbox_id == inbox_id else item for item in inbox]
            )
            return resolved

    def _load_unlocked(self) -> tuple[list[SchedulerEvent], list[InboxItem]]:
        if not self.path.exists():
            return [], []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.schema_version:
                raise SchedulerJournalError("unsupported scheduler journal schema")
            events = [self._event_from_dict(item) for item in payload.get("events", [])]
            inbox = [self._inbox_from_dict(item) for item in payload.get("inbox", [])]
            if len(events) > _MAX_RECORDS or len(inbox) > _MAX_RECORDS:
                raise SchedulerJournalError("scheduler journal is unbounded")
            return events, inbox
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, SchedulerJournalError):
                raise
            raise SchedulerJournalError(f"invalid scheduler journal: {exc}") from exc

    def _save_unlocked(self, events: list[SchedulerEvent], inbox: list[InboxItem]) -> None:
        if len(events) > _MAX_RECORDS or len(inbox) > _MAX_RECORDS:
            raise SchedulerJournalError("scheduler journal capacity exceeded")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".journal.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {"schema_version": self.schema_version,
                     "events": [event.to_dict() for event in events],
                     "inbox": [item.to_dict() for item in inbox]},
                    handle, ensure_ascii=False, indent=2, sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise SchedulerJournalError(f"scheduler journal write failed: {exc}") from exc

    @staticmethod
    def _event_from_dict(payload: dict) -> SchedulerEvent:
        required = {"event_id", "sequence", "event_type", "from_status", "to_status", "summary"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise SchedulerJournalError("event schema is invalid")
        event = SchedulerEvent(**payload)
        if not _ID.fullmatch(event.event_id) or event.sequence < 1:
            raise SchedulerJournalError("event is invalid")
        SchedulerJournal._bounded_text(event.summary)
        return event

    def _inbox_from_dict(self, payload: dict) -> InboxItem:
        legacy = {
            "inbox_id", "project_id", "scheduler_id", "title", "summary",
            "required_action", "status",
        }
        old_extended = legacy | {
            "decision_id", "task_id", "risk_class", "failed_gates", "pending_gates",
            "evidence_refs", "policy_id", "policy_hash",
        }
        extended = old_extended | {
            "uncertainty_kind",
        }
        executive = extended | {
            "decision_status", "authorization_status", "blocking_reasons",
        }
        old_resolved = old_extended | {"resolution_actor", "resolution_outcome", "resolved_at"}
        resolved = extended | {"resolution_actor", "resolution_outcome", "resolved_at"}
        executive_resolved = executive | {"resolution_actor", "resolution_outcome", "resolved_at"}
        if not isinstance(payload, dict) or set(payload) not in (
            legacy, old_extended, extended, executive, old_resolved, resolved,
            executive_resolved,
        ):
            raise SchedulerJournalError("inbox schema is invalid")
        if set(payload) == legacy:
            payload = {
                **payload, "decision_id": "", "task_id": "", "risk_class": "",
                "failed_gates": (), "pending_gates": (), "evidence_refs": (),
                "policy_id": "", "policy_hash": "", "resolution_actor": "",
                "uncertainty_kind": "", "resolution_outcome": "", "resolved_at": "",
                "decision_status": "", "authorization_status": "", "blocking_reasons": (),
            }
        elif set(payload) == old_extended:
            payload = {
                **payload, "uncertainty_kind": "", "resolution_actor": "",
                "resolution_outcome": "", "resolved_at": "", "decision_status": "",
                "authorization_status": "", "blocking_reasons": (),
            }
        elif set(payload) == old_resolved:
            payload = {
                **payload, "uncertainty_kind": "", "decision_status": "",
                "authorization_status": "", "blocking_reasons": (),
            }
        elif set(payload) == extended:
            payload = {
                **payload, "resolution_actor": "", "resolution_outcome": "", "resolved_at": "",
                "decision_status": "", "authorization_status": "", "blocking_reasons": (),
            }
        elif set(payload) == executive:
            payload = {
                **payload, "resolution_actor": "", "resolution_outcome": "", "resolved_at": "",
            }
        for field in ("failed_gates", "pending_gates", "evidence_refs", "blocking_reasons"):
            value = payload[field]
            if isinstance(value, list):
                if any(not isinstance(entry, str) for entry in value):
                    raise SchedulerJournalError("inbox evidence list is invalid")
                payload[field] = tuple(value)
            elif not isinstance(value, tuple):
                raise SchedulerJournalError("inbox evidence list is invalid")
        item = InboxItem(**payload)
        self._validate_inbox(item)
        return item

    def _validate_inbox(self, item: InboxItem) -> None:
        if item.project_id != self.project_id or item.scheduler_id != self.scheduler_id:
            raise SchedulerJournalError("inbox identity does not match journal")
        if not _ID.fullmatch(item.inbox_id) or not item.inbox_id.startswith("inbox-"):
            raise SchedulerJournalError("inbox ID is invalid")
        if item.status not in {InboxStatus.OPEN, InboxStatus.RESOLVED}:
            raise SchedulerJournalError("inbox status is invalid")
        for value in (item.title, item.summary, item.required_action):
            self._bounded_text(value)
        structured = any((item.decision_id, item.task_id, item.risk_class,
                          item.failed_gates, item.pending_gates, item.evidence_refs,
                          item.policy_id, item.policy_hash, item.decision_status,
                          item.authorization_status, item.blocking_reasons))
        if structured and not item.decision_id:
            raise SchedulerJournalError("inbox decision identity is required")
        if item.decision_id:
            if not re.fullmatch(r"decision-[a-f0-9]{32}", item.decision_id):
                raise SchedulerJournalError("inbox decision identity is invalid")
            if not item.task_id or item.risk_class not in {"MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}:
                raise SchedulerJournalError("inbox decision context is invalid")
            if item.decision_status and item.decision_status not in {"ELIGIBLE", "BLOCKED"}:
                raise SchedulerJournalError("inbox decision status is invalid")
            if item.authorization_status and item.authorization_status not in {
                "AUTHORIZED", "NOT_AUTHORIZED"
            }:
                raise SchedulerJournalError("inbox authorization status is invalid")
            if item.policy_hash and not re.fullmatch(r"[a-f0-9]{64}", item.policy_hash):
                raise SchedulerJournalError("inbox policy hash is invalid")
            self._bounded_list(item.failed_gates)
            self._bounded_list(item.pending_gates)
            self._bounded_list(item.evidence_refs)
            self._bounded_list(item.blocking_reasons)
            self._bounded_text(item.task_id)
            self._bounded_text(item.policy_id)
            if item.uncertainty_kind and item.uncertainty_kind not in {
                "UNAVAILABLE", "DIVERGENT", "CONTRADICTORY"
            }:
                raise SchedulerJournalError("inbox uncertainty kind is invalid")
        if item.status == InboxStatus.OPEN and any(
            (item.resolution_actor, item.resolution_outcome, item.resolved_at)
        ):
            raise SchedulerJournalError("open inbox cannot contain resolution")
        if item.status == InboxStatus.RESOLVED:
            self._bounded_text(item.resolution_actor)
            self._bounded_text(item.resolution_outcome)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", item.resolved_at):
                raise SchedulerJournalError("resolved inbox timestamp is invalid")

    @classmethod
    def _bounded_list(cls, values: tuple[str, ...]) -> None:
        if not isinstance(values, tuple) or len(values) > _MAX_RECORDS:
            raise SchedulerJournalError("inbox evidence list is invalid")
        for value in values:
            cls._bounded_text(value)

    @staticmethod
    def _bounded_text(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_TEXT
            or _SECRET.search(value)
        ):
            raise SchedulerJournalError("journal text is invalid")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_RECORDS:
            raise SchedulerJournalError("journal limit is invalid")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
