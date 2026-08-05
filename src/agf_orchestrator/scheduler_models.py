"""Immutable scheduler state schema and explicit lifecycle transitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class SchedulerValidationError(ValueError):
    """Raised when scheduler state is invalid or a transition is unsafe."""


class SchedulerStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_SCHEDULER_ID = re.compile(r"^scheduler-[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT_ID = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_ROADMAP_ID = re.compile(r"^roadmap-[a-z0-9][a-z0-9-]{0,79}$")
_ITEM_ID = re.compile(r"^item-[a-z0-9][a-z0-9-]{0,79}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MAX_TEXT = 4000
_MAX_ITEMS = 200
_MAX_BUDGET = 1_000_000_000


@dataclass(frozen=True)
class SchedulerState:
    schema_version: str
    scheduler_id: str
    project_id: str
    roadmap_id: str
    roadmap_version: str
    status: SchedulerStatus
    current_item_id: str | None
    created_at: str
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    budget_limit: int
    budget_used: int
    blocking_issues: tuple[str, ...]
    required_human_actions: tuple[str, ...]
    event_sequence: int

    _TRANSITIONS = {
        SchedulerStatus.CREATED: {
            SchedulerStatus.RUNNING, SchedulerStatus.BLOCKED,
            SchedulerStatus.HUMAN_REQUIRED, SchedulerStatus.CANCELLED,
        },
        SchedulerStatus.RUNNING: {
            SchedulerStatus.PAUSED, SchedulerStatus.BLOCKED,
            SchedulerStatus.HUMAN_REQUIRED, SchedulerStatus.COMPLETED,
            SchedulerStatus.FAILED, SchedulerStatus.CANCELLED,
        },
        SchedulerStatus.PAUSED: {
            SchedulerStatus.RUNNING, SchedulerStatus.BLOCKED,
            SchedulerStatus.HUMAN_REQUIRED, SchedulerStatus.CANCELLED,
        },
        SchedulerStatus.BLOCKED: {SchedulerStatus.RUNNING, SchedulerStatus.CANCELLED},
        SchedulerStatus.HUMAN_REQUIRED: {SchedulerStatus.RUNNING, SchedulerStatus.CANCELLED},
        SchedulerStatus.COMPLETED: set(),
        SchedulerStatus.FAILED: set(),
        SchedulerStatus.CANCELLED: set(),
    }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scheduler_id": self.scheduler_id,
            "project_id": self.project_id,
            "roadmap_id": self.roadmap_id,
            "roadmap_version": self.roadmap_version,
            "status": self.status.value,
            "current_item_id": self.current_item_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "budget_limit": self.budget_limit,
            "budget_used": self.budget_used,
            "blocking_issues": list(self.blocking_issues),
            "required_human_actions": list(self.required_human_actions),
            "event_sequence": self.event_sequence,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise SchedulerValidationError("schema_version must be 1.0")
        if not _SCHEDULER_ID.fullmatch(self.scheduler_id):
            raise SchedulerValidationError("scheduler_id is invalid")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise SchedulerValidationError("project_id is invalid")
        if not _ROADMAP_ID.fullmatch(self.roadmap_id):
            raise SchedulerValidationError("roadmap_id is invalid")
        if not isinstance(self.roadmap_version, str) or not self.roadmap_version.isdigit():
            raise SchedulerValidationError("roadmap_version is invalid")
        if not isinstance(self.status, SchedulerStatus):
            raise SchedulerValidationError("status is invalid")
        if self.current_item_id is not None and not _ITEM_ID.fullmatch(self.current_item_id):
            raise SchedulerValidationError("current_item_id is invalid")
        for label, value in (("created_at", self.created_at), ("updated_at", self.updated_at)):
            if not _TIMESTAMP.fullmatch(value):
                raise SchedulerValidationError(f"{label} is invalid")
        if self.lease_owner is not None and not self._bounded_text("lease_owner", self.lease_owner):
            raise SchedulerValidationError("lease_owner is invalid")
        if self.lease_expires_at is not None and not _TIMESTAMP.fullmatch(self.lease_expires_at):
            raise SchedulerValidationError("lease_expires_at is invalid")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise SchedulerValidationError("lease owner and expiry must be paired")
        if not isinstance(self.budget_limit, int) or isinstance(self.budget_limit, bool):
            raise SchedulerValidationError("budget_limit is invalid")
        if not 0 <= self.budget_limit <= _MAX_BUDGET:
            raise SchedulerValidationError("budget_limit is invalid")
        if not isinstance(self.budget_used, int) or isinstance(self.budget_used, bool):
            raise SchedulerValidationError("budget_used is invalid")
        if not 0 <= self.budget_used <= self.budget_limit:
            raise SchedulerValidationError("budget_used is invalid")
        self._bounded_list("blocking_issues", self.blocking_issues, allow_empty=True)
        self._bounded_list("required_human_actions", self.required_human_actions, allow_empty=True)
        if not isinstance(self.event_sequence, int) or isinstance(self.event_sequence, bool):
            raise SchedulerValidationError("event_sequence is invalid")
        if self.event_sequence < 0:
            raise SchedulerValidationError("event_sequence is invalid")

    def transition(self, status: SchedulerStatus) -> "SchedulerState":
        """Return a new state only for an explicitly permitted transition."""
        self.validate()
        if status not in self._TRANSITIONS[self.status]:
            raise SchedulerValidationError(
                f"invalid scheduler transition: {self.status.value} -> {status.value}"
            )
        return replace(self, status=status, event_sequence=self.event_sequence + 1)

    @staticmethod
    def _bounded_text(label: str, value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip()) and len(value) <= _MAX_TEXT

    @classmethod
    def _bounded_list(cls, label: str, values: Any, *, allow_empty: bool) -> None:
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_ITEMS:
            raise SchedulerValidationError(f"{label} is invalid")
        if not allow_empty and not values:
            raise SchedulerValidationError(f"{label} is invalid")
        if any(not cls._bounded_text(label, value) for value in values):
            raise SchedulerValidationError(f"{label} is invalid")


def scheduler_from_dict(payload: dict[str, Any]) -> SchedulerState:
    """Construct and validate an exact JSON-shaped scheduler state."""
    required = {
        "schema_version", "scheduler_id", "project_id", "roadmap_id", "roadmap_version",
        "status", "current_item_id", "created_at", "updated_at", "lease_owner",
        "lease_expires_at", "budget_limit", "budget_used", "blocking_issues",
        "required_human_actions", "event_sequence",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise SchedulerValidationError("scheduler schema is missing or contains unknown fields")
    try:
        state = SchedulerState(
            schema_version=payload["schema_version"], scheduler_id=payload["scheduler_id"],
            project_id=payload["project_id"], roadmap_id=payload["roadmap_id"],
            roadmap_version=payload["roadmap_version"], status=SchedulerStatus(payload["status"]),
            current_item_id=payload["current_item_id"], created_at=payload["created_at"],
            updated_at=payload["updated_at"], lease_owner=payload["lease_owner"],
            lease_expires_at=payload["lease_expires_at"], budget_limit=payload["budget_limit"],
            budget_used=payload["budget_used"], blocking_issues=tuple(payload["blocking_issues"]),
            required_human_actions=tuple(payload["required_human_actions"]),
            event_sequence=payload["event_sequence"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchedulerValidationError(f"invalid scheduler structure: {exc}") from exc
    state.validate()
    return state
