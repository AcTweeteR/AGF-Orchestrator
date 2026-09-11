"""Bounded governed steps connecting canonical sessions and delivery.

A step never accepts a provider's completion claim as Objective acceptance.
Interrupted dispatch is reconciled from intents before any new invocation.
"""

import json
from dataclasses import asdict

from .delivery_admission import admit_live_delivery
from .delivery_reconciliation import DeliveryIntentStore
from .execution_journal import ExecutionRecoveryRequired
from .git_delivery import GitDelivery, sanitize_branch_name
from .locking import LockError, project_lock
from .models import plan_from_dict
from .objective_acceptance import (
    ObjectiveAcceptanceError,
    content_hash,
    read_objective_acceptance,
)
from .session_manager import SessionManagerError
from .session_models import SessionStatus
from .session_store import SessionStore
from .task_dependencies import (
    MissingIntegrationEvidence,
    verify_integrated_plan,
    verify_integrated_task,
    verify_task_dependencies,
)
from .task_dependencies import _hash as integration_hash


class SessionContinuation:
    def __init__(self, manager, pipeline, *, architect_factory=None):
        self.manager = manager
        self.pipeline = pipeline
        self.architect_factory = architect_factory

    def _result(self, session, status, action, reason, **extra):
        return {"schema_version": "1.0", "session_id": session.session_id,
                "status": status, "action": action, "reason": reason,
                "objective_completed": False, **extra}

    def tick(self, session_id, *, execute=False, confirm_execution=False, confirm_delivery=False):
        if not all(value is True for value in (execute, confirm_execution, confirm_delivery)):
            raise SessionManagerError("continuation requires execution and delivery confirmation")
        session = self.manager.get(session_id)
        with project_lock(self.manager.store.state_dir, f"continuation-{session.project_id}",
                          "governed-continuation"):
            try:
                return self._tick(session_id)
            except ExecutionRecoveryRequired:
                return self._result(session, "HUMAN_REQUIRED", "interrupted-dispatch",
                                    "prior execution requires canonical reconciliation")
            except LockError:
                # The campaign runner owns bounded retry/backoff. Contention is
                # not evidence corruption and must not terminalize its session.
                raise
            except (OSError, ValueError, RuntimeError):
                return self._result(session, "BLOCKED", "evidence-gate",
                                    "canonical evidence failed; inspect retained artifacts")

    def _tick(self, session_id):
        manager = self.manager
        store = manager.store
        session = manager.get(session_id)
        if session.status is SessionStatus.COMPLETED:
            return manager.complete(session_id, execute=True, confirm_execution=True)
        if session.status in {SessionStatus.CANCELLED, SessionStatus.NO_JUSTIFIED_WORK}:
            return self._result(session, "HUMAN_REQUIRED", "terminal-checkpoint",
                                "historical terminal disposition requires current assessment")
        previous = session.to_dict()
        session = manager.resume(session_id)
        if session.to_dict() != previous:
            status = ("CONTINUE" if session.status in {SessionStatus.READY,
                       SessionStatus.RETRY_REQUIRED} else "HUMAN_REQUIRED"
                       if session.required_human_actions else "BLOCKED")
            return self._result(session, status, "reconcile", "canonical session reconciled")
        if session.status not in {SessionStatus.READY, SessionStatus.RETRY_REQUIRED}:
            return self._result(session, "HUMAN_REQUIRED" if session.required_human_actions
                                else "BLOCKED", "session-gate", "session is not executable")
        project = manager.registry.get(session.project_id)
        if session.status is SessionStatus.RETRY_REQUIRED:
            return self._assess(session)
        path = store.ensure_safe_path(session.plan_path)
        if store.artifact_hash(str(path)) != session.artifact_hashes.get("plan"):
            raise SessionManagerError("continuation plan hash differs")
        plan = plan_from_dict(json.loads(path.read_text()))
        intents = DeliveryIntentStore(store.state_dir).for_session(project.project_id, session_id)
        if not intents:
            if "assessment" not in session.artifact_hashes:
                return self._assess(session)
            gate = self._objective_gate(session, project, plan)
            if gate is not None:
                return gate
        try:
            acceptance = read_objective_acceptance(project, session)
            approved_plan = acceptance.approved_plan_sha256
        except ObjectiveAcceptanceError:
            approved_plan = None
        try:
            verify_integrated_plan(session_id, plan, project.repository_root,
                                   state_dir=store.state_dir, approved_plan_sha256=approved_plan)
        except MissingIntegrationEvidence:
            pass
        else:
            return manager.complete(session_id, execute=True, confirm_execution=True)
        integrated = set()
        for task in plan.tasks:
            try:
                verify_integrated_task(session_id, plan, task.task_id, project.repository_root,
                                       state_dir=store.state_dir)
            except MissingIntegrationEvidence:
                continue
            integrated.add(task.task_id)
        pending = [item for item in intents if item.task_id not in integrated]
        if pending:
            if len(pending) != 1:
                raise SessionManagerError("ambiguous pending delivery intents")
            item = pending[0]
            task = next((task for task in plan.tasks if task.task_id == item.task_id), None)
            if (task is None or item.base_sha != session.base_sha
                    or item.plan_hash != integration_hash(plan.to_dict())
                    or item.task_hash != integration_hash(asdict(task))):
                raise SessionManagerError("pending intent differs from canonical work")
            return self._result(session, "WAIT", "external-integration",
                                "await exact delivery integration before continuing",
                                delivery_id=item.delivery_id)
        if not integrated and "assessment" not in session.artifact_hashes:
            return self._assess(session)
        gate = self._objective_gate(session, project, plan)
        if gate is not None:
            return gate
        for task in plan.tasks:
            if task.task_id in integrated:
                continue
            try:
                verify_task_dependencies(session_id, plan, task, project.repository_root)
            except MissingIntegrationEvidence:
                continue
            # Dispatch and dependency gates must use the same configured
            # business-state store. Authority still uses its existing root.
            if store.state_dir != SessionStore().state_dir:
                return self._result(session, "HUMAN_REQUIRED", "state-binding",
                                    "live delivery session differs from configured state")
            admit_live_delivery(project, plan, task.task_id)
            binding = {"project_id": project.project_id, "session_id": session_id,
                       "plan_sha256": session.artifact_hashes["plan"],
                       "task_sha256": content_hash(asdict(task)),
                       "task_id": task.task_id, "base_sha": session.base_sha}
            return self._dispatch(session, project, plan, task, binding)
        return self._result(session, "BLOCKED", "dependency-gate", "no verified executable task")

    def _objective_gate(self, session, project, plan):
        try:
            acceptance = read_objective_acceptance(project, session)
            if (acceptance.approved_plan_sha256
                    and content_hash(plan.to_dict()) != acceptance.approved_plan_sha256
                    and not DeliveryIntentStore(self.manager.store.state_dir).for_session(
                        project.project_id, session.session_id,
                    )):
                bound = self.manager.bind_objective_plan(session.session_id)
                return self._result(bound, "CONTINUE", "objective-binding",
                                    "first executable plan matches the signed owner contract")
            requirements = {item.requirement_id for item in acceptance.objective.requirements
                            if item.mandatory}
            if (plan.objective_id != acceptance.objective.objective_id
                    or not requirements <= set(plan.requirement_refs)):
                raise ObjectiveAcceptanceError("plan lacks approved Objective traceability")
        except ObjectiveAcceptanceError as exc:
            return self._result(session, "HUMAN_REQUIRED", "objective-authority", str(exc))
        return None

    def _dispatch(self, session, project, plan, task, binding):
        store = self.manager.store
        from .execution_journal import require_reconciled_execution

        failed_invocations = require_reconciled_execution(store, session, plan)
        remaining = project.policy.maximum_correction_rounds + 1 - failed_invocations
        if remaining <= 0:
            return self._result(session, "BLOCKED", "retry-budget",
                                "bounded delivery budget exhausted")
        if failed_invocations:
            branch = sanitize_branch_name(
                str(plan.scope.get("delivery_branch") or plan.plan_id), task.task_id,
            )
            GitDelivery().validate_target(project.repository_root, session.base_sha, branch)
        self.pipeline.max_correction_rounds = remaining - 1
        # DeliveryPipeline takes the shared dispatch lock and records the sole
        # runtime start before invoking a provider. Do not leave a separate
        # coordinator start that another entry point could overlook on restart.
        report = self.pipeline.deliver(plan, task.task_id, project.repository_root,
                                       execute=True, project_id=project.project_id,
                                       session_id=session.session_id)
        payload = report.to_dict()
        artifact, _ = store.write_artifact(
            session.session_id,
            f"continuation-{content_hash(binding)}-result-{content_hash(payload)}.json",
            json.dumps(payload, sort_keys=True) + "\n",
        )
        return self._result(session, "CONTINUE", "delivery", "delivery attempt recorded",
                            evidence_path=artifact)

    def _assess(self, session):
        if self.architect_factory is not None:
            self.manager.architect = self.architect_factory()
        result = self.manager.assess(session.session_id)
        if result.status is SessionStatus.NO_JUSTIFIED_WORK:
            return self._result(result, "NO_JUSTIFIED_WORK", "assessment",
                                "current governed assessment found no justified work")
        return self._result(result, "CONTINUE" if result.status is SessionStatus.READY
                            else "HUMAN_REQUIRED" if result.required_human_actions else "BLOCKED",
                            "assessment", "assessment persisted")
