"""Safe session lifecycle and explicit execution authorization checkpoints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .architect_planning import (
    ArchitectPlanningError,
    ProviderArchitect,
    architect_request_hash,
    architect_response_hash,
    build_architect_request,
    provider_evidence_payload,
    validate_architect_response,
    verify_provider_evidence,
)
from .authority_context import AuthorityContextError
from .capability_selection import CapabilityCandidate, SelectionGates
from .constitution import ConstitutionAuthority, ConstitutionVerificationError
from .delivery_reconciliation import DeliveryIntentStore, DeliveryReconciliationError
from .director import Director
from .locking import lock_status, project_lock, session_lock
from .models import PlanStatus, plan_from_dict
from .policy_authority import PolicyActivationError, PolicyAuthority
from .project_registry import ProjectRegistry, ProjectRegistryError, _git, parse_remote_url
from .session_models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Session,
    SessionEvent,
    SessionStatus,
)
from .session_store import SessionStore, SessionStoreError
from .target_assessment import (
    ArchitectureDecision,
    TargetAssessment,
    assess_repository,
    derive_architecture,
)


def _canonical_plan_hash(payload: dict[str, object]) -> str:
    """Hash the plan representation used by delivery intents.

    Session artifact hashes include file formatting and are intentionally
    distinct from the canonical model hash bound into a delivery intent.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SessionManagerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _assessment_version(store: SessionStore, session: Session) -> int:
    plan_path = session.plan_path
    versions = []
    if not plan_path:
        return 2
    stem = Path(plan_path).stem
    if stem.startswith("plan-v"):
        try:
            versions.append(int(stem.removeprefix("plan-v")))
        except ValueError:
            pass
    directory = store.artifacts_dir / session.session_id
    store.ensure_safe_path(directory)
    for path in directory.glob("*-v*.json"):
        try:
            versions.append(int(path.stem.rsplit("-v", 1)[1]))
        except ValueError:
            continue
    return max(versions, default=1) + 1


def _assessment_artifact_name(name: str, version: int) -> str:
    if version == 2:
        return name
    if name == "plan-v2.json":
        return f"plan-v{version}.json"
    return f"{Path(name).stem}-v{version}.json"


def _assessment_artifact_paths(store: SessionStore, session: Session) -> dict[str, Path]:
    version = int(Path(session.plan_path).stem.removeprefix("plan-v"))
    suffix = "" if version == 2 else f"-v{version}"
    directory = store.artifacts_dir / session.session_id
    paths = {
        "assessment": directory / f"assessment{suffix}.json",
        "architecture": directory / f"architecture{suffix}.json",
        "architect_request": directory / f"architect-request{suffix}.json",
        "plan": Path(session.plan_path),
    }
    if "provider_evidence" in session.artifact_hashes:
        paths["provider_evidence"] = directory / f"provider-evidence{suffix}.json"
    if "architect_response" in session.artifact_hashes:
        paths["architect_response"] = directory / f"architect-response{suffix}.json"
    return paths


