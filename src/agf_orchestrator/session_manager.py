"""Safe session lifecycle and explicit execution authorization checkpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .director import Director
from .locking import lock_status, project_lock, session_lock
from .models import PlanStatus
from .project_registry import ProjectRegistry, ProjectRegistryError, _git, parse_remote_url
from .session_models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Session,
    SessionEvent,
    SessionStatus,
)
from .session_store import SessionStore, SessionStoreError


class SessionManagerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SessionManager:
    """Manage persisted state; resume authorizes a stage but does not execute it."""

    def __init__(self, state_dir=None, *, registry=None, store=None, director=None):
        self.store = store or SessionStore(state_dir)
        self.registry = registry or ProjectRegistry(self.store.state_dir)
        self.director = director or Director()

    def start(self, project_name: str, goal: str) -> Session:
        with self.registry._lock("session-start-project"):
            project = self.registry._get_unlocked(project_name)
        with project_lock(self.store.state_dir, project.project_id, "session-start"):
            project = self.registry.verify(project.project_id)
            if project.status.value != "ACTIVE":
                raise SessionManagerError("project verification failed; inspect stale reason")
            dirty = bool(_git(Path(project.repository_root), "status", "--porcelain"))
            if dirty and not project.policy.allow_dirty_planning:
                raise SessionManagerError("project is dirty and policy disallows dirty planning")
            plan = self.director.create_plan(
                goal, self._repository_context(project, clean=not dirty)
            )
            session_id = (
                "session-"
                + hashlib.sha256(
                    f"{project.project_id}:{goal}:{project.current_head_sha}:{uuid4()}".encode()
                ).hexdigest()[:20]
            )
            plan_path, plan_hash = self.store.write_artifact(
                session_id,
                "plan.json",
                json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
            )
            status = (
                SessionStatus.READY
                if plan.status is PlanStatus.READY
                else SessionStatus.HUMAN_REQUIRED
            )
            session = Session(
                session_id=session_id,
                project_id=project.project_id,
                goal=" ".join(goal.split()),
                created_at=_now(),
                updated_at=_now(),
                base_sha=project.current_head_sha,
                current_stage=status.value,
                status=status,
                plan_path=plan_path,
                blocking_issues=(
                    []
                    if status is SessionStatus.READY
                    else ["generated plan requires human clarification"]
                ),
                required_human_actions=(
                    [] if status is SessionStatus.READY else ["clarify the goal"]
                ),
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
                "session-start",
            )
            self._save(session)
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
        operation_id: str | None = None,
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
        operation_id = operation_id or f"transition:{to_status.value}"
        with session_lock(self.store.state_dir, session_id, f"transition:{to_status.value}"):
            session = self.store.load(session_id)
            with project_lock(self.store.state_dir, session.project_id, "session-transition"):
                if any(event.operation_id == operation_id for event in session.events):
                    return session
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
                    operation_id,
                )
                self._save(updated)
                return updated

    def resume(
        self,
        session_id: str,
        *,
        project_name: str | None = None,
        execute: bool = False,
        confirm_execution: bool = False,
        confirm_delivery: bool = False,
    ) -> Session:
        self._validate_resume_flags(execute, confirm_execution, confirm_delivery)
        with session_lock(self.store.state_dir, session_id, "resume"):
            session = self.store.load(session_id)
            if session.status in TERMINAL_STATUSES:
                raise SessionManagerError("terminal session cannot resume")
            project = self.registry.get(session.project_id)
            if project_name is not None and project_name not in {
                project.name,
                project.project_id,
            }:
                raise SessionManagerError("selected project does not match session project")
            with project_lock(self.store.state_dir, project.project_id, "session-resume"):
                return self._resume_locked(session, project, execute, confirm_delivery)

    @staticmethod
    def _validate_resume_flags(execute: bool, confirm_execution: bool, confirm_delivery: bool):
        if confirm_execution and not execute:
            raise SessionManagerError("--confirm-execution requires --execute")
        if confirm_delivery and not execute:
            raise SessionManagerError("--confirm-delivery requires --execute")
        if confirm_delivery and not confirm_execution:
            raise SessionManagerError("--confirm-delivery requires --confirm-execution")

    def _resume_locked(self, session, project, execute, confirm_delivery):
        if project.status.value != "ACTIVE":
            return self._mark_stale(
                session,
                f"project status is {project.status.value}",
                "restore project policy and identity through an explicit human action",
            )
        root = Path(project.repository_root)
        try:
            origin = parse_remote_url(_git(root, "config", "--get", "remote.origin.url")).normalized
            head = _git(root, "rev-parse", "HEAD")
            branch = _git(root, "branch", "--show-current")
        except ProjectRegistryError as exc:
            return self._mark_stale(session, str(exc), "repair repository identity")
        if origin != project.origin_url or branch != project.default_branch:
            return self._mark_stale(
                session,
                "repository origin or branch changed",
                "restore the registered repository identity",
            )
        if head != session.base_sha:
            return self._mark_stale(
                session,
                f"base SHA drifted: expected {session.base_sha}, found {head}",
                "review the drift and start a new session if appropriate",
            )
        try:
            self._validate_plan_identity(session, project)
        except SessionManagerError as exc:
            return self._mark_stale(session, str(exc), "inspect or restore session evidence")
        if session.status is SessionStatus.READY and execute:
            if not project.policy.allow_live_execution:
                raise SessionManagerError("execution is not authorized by project policy")
            if confirm_delivery and not project.policy.allow_delivery:
                raise SessionManagerError("delivery is not authorized by project policy")
            return self._transition_locked(
                session,
                SessionStatus.EXECUTING,
                "execution authorization checkpoint; pipeline stage not executed",
                "HUMAN",
                "resume-execution:" + session.session_id,
            )
        return session

    def _validate_plan_identity(self, session: Session, project) -> None:
        if not session.plan_path:
            raise SessionManagerError("required plan artifact is missing")
        expected_dir = (self.store.artifacts_dir / session.session_id).resolve()
        path = Path(session.plan_path)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise SessionManagerError("required plan artifact is missing") from exc
        if resolved.parent != expected_dir or expected_dir != resolved.parent:
            raise SessionManagerError("plan artifact is outside its session artifact directory")
        if session.artifact_hashes.get("plan") != self.store.artifact_hash(str(resolved)):
            raise SessionManagerError("plan artifact hash differs")
        try:
            from .executor import load_plan

            plan = load_plan(str(resolved))
        except Exception as exc:
            raise SessionManagerError("plan artifact is invalid") from exc
        if plan.repository.root != project.repository_root:
            raise SessionManagerError("plan repository root does not match registered project")
        if parse_remote_url(plan.repository.origin).normalized != project.origin_url:
            raise SessionManagerError("plan origin does not match registered project")
        if plan.repository.head_sha != session.base_sha:
            raise SessionManagerError("plan base SHA does not match session base SHA")

    def _transition_locked(self, session, status, summary, actor, operation_id):
        if any(event.operation_id == operation_id for event in session.events):
            return session
        updated = self._append_event(
            session,
            session.status,
            status,
            summary,
            [],
            [],
            actor,
            operation_id,
        )
        self._save(updated)
        return updated

    def _mark_stale(self, session, reason: str, action: str) -> Session:
        operation_id = "stale:" + reason
        if any(event.operation_id == operation_id for event in session.events):
            return session
        updated = self._append_event(
            session,
            session.status,
            SessionStatus.STALE,
            reason,
            [],
            [reason],
            "SYSTEM",
            operation_id,
        )
        updated.required_human_actions = [action]
        self._save(updated)
        return updated

    def _save(self, session: Session) -> None:
        try:
            self.store.save(session)
        except (OSError, SessionStoreError) as exc:
            raise SessionManagerError(
                "HUMAN_REQUIRED: transition persistence is uncertain"
            ) from exc

    @staticmethod
    def _append_event(
        session,
        from_status,
        to_status,
        summary,
        evidence_refs,
        blocking_issues,
        actor,
        operation_id,
    ):
        timestamp = _now()
        event = SessionEvent(
            event_id="event-" + uuid4().hex,
            operation_id=operation_id,
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
        return self.transition(
            session_id,
            SessionStatus.CANCELLED,
            summary=reason,
            actor="HUMAN",
            operation_id="cancel:" + session_id,
        )

    def lock_status(self, session_id: str) -> dict:
        session = self.store.load(session_id)
        return {
            "session": lock_status(self.store.state_dir / "locks" / f"session-{session_id}.lock"),
            "project": lock_status(
                self.store.state_dir / "locks" / f"project-{session.project_id}.lock"
            ),
            "lock_order": ["session", "project"],
        }
