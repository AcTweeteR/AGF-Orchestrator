"""Shared live-delivery admission; policy interpretation stays in existing authorities."""

from pathlib import Path

from .authority_context import resolve_authority
from .constitution import ConstitutionAuthority, ConstitutionVerificationError
from .executor import ExecutionValidationError
from .project_models import ProjectStatus
from .project_registry import ProjectRegistryError


def admit_live_delivery(project, plan, task_id):
    if project.status is not ProjectStatus.ACTIVE:
        raise ProjectRegistryError("project is not active")
    if not project.policy.allow_live_execution or not project.policy.allow_delivery:
        raise ProjectRegistryError("project policy denies live delivery")
    try:
        ConstitutionAuthority().resolve(project.project_id)
    except ConstitutionVerificationError as exc:
        raise ProjectRegistryError(str(exc)) from exc
    if (Path.home() / ".agf-orchestrator" / "policy-state.sqlite3").exists():
        active_policy = resolve_authority(project.project_id).policy
    else:
        active_policy = None
    if active_policy is None and not project.policy.require_human_merge:
        raise ProjectRegistryError("delivery requires human merge approval")
    task = next((item for item in plan.tasks if item.task_id == task_id), None)
    if task is None:
        raise ExecutionValidationError(f"task does not exist: {task_id}")
    if active_policy is not None and active_policy.requires_human_merge(task.risk_level):
        raise ProjectRegistryError(f"active policy requires human merge for risk {task.risk_level}")