def _validate_predecessor_chain(
    first_path: str, first_hash: str, expected_dir: Path, project, safe_path
) -> None:
    raw_path = Path(first_path)
    expected_hash = first_hash
    seen: set[Path] = set()
    from .executor import load_plan

    while True:
        if raw_path.is_symlink():
            raise SessionManagerError("recovered plan predecessor must not be a symlink")
        try:
            safe_path(raw_path)
        except SessionStoreError as exc:
            raise SessionManagerError("recovered plan predecessor path is unsafe") from exc
        path = raw_path.resolve()
        if (
            path in seen
            or path.parent != expected_dir
            or not path.is_file()
        ):
            raise SessionManagerError("recovered plan predecessor is outside session lineage")
        seen.add(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise SessionManagerError("recovered plan predecessor hash differs")
        try:
            load_plan(str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            repository = payload["repository"]
            if (
                repository["root"] != project.repository_root
                or parse_remote_url(repository["origin"]).identity
                != parse_remote_url(project.origin_url).identity
                or repository["head_sha"] != project.current_head_sha
            ):
                raise SessionManagerError("recovered plan predecessor binding differs")
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionManagerError("recovered plan predecessor is invalid") from exc
        scope = payload.get("scope", {})
        next_path = scope.get("lineage")
        if not next_path:
            return
        next_hash = scope.get("predecessor_plan_sha256")
        if not isinstance(next_hash, str) or not next_hash:
            raise SessionManagerError("recovered plan predecessor hash is missing")
        raw_path = Path(next_path)
        expected_hash = next_hash


class SessionManager:
    """Manage persisted state; resume authorizes a stage but does not execute it."""

    def __init__(
        self,
        state_dir=None,
        *,
        registry=None,
        store=None,
        director=None,
        architect=None,
        architect_candidates: tuple[CapabilityCandidate, ...] = (),
        architect_providers: dict[str, object] | None = None,
        architect_gates: SelectionGates | None = None,
    ):
        self.store = store or SessionStore(state_dir)
        self.registry = registry or ProjectRegistry(self.store.state_dir)
        self.director = director or Director()
        self.architect = architect
        self.architect_candidates = architect_candidates
        self.architect_providers = architect_providers or {}
        self.architect_gates = architect_gates

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

    def assess(self, session_id: str) -> Session:
        """Advance a placeholder plan through persisted assessment/architecture.

        This is an explicit planning operation.  It never invokes an
        implementer and never mutates the target repository.
        """
        with session_lock(self.store.state_dir, session_id, "assessment"):
            session = self.store.load(session_id)
            if session.status in TERMINAL_STATUSES:
                raise SessionManagerError("terminal session cannot be assessed")
            fresh_retry = session.status is SessionStatus.RETRY_REQUIRED
            project = self.registry.get(session.project_id)
            artifacts_ready = {
                "assessment", "architecture", "architect_request", "plan", "original_plan"
            }.issubset(session.artifact_hashes)
            if (
                artifacts_ready
                and session.status is not SessionStatus.RETRY_REQUIRED
                and session.plan_path
                and Path(session.plan_path).name.startswith("plan-v")
            ):
                root = Path(project.repository_root)
                actual_head = _git(root, "rev-parse", "HEAD")
                actual_branch = _git(root, "branch", "--show-current")
                actual_origin = parse_remote_url(
                    _git(root, "config", "--get", "remote.origin.url")
                ).identity
                if (
                    actual_head != session.base_sha
                    or actual_branch != project.default_branch
                    or actual_origin != parse_remote_url(project.origin_url).identity
                ):
                    return self._mark_stale(
                        session,
                        "repository identity or baseline changed during assessment recovery",
                        "restore the registered repository identity and baseline",
                    )
                artifact_paths = _assessment_artifact_paths(self.store, session)
                try:
                    for path in artifact_paths.values():
                        self.store.ensure_safe_path(path)
                        if path.is_symlink():
                            raise SessionManagerError("persisted assessment path is a symlink")
                    plan_payload = json.loads(artifact_paths["plan"].read_text(encoding="utf-8"))
                    assessment_payload = json.loads(
                        artifact_paths["assessment"].read_text(encoding="utf-8")
                    )
                    request_payload = json.loads(
                        artifact_paths["architect_request"].read_text(encoding="utf-8")
                    )
                    legacy_lineage = (
                        "predecessor_plan_sha256" not in plan_payload.get("scope", {})
                        or "repository_origin" not in assessment_payload
                        or "predecessor_plan" not in session.artifact_hashes
                        or "architect_request" not in session.artifact_hashes
                        or "provider_evidence" not in session.artifact_hashes
                        or "request_hash" not in request_payload
                        or "architecture_hash" not in plan_payload.get("scope", {})
                    )
                    for key, path in artifact_paths.items():
                        if self.store.artifact_hash(str(path)) != session.artifact_hashes[key]:
                            raise SessionManagerError(f"persisted {key} artifact hash differs")
                    architecture_payload = json.loads(
                        artifact_paths["architecture"].read_text(encoding="utf-8")
                    )
                    provider_evidence = None
                    if "provider_evidence" in artifact_paths:
                        provider_evidence = json.loads(
                            artifact_paths["provider_evidence"].read_text(encoding="utf-8")
                        )
                    if not legacy_lineage or fresh_retry:
                        recovered_assessment = TargetAssessment(**assessment_payload)
                        if recovered_assessment.project_id != project.project_id:
                            raise SessionManagerError(
                                "recovered assessment project binding differs"
                            )
                        if request_payload.get("request_hash") != architect_request_hash(
                            request_payload
                        ):
                            raise SessionManagerError("architect request hash differs")
                        if (
                            request_payload.get("assessment", {}).get("evidence_hash")
                            != recovered_assessment.evidence_hash
                        ):
                            raise SessionManagerError(
                                "architect request assessment binding differs"
                            )
                        if (
                            request_payload.get("repository", {}).get("root")
                            != recovered_assessment.repository_root
                            or request_payload.get("repository", {}).get("origin")
                            != recovered_assessment.repository_origin
                            or request_payload.get("repository", {}).get("head_sha")
                            != recovered_assessment.baseline_sha
                        ):
                            raise SessionManagerError(
                                "architect request repository binding differs"
                            )
                        recovered_clean = not bool(
                            _git(Path(project.repository_root), "status", "--porcelain")
                        )
                        recovered_assessment.validate(
                            self._repository_context(
                                project, clean=recovered_clean
                            )
                        )
                        recovered_architecture = ArchitectureDecision(**architecture_payload)
                        recovered_architecture.validate(recovered_assessment)
                        provider_state = architecture_payload.get("provider_selection")
                        if not isinstance(provider_state, dict):
                            raise SessionManagerError(
                                "persisted provider selection evidence is invalid"
                            )
                        if provider_state.get("architect_request_hash") != request_payload.get(
                            "request_hash"
                        ):
                            raise SessionManagerError("provider selection request binding differs")
                        if fresh_retry:
                            evidence_inputs = (
                                provider_evidence.get("inputs")
                                if isinstance(provider_evidence, dict)
                                else None
                            )
                            required_inputs = {
                                "project_id": project.project_id,
                                "session_id": session.session_id,
                                "request_hash": request_payload.get("request_hash"),
                                "target_sha": recovered_assessment.baseline_sha,
                                "plan_hash": session.artifact_hashes.get("predecessor_plan"),
                            }
                            if (
                                not isinstance(provider_evidence, dict)
                                or provider_evidence.get("schema_version") != "1.0"
                                or provider_evidence.get("source") != "adapter"
                                or provider_evidence.get("evidence_kind") != "observation"
                                or provider_evidence.get("attestation") != "unavailable"
                                or not isinstance(provider_evidence.get("attempts"), list)
                                or not isinstance(provider_evidence.get("selection_audit"), list)
                                or not isinstance(evidence_inputs, dict)
                                or any(
                                    evidence_inputs.get(key) != value
                                    for key, value in required_inputs.items()
                                )
                                or evidence_inputs.get("plan_path")
                                != plan_payload.get("scope", {}).get("lineage")
                            ):
                                raise SessionManagerError(
                                    "persisted provider evidence binding is invalid"
                                )
                        authoritative_candidates = None
                        authoritative_gates = None
                        if isinstance(self.architect, ProviderArchitect):
                            authoritative_candidates = self.architect.candidates
                            authoritative_gates = self.architect.gates
                        if provider_evidence is not None and not fresh_retry and not isinstance(
                            self.architect, ProviderArchitect
                        ):
                            return self._mark_retry_required(
                                session,
                                "current provider authority is unavailable; fresh retry required",
                                str(artifact_paths["provider_evidence"]),
                            )
                        if provider_evidence is None and not fresh_retry:
                            raise SessionManagerError("provider evidence artifact is missing")
                        if authoritative_gates is not None and not fresh_retry:
                            current_gate_results = {
                                name: getattr(authoritative_gates, name)
                                for name in (
                                    "allow_fallback",
                                    "budget_eligible",
                                    "empirical_evidence_eligible",
                                    "health_eligible",
                                    "independence_eligible",
                                    "policy_eligible",
                                    "privacy_eligible",
                                )
                            }
                            current_profile_evidence = [
                                {
                                    "provider_id": candidate.profile.provider_id,
                                    "profile_id": candidate.profile.profile_id,
                                    "profile": candidate.profile.to_dict(),
                                    "priority": candidate.priority,
                                    "diagnostic_only": candidate.diagnostic_only,
                                }
                                for candidate in authoritative_candidates or ()
                            ]
                            if (
                                provider_state.get("gate_results") != current_gate_results
                                or provider_state.get("profile_evidence")
                                != current_profile_evidence
                            ):
                                return self._mark_retry_required(
                                    session,
                                    "persisted provider evidence is stale; fresh retry required",
                                    str(artifact_paths["provider_evidence"]),
                                )
                        if not fresh_retry:
                            verify_provider_evidence(
                                provider_evidence,
                                provider_state,
                                request=build_architect_request(
                                    session.goal,
                                    self._repository_context(project, clean=recovered_clean),
                                    recovered_assessment,
                                    registered_project=project,
                                ),
                                session_id=session.session_id,
                                plan_path=plan_payload["scope"].get("lineage"),
                                plan_hash=session.artifact_hashes["predecessor_plan"],
                                target_sha=recovered_assessment.baseline_sha,
                                now=_now(),
                                authoritative_candidates=authoritative_candidates or (),
                                authoritative_gates=authoritative_gates,
                            )
                        response_hash = provider_state.get("response_hash")
                        if provider_state.get("status") == "SELECTED" and not response_hash:
                            raise SessionManagerError(
                                "selected architect response evidence is missing"
                            )
                        if response_hash and not fresh_retry:
                            response_path = artifact_paths.get("architect_response")
                            if response_path is None:
                                raise SessionManagerError("architect response artifact is missing")
                            if "architect_response" not in session.artifact_hashes:
                                raise SessionManagerError("architect response artifact is missing")
                            if self.store.artifact_hash(str(response_path)) != (
                                session.artifact_hashes["architect_response"]
                            ):
                                raise SessionManagerError(
                                    "persisted architect response artifact hash differs"
                                )
                            response_payload = json.loads(response_path.read_text(encoding="utf-8"))
                            if architect_response_hash(response_payload) != response_hash:
                                raise SessionManagerError("architect response hash differs")
                            validated_response = validate_architect_response(
                                response_payload,
                                build_architect_request(
                                    session.goal,
                                    self._repository_context(project, clean=recovered_clean),
                                    recovered_assessment,
                                    registered_project=project,
                                ),
                            )
                            if (
                                provider_state.get("planning_outcome") == "NO_JUSTIFIED_WORK"
                                and validated_response is not None
                            ) or (
                                provider_state.get("planning_outcome") != "NO_JUSTIFIED_WORK"
                                and validated_response is None
                            ):
                                raise SessionManagerError("architect response outcome differs")
                        if (
                            plan_payload["scope"].get("assessment_hash")
                            != recovered_assessment.evidence_hash
                        ):
                            raise SessionManagerError("recovered plan assessment binding differs")
                        if plan_payload["scope"].get("in") != [
                            recovered_architecture.bounded_objective
                        ]:
                            raise SessionManagerError("recovered plan objective binding differs")
                        expected_architecture_hash = hashlib.sha256(
                            json.dumps(
                                recovered_architecture.to_dict(),
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest()
                        if (
                            plan_payload["scope"].get("architecture_hash")
                            != expected_architecture_hash
                        ):
                            raise SessionManagerError("recovered plan architecture binding differs")
                        raw_plan_tasks = plan_payload.get("tasks", [])
                        if len({item.get("task_id") for item in raw_plan_tasks}) != len(
                            raw_plan_tasks
                        ):
                            raise SessionManagerError("recovered plan contains duplicate task IDs")
                        plan_tasks = {
                            item["task_id"]: item
                            for item in raw_plan_tasks
                        }
                        architecture_task_ids = {
                            item["task_id"] for item in recovered_architecture.tasks
                        }
                        if set(plan_tasks) != architecture_task_ids:
                            raise SessionManagerError("recovered plan task set differs")
                        if (
                            plan_payload["scope"].get("delivery_branch")
                            != recovered_architecture.delivery_branch
                            or plan_payload["scope"].get("out")
                            != list(recovered_architecture.prohibited_paths)
                        ):
                            raise SessionManagerError("recovered plan scope differs")
                        for architecture_task in recovered_architecture.tasks:
                            planned_task = plan_tasks.get(architecture_task["task_id"])
                            if planned_task is None or any(
                                planned_task.get(field) != architecture_task.get(field)
                                for field in (
                                    "allowed_paths", "dependencies", "acceptance_criteria",
                                    "validation_commands", "risk_level", "objective",
                                )
                            ):
                                raise SessionManagerError("recovered plan task scope differs")
                        if (
                            plan_payload["scope"].get("predecessor_plan_sha256")
                            != session.artifact_hashes["predecessor_plan"]
                        ):
                            raise SessionManagerError("recovered plan lineage differs")
                        predecessor_path = plan_payload["scope"].get("lineage")
                        if not predecessor_path:
                            raise SessionManagerError("recovered plan predecessor is missing")
                        _validate_predecessor_chain(
                            predecessor_path,
                            session.artifact_hashes["predecessor_plan"],
                            (self.store.artifacts_dir / session.session_id).resolve(),
                            project,
                            self.store.ensure_safe_path,
                        )
                    self._validate_plan_identity(session, project)
                except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
                    raise SessionManagerError("persisted assessment evidence is invalid") from exc
                if not fresh_retry and "provider_evidence" in session.artifact_hashes:
                    return self._mark_retry_required(
                        session,
                        "provider evidence is stale; historical provider observation "
                        "cannot authorize recovery; fresh retry required",
                        str(artifact_paths["provider_evidence"]),
                    )
                if not legacy_lineage and not fresh_retry:
                    return session
                self.store.write_artifact(
                    session.session_id,
                    "assessment-legacy.json",
                    artifact_paths["assessment"].read_text(encoding="utf-8"),
                )
            with project_lock(self.store.state_dir, project.project_id, "session-assessment"):
                if project.status.value != "ACTIVE":
                    return self._mark_stale(
                        session, "project is not active", "restore project identity"
                    )
                root = Path(project.repository_root)
                actual_head = _git(root, "rev-parse", "HEAD")
                actual_branch = _git(root, "branch", "--show-current")
                if actual_branch != project.default_branch:
                    return self._mark_stale(
                        session,
                        "repository branch changed during assessment",
                        "restore the registered default branch",
                    )
                actual_origin = parse_remote_url(
                    _git(root, "config", "--get", "remote.origin.url")
                ).identity
                if actual_origin != parse_remote_url(project.origin_url).identity:
                    return self._mark_stale(
                        session,
                        "repository origin changed during assessment",
                        "restore the registered repository identity",
                    )
                repository = self._repository_context(
                    project, clean=not bool(_git(root, "status", "--porcelain"))
                )
                repository = replace(repository, head_sha=actual_head)
                if actual_head != session.base_sha:
                    return self._mark_stale(
                        session,
                        "base SHA drifted: "
                        f"expected {session.base_sha}, found {repository.head_sha}",
                        "review the drift and start a new session if appropriate",
                    )
                assessment = assess_repository(
                    repository, project.project_id, registered_project=project
                )
                request = build_architect_request(
                    session.goal, repository, assessment, registered_project=project
                )
                architect = self.architect or ProviderArchitect(
                    self.architect_candidates,
                    self.architect_providers,
                    now=_now(),
                    project_id=project.project_id,
                    gates=self.architect_gates,
                )
                try:
                    if isinstance(architect, ProviderArchitect):
                        proposal = architect.propose(request)
                    else:
                        proposal = architect.propose(session.goal, assessment)
                except ArchitectPlanningError as exc:
                    proposal = None
                    provider_selection = {
                        **getattr(architect, "provider_selection", {}),
                        "status": "BLOCKED",
                        "reason": str(exc),
                        "architect_request_hash": request.request_hash,
                    }
                    provider_selection["considered"] = [
                        candidate.profile.provider_id
                        for candidate in sorted(
                            getattr(architect, "candidates", ()),
                            key=lambda item: (
                                item.priority,
                                item.profile.provider_id,
                                item.profile.profile_id,
                            ),
                        )
                    ]
                    provider_selection["rejected_reasons"] = list(
                        [str(exc)]
                    )
                    provider_selection["reason"] = str(exc)
                    if getattr(architect, "planning_outcome", None) == "NO_JUSTIFIED_WORK":
                        provider_selection["planning_outcome"] = "NO_JUSTIFIED_WORK"
                else:
                    provider_selection = getattr(architect, "provider_selection", None)
                if provider_selection is not None:
                    provider_selection = {
                        **provider_selection,
                        "architect_request_hash": request.request_hash,
                    }
                old_plan_path = session.plan_path
                assessment_version = _assessment_version(self.store, session)
                predecessor_hash = (
                    self.store.artifact_hash(old_plan_path) if old_plan_path else None
                )
                evidence_path = None
                evidence_hash = None
                if isinstance(architect, ProviderArchitect):
                    evidence = provider_evidence_payload(
                        architect,
                        request,
                        session_id=session.session_id,
                        plan_path=old_plan_path,
                        plan_hash=predecessor_hash or "",
                        target_sha=repository.head_sha,
                        selection=provider_selection,
                    )
                    evidence_path, evidence_hash = self.store.write_artifact(
                        session.session_id,
                        _assessment_artifact_name("provider-evidence.json", assessment_version),
                        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                    )
                    provider_selection = {
                        **(provider_selection or {}),
                        "provider_evidence_hash": evidence_hash,
                    }
                architecture = derive_architecture(
                    session.goal,
                    repository,
                    assessment,
                    proposal=proposal,
                    provider_selection=provider_selection,
                )
                request_path, request_hash = self.store.write_artifact(
                    session.session_id,
                    _assessment_artifact_name("architect-request.json", assessment_version),
                    json.dumps(request.to_dict(), indent=2, sort_keys=True) + "\n",
                )
                response_path = None
                response_hash = None
                if (
                    isinstance(architect, ProviderArchitect)
                    and architect.last_response is not None
                ):
                    response = architect.last_response
                    response_text = (
                        response
                        if isinstance(response, str)
                        else json.dumps(response, indent=2, sort_keys=True)
                    )
                    response_path, response_hash = self.store.write_artifact(
                        session.session_id,
                        _assessment_artifact_name("architect-response.json", assessment_version),
                        response_text + "\n",
                    )
                assessment_path, assessment_hash = self.store.write_artifact(
                    session.session_id,
                    _assessment_artifact_name("assessment.json", assessment_version),
                    json.dumps(assessment.to_dict(), indent=2, sort_keys=True) + "\n",
                )
                architecture_path, architecture_hash = self.store.write_artifact(
                    session.session_id,
                    _assessment_artifact_name("architecture.json", assessment_version),
                    json.dumps(architecture.to_dict(), indent=2, sort_keys=True) + "\n",
                )
                original_plan_hash = session.artifact_hashes.get("original_plan", "")
                plan = self.director.create_assessed_plan(
                    session.goal,
                    repository,
                    assessment,
                    architecture,
                    lineage=old_plan_path,
                    lineage_hash=predecessor_hash,
                )
                plan_path, plan_hash = self.store.write_artifact(
                    session.session_id,
                    _assessment_artifact_name("plan-v2.json", assessment_version),
                    json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
                )
                session.plan_path = plan_path
                session.artifact_hashes = {
                    key: value for key, value in session.artifact_hashes.items()
                    if key not in {"provider_evidence", "architect_response"}
                }
                session.artifact_hashes.update({
                    "original_plan": original_plan_hash or predecessor_hash or "",
                    "predecessor_plan": predecessor_hash or "",
                    "plan": plan_hash,
                    "assessment": assessment_hash,
                    "architecture": architecture_hash,
                    "architect_request": request_hash,
                })
                if evidence_hash is not None:
                    session.artifact_hashes["provider_evidence"] = evidence_hash
                if response_hash is not None:
                    session.artifact_hashes["architect_response"] = response_hash
                target_status = (
                    SessionStatus.READY
                    if plan.status is PlanStatus.READY
                    else SessionStatus.BLOCKED
                )
                summary = (
                    "assessment and architecture approved; executable scope persisted"
                    if target_status is SessionStatus.READY
                    else "assessment completed; no bounded executable scope was justified"
                )
                updated = self._append_event(
                    session,
                    session.status,
                    target_status,
                    summary,
                    [assessment_path, request_path, architecture_path, plan_path]
                    + ([evidence_path] if evidence_path else [])
                    + ([response_path] if response_path else []),
                    [] if target_status is SessionStatus.READY else [architecture.rationale],
                    "DIRECTOR",
                    "assessment:" + session.session_id,
                )
                self._save(updated)
                return updated

    def repair_lineage(self, session_id: str) -> Session:
        """Repair one proven self-referential plan without creating a session."""
        with session_lock(self.store.state_dir, session_id, "lineage-repair"):
            session = self.store.load(session_id)
            project = self.registry.verify(session.project_id)
            with project_lock(self.store.state_dir, project.project_id, "lineage-repair"):
                if session.status in TERMINAL_STATUSES:
                    raise SessionManagerError("terminal session cannot be repaired")
                try:
                    ConstitutionAuthority().resolve(project.project_id)
                    PolicyAuthority().resolve(project.project_id)
                except (
                    AuthorityContextError,
                    ConstitutionVerificationError,
                    PolicyActivationError,
                ) as exc:
                    raise SessionManagerError(
                        "lineage repair authority verification failed"
                    ) from exc
                if not session.plan_path:
                    raise SessionManagerError("session has no plan artifact")
                plan_path = self.store.ensure_safe_path(session.plan_path)
                directory = self.store.ensure_safe_path(
                    self.store.artifacts_dir / session.session_id
                )
                try:
                    audit_path = self.store.ensure_safe_path(
                        directory / "lineage-repair.json"
                    )
                except SessionStoreError as exc:
                    raise SessionManagerError("lineage repair audit is unsafe") from exc
                if audit_path.is_symlink():
                    raise SessionManagerError("lineage repair audit must not be a symlink")
                audit_payload = None
                if audit_path.is_file():
                    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
                scope = payload.get("scope", {})
                raw_lineage = scope.get("lineage", "")
                raw_lineage_path = Path(raw_lineage)
                try:
                    self.store.ensure_safe_path(raw_lineage_path)
                except SessionStoreError as exc:
                    raise SessionManagerError("plan lineage reference is unsafe") from exc
                if raw_lineage_path.is_symlink():
                    raise SessionManagerError("plan lineage reference must not be a symlink")
                is_self_reference = (
                    raw_lineage_path.resolve() == plan_path.resolve()
                )
                current_hash = self.store.artifact_hash(str(plan_path))
                repository = payload["repository"]
                if (
                    repository["root"] != project.repository_root
                    or parse_remote_url(repository["origin"]).identity
                    != parse_remote_url(project.origin_url).identity
                    or repository["branch"] != project.default_branch
                    or repository["head_sha"] != session.base_sha
                ):
                    raise SessionManagerError("current plan binding differs")
                current_plan = plan_from_dict(payload)
                current_plan.validate()
                if audit_payload is not None:
                    operation_id = "lineage-repair:" + session.session_id
                    if (
                        audit_payload.get("schema_version") != "1.0"
                        or audit_payload.get("operation_id") != operation_id
                        or audit_payload.get("session_id") != session.session_id
                        or audit_payload.get("old_plan_path") != str(plan_path)
                        or audit_payload.get("plan_id") != current_plan.plan_id
                        or audit_payload.get("plan_version") != "v2"
                    ):
                        raise SessionManagerError("lineage repair audit identity is invalid")
                    predecessor = Path(audit_payload["corrected_predecessor_reference"])
                    predecessor_hash = audit_payload["corrected_predecessor_hash"]
                    if predecessor.is_symlink():
                        raise SessionManagerError("repaired predecessor must not be a symlink")
                    _validate_predecessor_chain(
                        str(predecessor), predecessor_hash, directory.resolve(), project,
                        self.store.ensure_safe_path,
                    )
                    backup_path = self.store.ensure_safe_path(
                        directory / "plan-v2-invalid-lineage.json"
                    )
                    if backup_path.is_symlink() or not backup_path.is_file():
                        raise SessionManagerError("lineage repair backup is missing or unsafe")
                    backup_hash = self.store.artifact_hash(str(backup_path))
                    if backup_hash != audit_payload.get("old_plan_hash"):
                        raise SessionManagerError("lineage repair backup hash differs")
                    persisted_plan_hash = session.artifact_hashes.get("plan")
                    if current_hash == audit_payload.get("old_plan_hash"):
                        if persisted_plan_hash != audit_payload.get("old_plan_hash"):
                            raise SessionManagerError(
                                "lineage repair old plan hash is not persisted"
                            )
                    elif current_hash == audit_payload.get("corrected_plan_hash"):
                        if persisted_plan_hash not in {
                            audit_payload.get("old_plan_hash"),
                            audit_payload.get("corrected_plan_hash"),
                        }:
                            raise SessionManagerError("lineage repair session hash is inconsistent")
                    persisted_audit_hash = session.artifact_hashes.get("lineage_repair_audit")
                    if persisted_audit_hash and persisted_audit_hash != self.store.artifact_hash(
                        str(audit_path)
                    ):
                        raise SessionManagerError("lineage repair audit hash differs")
                    if audit_payload.get("old_predecessor_reference") != str(plan_path):
                        raise SessionManagerError("lineage repair old reference differs")
                    if predecessor_hash != session.artifact_hashes.get("original_plan"):
                        raise SessionManagerError("lineage repair predecessor is not original")
                    audit_repaired = current_hash == audit_payload.get("corrected_plan_hash")
                    if audit_repaired:
                        if (
                            scope.get("lineage")
                            != audit_payload["corrected_predecessor_reference"]
                            or scope.get("predecessor_plan_sha256") != predecessor_hash
                        ):
                            raise SessionManagerError("repaired plan lineage differs from audit")
                        updated_hashes = dict(session.artifact_hashes)
                        updated_hashes.update({
                            "plan": current_hash,
                            "predecessor_plan": predecessor_hash,
                            "lineage_repair_audit": self.store.artifact_hash(
                                str(audit_path)
                            ),
                            "lineage_repair_backup": backup_hash,
                        })
                        updated = replace(
                            session, artifact_hashes=updated_hashes,
                            blocking_issues=[], required_human_actions=[],
                        )
                        if not any(event.operation_id == operation_id for event in session.events):
                            updated = self._append_event(
                                updated, session.status, SessionStatus.BLOCKED,
                                "completed previously interrupted proven plan lineage repair",
                                [str(audit_path), str(predecessor), str(plan_path)], [],
                                "SYSTEM", operation_id,
                            )
                        self._save(updated)
                        return updated
                    elif current_hash != audit_payload.get("old_plan_hash"):
                        raise SessionManagerError("lineage repair current plan hash is unknown")
                    elif not is_self_reference:
                        raise SessionManagerError("lineage repair old plan is not self-referential")
                if not is_self_reference:
                    raise SessionManagerError("plan is not the proven self-reference case")
                if current_hash != session.artifact_hashes.get("plan") and audit_payload is None:
                    raise SessionManagerError("current plan hash is not persisted")
                original_hash = session.artifact_hashes.get("original_plan")
                if not original_hash:
                    raise SessionManagerError("immutable original plan hash is missing")
                candidates = []
                for candidate in sorted(directory.glob("*.json")):
                    if candidate.resolve() == plan_path.resolve():
                        continue
                    if self.store.artifact_hash(str(candidate)) != original_hash:
                        continue
                    _validate_predecessor_chain(
                        str(candidate), original_hash, directory.resolve(), project,
                        self.store.ensure_safe_path,
                    )
                    candidates.append(candidate)
                if len(candidates) != 1:
                    raise SessionManagerError("intended predecessor is not uniquely proven")
                predecessor = candidates[0]
                corrected = dict(payload)
                corrected_scope = dict(scope)
                corrected_scope["lineage"] = str(predecessor)
                corrected_scope["predecessor_plan_sha256"] = original_hash
                corrected["scope"] = corrected_scope
                repaired_plan = plan_from_dict(corrected)
                repaired_plan.validate()
                content = json.dumps(corrected, indent=2, sort_keys=True) + "\n"
                repaired_hash = hashlib.sha256(content.encode()).hexdigest()
                operation_id = "lineage-repair:" + session.session_id
                audit_name = "lineage-repair.json"
                backup_name = "plan-v2-invalid-lineage.json"
                audit = {
                    "schema_version": "1.0",
                    "operation_id": operation_id,
                    "session_id": session.session_id,
                    "plan_id": repaired_plan.plan_id,
                    "plan_version": "v2",
                    "timestamp": _now(),
                    "tool": "agf-orchestrator-lineage-repair-v1",
                    "reason": "plan-v2 self-referential predecessor",
                    "old_plan_path": str(plan_path),
                    "old_plan_hash": current_hash,
                    "old_predecessor_reference": scope["lineage"],
                    "old_predecessor_hash": scope.get("predecessor_plan_sha256"),
                    "corrected_predecessor_reference": str(predecessor),
                    "corrected_predecessor_hash": original_hash,
                    "corrected_plan_hash": repaired_hash,
                    "evidence": [
                        str(directory / "plan.json"),
                        str(directory / "assessment.json"),
                        str(directory / "architecture.json"),
                        "session.artifact_hashes.original_plan",
                    ],
                }
                audit_content = json.dumps(audit, indent=2, sort_keys=True) + "\n"
                audit_path, audit_hash = self.store.write_artifact(
                    session.session_id, audit_name, audit_content
                )
                self.store.replace_artifact_for_recovery(
                    session.session_id, plan_path.name, content, backup_name
                )
                updated_hashes = dict(session.artifact_hashes)
                updated_hashes.update({
                    "plan": repaired_hash,
                    "predecessor_plan": original_hash,
                    "lineage_repair_audit": audit_hash,
                    "lineage_repair_backup": self.store.artifact_hash(
                        str(directory / backup_name)
                    ),
                })
                updated = replace(
                    session,
                    artifact_hashes=updated_hashes,
                    blocking_issues=[],
                    required_human_actions=[],
                )
                updated = self._append_event(
                    updated,
                    session.status,
                    SessionStatus.BLOCKED,
                    "repaired proven plan lineage; pilot remains blocked pending assessment",
                    [audit_path, str(predecessor), str(plan_path)],
                    [],
                    "SYSTEM",
                    operation_id,
                )
                self._save(updated)
                return updated

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
            origin = parse_remote_url(_git(root, "config", "--get", "remote.origin.url")).identity
            head = _git(root, "rev-parse", "HEAD")
            branch = _git(root, "branch", "--show-current")
        except ProjectRegistryError as exc:
            return self._mark_stale(session, str(exc), "repair repository identity")
        if (
            origin != parse_remote_url(project.origin_url).identity
            or branch != project.default_branch
        ):
            return self._mark_stale(
                session,
                "repository origin or branch changed",
                "restore the registered repository identity",
            )
        if head != session.base_sha:
            reconciled = self._reconcile_verified_delivery(session, project, root, head)
            if reconciled is not None:
                return reconciled
            return self._mark_stale(
                session,
                f"base SHA drifted: expected {session.base_sha}, found {head}",
                "review the drift and start a new session if appropriate",
            )
        try:
            self._validate_plan_identity(session, project)
            self._validate_plan_lineage(session, project)
        except SessionManagerError as exc:
            return self._mark_stale(session, str(exc), "inspect or restore session evidence")
        if session.plan_path:
            try:
                recovered_scope = json.loads(
                    Path(session.plan_path).read_text(encoding="utf-8")
                ).get("scope", {})
            except (OSError, json.JSONDecodeError, TypeError):
                return self._mark_stale(
                    session, "reconciled plan is unreadable", "restore session evidence"
                )
            if recovered_scope.get("delivery_reconciliation", {}).get("completed_task_id"):
                return session
        if session.status is SessionStatus.READY and "provider_evidence" in session.artifact_hashes:
            evidence_path = _assessment_artifact_paths(self.store, session)["provider_evidence"]
            return self._mark_retry_required(
                session,
                "historical provider observation cannot authorize recovery; "
                "fresh retry required",
                str(evidence_path),
            )
        if session.status is SessionStatus.READY and execute:
            if not project.policy.allow_live_execution:
                raise SessionManagerError("execution is not authorized by project policy")
            if confirm_delivery and not project.policy.allow_delivery:
                raise SessionManagerError("delivery is not authorized by project policy")
            try:
                ConstitutionAuthority().resolve(project.project_id)
            except (AuthorityContextError, ConstitutionVerificationError) as exc:
                raise SessionManagerError(str(exc)) from exc
            return self._transition_locked(
                session,
                SessionStatus.EXECUTING,
                "execution authorization checkpoint; pipeline stage not executed",
                "HUMAN",
                "resume-execution:" + session.session_id,
            )
        return session

    def _reconcile_verified_delivery(self, session, project, root: Path, observed_head: str):
        """Advance recovery only when a persisted intent proves the exact target state."""
        try:
            store = DeliveryIntentStore(self.store.state_dir)
            intents = [
                item for item in store.for_session(project.project_id, session.session_id)
                if item.base_sha == session.base_sha
            ]
            if len(intents) != 1:
                return None
            intent = intents[0]
            receipt = store.observe(project.project_id, intent.delivery_id, root)
            if receipt.observed_sha != observed_head:
                return None
            old_plan = Path(session.plan_path or "")
            old_hash = session.artifact_hashes.get("plan")
            if not old_hash or not old_plan.is_file():
                return None
            payload = json.loads(old_plan.read_text(encoding="utf-8"))
            if intent.plan_id != payload.get("plan_id"):
                return None
            if intent.plan_hash != _canonical_plan_hash(payload):
                return None
            task_payloads = [
                item for item in payload.get("tasks", [])
                if item.get("task_id") == intent.task_id
            ]
            if len(task_payloads) != 1:
                return None
            task_hash = hashlib.sha256(
                json.dumps(task_payloads[0], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if intent.task_hash != task_hash:
                return None
            payload["repository"]["head_sha"] = observed_head
            payload["scope"] = {
                **payload.get("scope", {}),
                "lineage": str(old_plan),
                "predecessor_plan_sha256": old_hash,
                "delivery_reconciliation": {
                    "delivery_id": intent.delivery_id,
                    "intent_hash": intent.content_sha256,
                    "receipt_hash": receipt.receipt_sha256,
                    "observed_sha": observed_head,
                    "completed_task_id": intent.task_id,
                },
            }
            reconciled = plan_from_dict(payload)
            version = _assessment_version(self.store, session)
            plan_path, plan_hash = self.store.write_artifact(
                session.session_id,
                f"plan-v{version}.json",
                json.dumps(reconciled.to_dict(), indent=2, sort_keys=True) + "\n",
            )
            session.plan_path = plan_path
            session.base_sha = observed_head
            session.delivery_branch = intent.delivery_branch
            session.delivery_report_path = str(
                store.receipt_path(project.project_id, intent.delivery_id)
            )
            session.artifact_hashes.update({
                "plan": plan_hash,
                "delivery_intent": intent.content_sha256,
                "delivery_receipt": receipt.receipt_sha256,
            })
            updated = self._append_event(
                session,
                session.status,
                SessionStatus.READY,
                "verified external delivery reconciled from persisted intent and receipt",
                [session.delivery_report_path, plan_path],
                [],
                "RELEASE_MANAGER",
                "delivery-reconcile:" + intent.delivery_id,
            )
            self._save(updated)
            return updated
        except (
            DeliveryReconciliationError,
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

    def _validate_plan_lineage(self, session: Session, project) -> None:
        """Validate every persisted predecessor before normal continuation."""
        if not session.plan_path:
            return
        plan_path = self.store.ensure_safe_path(session.plan_path)
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SessionManagerError("persisted plan lineage is invalid") from exc
        scope = payload.get("scope", {})
        lineage = scope.get("lineage")
        lineage_hash = scope.get("predecessor_plan_sha256")
        if not lineage and not lineage_hash:
            return
        expected_hash = session.artifact_hashes.get("predecessor_plan")
        if not lineage or not lineage_hash or not expected_hash or lineage_hash != expected_hash:
            raise SessionManagerError("persisted plan lineage is incomplete")
        _validate_predecessor_chain(
            lineage,
            lineage_hash,
            (self.store.artifacts_dir / session.session_id).resolve(),
            project,
            self.store.ensure_safe_path,
        )

    def _validate_plan_identity(self, session: Session, project) -> None:
        if not session.plan_path:
            raise SessionManagerError("required plan artifact is missing")
        expected_dir = (self.store.artifacts_dir / session.session_id).resolve()
        path = Path(session.plan_path)
        try:
            self.store.ensure_safe_path(path)
        except SessionStoreError as exc:
            raise SessionManagerError("plan artifact path is unsafe") from exc
        if path.is_symlink():
            raise SessionManagerError("plan artifact must not be a symlink")
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
        if parse_remote_url(plan.repository.origin).identity != parse_remote_url(
            project.origin_url
        ).identity:
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

    def _mark_retry_required(self, session, reason: str, evidence_ref: str) -> Session:
        evidence_key = session.artifact_hashes.get("provider_evidence", evidence_ref)
        operation_id = "retry-required:" + session.session_id + ":" + evidence_key
        if any(event.operation_id == operation_id for event in session.events):
            return session
        updated = self._append_event(
            session,
            session.status,
            SessionStatus.RETRY_REQUIRED,
            reason,
            [evidence_ref],
            [reason],
            "SYSTEM",
            operation_id,
        )
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
