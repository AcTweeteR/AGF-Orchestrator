"""Content-bound projection of the owner's first executable plan.

Projection adds traceability only. It cannot approve an Objective or replace
execution history. Draft ancestors remain in the canonical lineage.
"""

import json
from dataclasses import replace

from .delivery_reconciliation import DeliveryIntentStore
from .objective_acceptance import ObjectiveAcceptanceError, content_hash
from .session_models import SessionStatus


def require_unexecuted_planning(session, store):
    origin_path = store.ensure_safe_path(
        store.artifacts_dir / session.session_id / "planning-origin.json",
    )
    if (not origin_path.is_file() or store.artifact_hash(str(origin_path))
            != session.artifact_hashes.get("planning_origin")):
        raise ObjectiveAcceptanceError("historical session lacks an unambiguous planning origin")
    origin = json.loads(origin_path.read_text())
    if (origin.get("protocol") != "governed-session/1"
            or origin.get("session_id") != session.session_id
            or origin.get("project_id") != session.project_id):
        raise ObjectiveAcceptanceError("planning origin is inconsistent")
    path = store.ensure_safe_path(session.plan_path)
    expected = session.artifact_hashes["plan"]
    for _ in range(200):
        if store.artifact_hash(str(path)) != expected:
            raise ObjectiveAcceptanceError("planning predecessor hash differs")
        if expected == origin.get("initial_plan_sha256"):
            break
        payload = json.loads(path.read_text())
        previous = payload.get("scope", {}).get("lineage")
        if not previous:
            raise ObjectiveAcceptanceError("planning lineage does not reach its recorded origin")
        path = store.ensure_safe_path(previous)
        if path.parent != origin_path.parent:
            raise ObjectiveAcceptanceError("planning predecessor belongs to another session")
        expected = payload["scope"]["predecessor_plan_sha256"]
    else:
        raise ObjectiveAcceptanceError("planning lineage exceeds limit")
    if session.status is not SessionStatus.READY:
        raise ObjectiveAcceptanceError("Objective binding requires ready planning")
    if any((session.execution_report_path, session.review_report_path,
            session.compliance_report_path, session.delivery_report_path, session.pr_url)):
        raise ObjectiveAcceptanceError("existing execution cannot become draft planning")
    if DeliveryIntentStore(store.state_dir).for_session(session.project_id, session.session_id):
        raise ObjectiveAcceptanceError("existing delivery cannot become draft planning")
    execution_states = {"EXECUTING", "REVIEWING", "CORRECTING", "COMPLIANCE",
                        "DELIVERING", "PR_READY", "COMPLETED", "FAILED"}
    if any(event.from_status in execution_states or event.to_status in execution_states
           for event in session.events):
        raise ObjectiveAcceptanceError("execution history prevents first-plan binding")
    artifacts = store.ensure_safe_path(store.artifacts_dir / session.session_id)
    if any(path.name.startswith(("continuation-", "execution-started-"))
           for path in artifacts.iterdir()):
        raise ObjectiveAcceptanceError("dispatch history requires reconciliation before binding")


def project_objective_plan(plan, session, objective, criteria):
    """Produce a proposal; only a matching authenticated hash authorizes its use."""
    if plan.objective_id not in {None, objective.objective_id}:
        raise ObjectiveAcceptanceError("planning Objective identity contradicts the contract")
    task_refs = {task.task_id: set() for task in plan.tasks}
    tasks = {task.task_id: task for task in plan.tasks}
    coverage = set()
    for criterion in criteria:
        coverage.update(criterion.task_ids)
        if not set(criterion.task_ids) <= set(tasks):
            raise ObjectiveAcceptanceError("mapping references unknown planned task")
        permitted = {command for task_id in criterion.task_ids
                     for command in tasks[task_id].validation_commands}
        if not set(criterion.validation_commands) <= permitted:
            raise ObjectiveAcceptanceError("mapping changes reviewed validators")
        if criterion.criterion_id.startswith("requirement:"):
            requirement = criterion.criterion_id.split(":")[1]
            for task_id in criterion.task_ids:
                task_refs[task_id].add(requirement)
    if coverage != set(tasks):
        raise ObjectiveAcceptanceError("mapping omits planned work")
    references = {ref for refs in task_refs.values() for ref in refs}
    if ((plan.requirement_refs and set(plan.requirement_refs) != references)
            or any(task.requirement_refs and set(task.requirement_refs) != task_refs[task.task_id]
                   for task in plan.tasks)):
        raise ObjectiveAcceptanceError("projection would replace existing requirement references")
    projected = replace(
        plan, objective_id=objective.objective_id,
        requirement_refs=sorted({ref for refs in task_refs.values() for ref in refs}),
        tasks=[replace(task, requirement_refs=sorted(task_refs[task.task_id]))
               for task in plan.tasks],
        scope={**plan.scope, "lineage": session.plan_path,
               "predecessor_plan_sha256": session.artifact_hashes["plan"]},
    )
    projected.validate()
    return projected


def assert_approved_projection(plan, acceptance):
    if (acceptance.approved_plan_sha256 is None
            or content_hash(plan.to_dict()) != acceptance.approved_plan_sha256):
        raise ObjectiveAcceptanceError("projected plan differs from the owner-approved plan hash")
