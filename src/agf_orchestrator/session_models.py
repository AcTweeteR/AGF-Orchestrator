"""Session state machine and immutable event records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SessionStatus(StrEnum):
    PLANNING = "PLANNING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    CORRECTING = "CORRECTING"
    COMPLIANCE = "COMPLIANCE"
    DELIVERING = "DELIVERING"
    PR_READY = "PR_READY"
    BLOCKED = "BLOCKED"
    RETRY_REQUIRED = "RETRY_REQUIRED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    STALE = "STALE"


ACTOR_VALUES = {
    "DIRECTOR",
    "PLANNER",
    "IMPLEMENTER",
    "REVIEWER",
    "COMPLIANCE",
    "RELEASE_MANAGER",
    "HUMAN",
    "SYSTEM",
}
TERMINAL_STATUSES = {SessionStatus.COMPLETED, SessionStatus.CANCELLED}
ACTIVE_STATUSES = set(SessionStatus) - TERMINAL_STATUSES
ALLOWED_TRANSITIONS = {
    SessionStatus.PLANNING: {SessionStatus.READY},
    SessionStatus.READY: {SessionStatus.EXECUTING},
    SessionStatus.EXECUTING: {SessionStatus.REVIEWING},
    SessionStatus.REVIEWING: {SessionStatus.CORRECTING, SessionStatus.COMPLIANCE},
    SessionStatus.CORRECTING: {SessionStatus.REVIEWING},
    SessionStatus.COMPLIANCE: {SessionStatus.DELIVERING},
    SessionStatus.DELIVERING: {SessionStatus.PR_READY},
    SessionStatus.PR_READY: {SessionStatus.COMPLETED},
}
for _status in ACTIVE_STATUSES:
    ALLOWED_TRANSITIONS.setdefault(_status, set()).update(
        {
            SessionStatus.BLOCKED,
            SessionStatus.HUMAN_REQUIRED,
            SessionStatus.FAILED,
            SessionStatus.STALE,
            SessionStatus.CANCELLED,
        }
    )
ALLOWED_TRANSITIONS[SessionStatus.RETRY_REQUIRED].update(
    {SessionStatus.READY, SessionStatus.BLOCKED}
)
ALLOWED_TRANSITIONS[SessionStatus.BLOCKED].add(SessionStatus.RETRY_REQUIRED)


@dataclass(frozen=True)
class SessionEvent:
    event_id: str
    operation_id: str
    timestamp: str
    session_id: str
    event_type: str
    from_status: str | None
    to_status: str
    summary: str
    evidence_refs: list[str]
    blocking_issues: list[str]
    actor: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Session:
    session_id: str
    project_id: str
    goal: str
    created_at: str
    updated_at: str
    base_sha: str
    current_stage: str
    status: SessionStatus
    plan_path: str | None = None
    execution_report_path: str | None = None
    review_report_path: str | None = None
    compliance_report_path: str | None = None
    delivery_report_path: str | None = None
    delivery_branch: str | None = None
    pr_url: str | None = None
    blocking_issues: list[str] = field(default_factory=list)
    required_human_actions: list[str] = field(default_factory=list)
    events: list[SessionEvent] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["events"] = [event.to_dict() for event in self.events]
        return result


def session_from_dict(payload: dict[str, Any]) -> Session:
    if payload.get("schema_version") != "1.0":
        raise ValueError("HUMAN_REQUIRED: unsupported session schema")
    required = {
        "session_id",
        "project_id",
        "goal",
        "created_at",
        "updated_at",
        "base_sha",
        "current_stage",
        "status",
        "plan_path",
        "execution_report_path",
        "review_report_path",
        "compliance_report_path",
        "delivery_report_path",
        "delivery_branch",
        "pr_url",
        "blocking_issues",
        "required_human_actions",
        "events",
        "attempts",
        "artifact_hashes",
        "schema_version",
    }
    if set(payload) != required:
        raise ValueError("session schema is missing or contains unknown fields")
    events = [SessionEvent(**item) for item in payload["events"]]
    return Session(
        session_id=payload["session_id"],
        project_id=payload["project_id"],
        goal=payload["goal"],
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
        base_sha=payload["base_sha"],
        current_stage=payload["current_stage"],
        status=SessionStatus(payload["status"]),
        plan_path=payload["plan_path"],
        execution_report_path=payload["execution_report_path"],
        review_report_path=payload["review_report_path"],
        compliance_report_path=payload["compliance_report_path"],
        delivery_report_path=payload["delivery_report_path"],
        delivery_branch=payload["delivery_branch"],
        pr_url=payload["pr_url"],
        blocking_issues=payload["blocking_issues"],
        required_human_actions=payload["required_human_actions"],
        events=events,
        attempts=payload["attempts"],
        artifact_hashes=payload["artifact_hashes"],
        schema_version=payload["schema_version"],
    )
