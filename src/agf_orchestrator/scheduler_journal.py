"""Atomic bounded scheduler event journal and human inbox."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
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
        return event

    def _inbox_from_dict(self, payload: dict) -> InboxItem:
        legacy = {
            "inbox_id", "project_id", "scheduler_id", "title", "summary",
            "required_action", "status",
        }
        extended = legacy | {
            "decision_id", "task_id", "risk_class", "failed_gates", "pending_gates",
            "evidence_refs", "policy_id", "policy_hash",
        }
        if not isinstance(payload, dict) or set(payload) not in (legacy, extended):
            raise SchedulerJournalError("inbox schema is invalid")
        if set(payload) == legacy:
            payload = {
                **payload, "decision_id": "", "task_id": "", "risk_class": "",
                "failed_gates": (), "pending_gates": (), "evidence_refs": (),
                "policy_id": "", "policy_hash": "",
            }
        else:
            payload = {
                **payload,
                "failed_gates": tuple(payload["failed_gates"]),
                "pending_gates": tuple(payload["pending_gates"]),
                "evidence_refs": tuple(payload["evidence_refs"]),
            }
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
                          item.policy_id, item.policy_hash))
        if structured and not item.decision_id:
            raise SchedulerJournalError("inbox decision identity is required")
        if item.decision_id:
            if not re.fullmatch(r"decision-[a-f0-9]{32}", item.decision_id):
                raise SchedulerJournalError("inbox decision identity is invalid")
            if not item.task_id or item.risk_class != "MEDIUM":
                raise SchedulerJournalError("inbox decision context is invalid")
            if item.policy_hash and not re.fullmatch(r"[a-f0-9]{64}", item.policy_hash):
                raise SchedulerJournalError("inbox policy hash is invalid")
            self._bounded_list(item.failed_gates)
            self._bounded_list(item.pending_gates)
            self._bounded_list(item.evidence_refs)
            self._bounded_text(item.task_id)
            self._bounded_text(item.policy_id)

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
