"""Concise Director inbox derived from persisted sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .project_registry import ProjectRegistry
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
