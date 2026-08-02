"""Safe session lifecycle, resume checks, and idempotent transitions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .director import Director
from .locking import lock_status, session_lock
from .models import PlanStatus
from .project_registry import ProjectRegistry, ProjectRegistryError
from .session_models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Session,
    SessionEvent,
    SessionStatus,
)
from .session_store import SessionStore


class SessionManagerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SessionManager:
    def __init__(self, state_dir=None, *, registry=None, store=None, director=None):
        self.store = store or SessionStore(state_dir)
        self.registry = registry or ProjectRegistry(self.store.state_dir)
        self.director = director or Director()

    def start(self, project_name: str, goal: str) -> Session:
        project = self.registry.get(project_name)
        if project.status.value != "ACTIVE":
            raise SessionManagerError(f"project status is {project.status.value}")
        verified = self.registry.verify(project.project_id)
        if verified.status.value != "ACTIVE":
            raise SessionManagerError("project verification failed; inspect stale reason")
        root = Path(verified.repository_root)
        from .project_registry import _git

        dirty = bool(_git(root, "status", "--porcelain"))
        if dirty and not verified.policy.allow_dirty_planning:
            raise SessionManagerError("project is dirty and policy disallows dirty planning")
        plan = self.director.create_plan(goal, self._repository_context(verified, clean=not dirty))
        session_id = (
            "session-"
            + hashlib.sha256(
                f"{verified.project_id}:{goal}:{verified.current_head_sha}:{uuid4()}".encode()
            ).hexdigest()[:20]
        )
        plan_path, plan_hash = self.store.write_artifact(
            session_id, "plan.json", json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n"
        )
        status = (
            SessionStatus.READY if plan.status is PlanStatus.READY else SessionStatus.HUMAN_REQUIRED
        )
        session = Session(
            session_id=session_id,
            project_id=verified.project_id,
            goal=" ".join(goal.split()),
            created_at=_now(),
            updated_at=_now(),
            base_sha=verified.current_head_sha,
            current_stage=status.value,
            status=status,
            plan_path=plan_path,
            blocking_issues=(
                []
                if status is SessionStatus.READY
                else ["generated plan requires human clarification"]
            ),
            required_human_actions=[] if status is SessionStatus.READY else ["clarify the goal"],
            artifact_hashes={"plan": plan_hash},
        )
        session = self._append_event(
            session,
            SessionStatus.PLANNING,
            status,
            "session started and plan generated",
            [plan_path],
            session.blocking_issues,
            "DIRECTOR",
        )
        self.store.save(session)
        return session

    @staticmethod
    def _repository_context(project, *, clean: bool):
        from .models import RepositoryContext

        return RepositoryContext(
            project.repository_root,
            project.default_branch,
            project.origin_url,
            clean,
            project.current_head_sha,
        )

    def get(self, session_id: str) -> Session:
        return self.store.load(session_id)

    def list(self) -> list[Session]:
        return self.store.list()

    def transition(
        self,
        session_id: str,
        to_status: SessionStatus,
        *,
        summary: str = "",
        actor: str = "SYSTEM",
        evidence_refs=None,
        blocking_issues=None,
    ) -> Session:
        if actor not in {
            "DIRECTOR",
            "PLANNER",
            "IMPLEMENTER",
            "REVIEWER",
            "COMPLIANCE",
            "RELEASE_MANAGER",
            "HUMAN",
            "SYSTEM",
        }:
            raise SessionManagerError("invalid event actor")
        with session_lock(self.store.state_dir, session_id, f"transition:{to_status.value}"):
            session = self.store.load(session_id)
            if session.status is to_status:
                return session
            if to_status not in ALLOWED_TRANSITIONS.get(session.status, set()):
                raise SessionManagerError(
                    f"invalid session transition: {session.status.value} -> {to_status.value}"
                )
            updated = self._append_event(
                session,
                session.status,
                to_status,
                summary or to_status.value,
                evidence_refs or [],
                blocking_issues or [],
                actor,
            )
            self.store.save(updated)
            return updated

    def resume(
        self,
        session_id: str,
        *,
        execute: bool = False,
        confirm_execution: bool = False,
        confirm_delivery: bool = False,
    ) -> Session:
        with session_lock(self.store.state_dir, session_id, "resume"):
            session = self.store.load(session_id)
            if session.status in TERMINAL_STATUSES:
                raise SessionManagerError("terminal session cannot resume")
            project = self.registry.get(session.project_id)
            if project.status.value != "ACTIVE":
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    f"project status is {project.status.value}",
                    "restore project policy and identity through an explicit human action",
                )
            root = Path(project.repository_root)
            try:
                from .project_registry import _git

                origin = _git(root, "config", "--get", "remote.origin.url")
                head = _git(root, "rev-parse", "HEAD")
                branch = _git(root, "branch", "--show-current")
            except ProjectRegistryError as exc:
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    str(exc),
                    "repair repository identity and inspect the session",
                )
            if origin != project.origin_url or branch != project.default_branch:
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    "repository origin or branch changed",
                    "restore the registered repository identity",
                )
            if head != session.base_sha:
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    f"base SHA drifted: expected {session.base_sha}, found {head}",
                    "review the drift and start a new session if appropriate",
                )
            if session.plan_path is None or not Path(session.plan_path).exists():
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    "required plan artifact is missing",
                    "restore evidence or start a new session",
                )
            if session.artifact_hashes.get("plan") != self.store.artifact_hash(session.plan_path):
                return self._mark_locked(
                    session,
                    SessionStatus.STALE,
                    "plan artifact hash differs",
                    "inspect the artifact and session evidence",
                )
            if session.status is SessionStatus.READY and execute:
                if not (confirm_execution and project.policy.allow_live_execution):
                    raise SessionManagerError(
                        "execution requires --execute, --confirm-execution, and "
                        "project policy authorization"
                    )
                if confirm_delivery and not project.policy.allow_delivery:
                    raise SessionManagerError("delivery is not authorized by project policy")
                updated = self._append_event(
                    session,
                    session.status,
                    SessionStatus.EXECUTING,
                    "session resumed for explicitly authorized execution",
                    [],
                    [],
                    "HUMAN",
                )
                self.store.save(updated)
                return updated
            return session

    def _mark_locked(
        self, session: Session, status: SessionStatus, reason: str, action: str
    ) -> Session:
        updated = self._append_event(
            session, session.status, status, reason, [], [reason], "SYSTEM"
        )
        updated.required_human_actions = [action]
        self.store.save(updated)
        return updated

    @staticmethod
    def _append_event(
        session: Session,
        from_status: SessionStatus | None,
        to_status: SessionStatus,
        summary: str,
        evidence_refs,
        blocking_issues,
        actor: str,
    ) -> Session:
        timestamp = _now()
        event = SessionEvent(
            event_id="event-" + uuid4().hex,
            timestamp=timestamp,
            session_id=session.session_id,
            event_type=f"{from_status.value if from_status else 'NONE'}_TO_{to_status.value}",
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            summary=summary[:500],
            evidence_refs=list(evidence_refs),
            blocking_issues=list(blocking_issues),
            actor=actor,
        )
        session.events.append(event)
        session.status = to_status
        session.current_stage = to_status.value
        session.updated_at = timestamp
        session.blocking_issues = list(blocking_issues)
        return session

    def cancel(self, session_id: str, reason: str = "cancelled by human") -> Session:
        return self.transition(session_id, SessionStatus.CANCELLED, summary=reason, actor="HUMAN")

    def lock_status(self, session_id: str) -> dict:
        return lock_status(self.store.state_dir / "locks" / f"session-{session_id}.lock")
