"""Evaluate canonical Objective evidence; callers cannot supply acceptance results."""

from __future__ import annotations

import hashlib
import json
import subprocess

from .authority_context import resolve_authority
from .causal_findings import CausalFindingStore
from .executor import _create_worktree, _remove_worktree, _run_validations
from .models import plan_from_dict
from .objective_acceptance import (
    ObjectiveAcceptanceError,
    content_hash,
    read_objective_acceptance,
)
from .owner_authority import verify_envelope
from .project_models import ProjectStatus
from .project_registry import ProjectRegistry
from .session_models import SessionStatus
from .task_dependencies import verify_integrated_plan


def _git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE,
    ).decode().strip()


def _snapshot(project, session, store):
    current_project = ProjectRegistry(store.state_dir).get(session.project_id)
    if current_project != project:
        raise ValueError("canonical project changed")
    if (project.status is not ProjectStatus.ACTIVE
            or session.status not in {SessionStatus.READY, SessionStatus.COMPLETED}
            or session.blocking_issues or session.required_human_actions):
        raise ValueError("session is not ready for acceptance")
    root = project.repository_root
    if (_git(root, "rev-parse", "HEAD") != session.base_sha
            or _git(root, "branch", "--show-current") != project.default_branch
            or _git(root, "config", "--get", "remote.origin.url") != project.origin_url
            or _git(root, "status", "--porcelain")):
        raise ValueError("canonical target changed")
    path = store.ensure_safe_path(session.plan_path)
    if path.parent != store.ensure_safe_path(store.artifacts_dir / session.session_id):
        raise ValueError("plan outside session")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != session.artifact_hashes.get("plan"):
        raise ValueError("plan hash differs")
    plan = plan_from_dict(json.loads(data))
    plan.validate()
    acceptance = read_objective_acceptance(project, session)
    requirements = {item.requirement_id for item in acceptance.objective.requirements}
    mandatory = {item.requirement_id for item in acceptance.objective.requirements
                 if item.mandatory}
    if (plan.objective_id != acceptance.objective.objective_id
            or not mandatory <= set(plan.requirement_refs) <= requirements):
        raise ValueError("plan Objective or requirement identity differs")
    runtime = resolve_authority(project.project_id)
    if (runtime.context is None or runtime.context.manifest_hash != acceptance.generation_hash
            or runtime.snapshot is None or runtime.snapshot["kill_switch_active"]
            or runtime.policy_snapshot is None
            or runtime.policy_snapshot["active_policy_hash"] != acceptance.policy_hash):
        raise ObjectiveAcceptanceError("current policy and stop-signal authority are unresolved")
    if CausalFindingStore(store.state_dir).active(project.project_id):
        raise ValueError("unresolved findings prevent Objective acceptance")
    bindings = {
        "project_id": project.project_id, "session_id": session.session_id,
        "objective_sha256": acceptance.objective_sha256,
        "component_sha256": acceptance.component_sha256,
        "authority_generation": acceptance.generation_id,
        "authority_sha256": acceptance.generation_hash,
        "stop_generation": runtime.snapshot["generation"],
        "plan_sha256": digest, "target_sha": session.base_sha,
        "target_tree": _git(root, "rev-parse", "HEAD^{tree}"),
    }
    return plan, acceptance, bindings


def evaluate_objective_completion(project, session, store, *, execute=False, timeout=60.0):
    """Run current owner-mapped validation in isolation after all evidence gates.

    SATISFIED is a candidate decision. Only SessionManager records completion
    after rechecking this snapshot under its existing locks.
    """
    report = {"schema_version": "1.0", "status": "BLOCKED", "bindings": {},
              "criteria": [], "integration_evidence": [], "validation_evidence": [],
              "required_human_acceptance": [], "human_evidence": {},
              "blockers": [], "objective_completed": False}
    try:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout <= 300):
            raise ValueError("invalid acceptance validation timeout")
        plan, acceptance, bindings = _snapshot(project, session, store)
        report["bindings"] = bindings
        report["integration_evidence"] = verify_integrated_plan(
            session.session_id, plan, project.repository_root, state_dir=store.state_dir,
            policy_hash=acceptance.policy_hash, constitution_id=acceptance.constitution_id,
        )
        tasks = {task.task_id: task for task in plan.tasks}
        covered = {task_id for criterion in acceptance.criteria for task_id in criterion.task_ids}
        if covered != set(tasks):
            raise ValueError("Objective task coverage differs from canonical plan")
        commands = []
        for criterion in acceptance.criteria:
            if criterion.criterion_id.startswith("requirement:"):
                requirement = criterion.criterion_id.split(":")[1]
                if any(requirement not in tasks[task_id].requirement_refs
                       for task_id in criterion.task_ids):
                    raise ValueError("criterion task requirement binding differs")
            permitted = {command for task_id in criterion.task_ids
                         for command in tasks[task_id].validation_commands}
            if not set(criterion.validation_commands) <= permitted:
                raise ValueError("criterion validators differ from reviewed tasks")
            if criterion.method == "human":
                expected = {"schema_version": "1.0", "decision": "ACCEPTED",
                            **bindings, "criterion_id": criterion.criterion_id,
                            "statement_sha256": criterion.statement_sha256}
                path = store.ensure_safe_path(
                    store.artifacts_dir / session.session_id
                    / f"human-acceptance-{content_hash(expected)}.json"
                )
                if not path.is_file():
                    report["required_human_acceptance"].append(
                        {"artifact_name": path.name, "payload": expected},
                    )
                else:
                    record = json.loads(path.read_text())
                    if set(record) != {"payload", "envelope"} or record["payload"] != expected:
                        raise ValueError("human acceptance binding differs")
                    verify_envelope(expected, record["envelope"])
                    report["human_evidence"][path.name] = store.artifact_hash(str(path))
            else:
                commands.extend(criterion.validation_commands)
            report["criteria"].append({"criterion_id": criterion.criterion_id,
                                       "method": criterion.method, "status": "PENDING"})
        if report["required_human_acceptance"]:
            raise ObjectiveAcceptanceError("designated human criteria await signed acceptance")
        # All reviewed task validators remain mandatory, even when mappings overlap.
        commands.extend(command for task in plan.tasks for command in task.validation_commands)
        commands = list(dict.fromkeys(commands))
        if not execute or not project.policy.allow_live_execution:
            raise ObjectiveAcceptanceError("current validation requires authorized live execution")
        worktree = _create_worktree(project.repository_root, session.base_sha)
        try:
            evidence, passed, blockers = _run_validations(commands, worktree, timeout)
            report["validation_evidence"] = evidence
            if not passed or blockers or _git(worktree, "status", "--porcelain"):
                raise ValueError("current validation failed or modified its target")
        finally:
            if not _remove_worktree(project.repository_root, worktree):
                raise ValueError("acceptance worktree cleanup is incomplete")
        verify_current_completion_evidence(project, session, store, report)
        report["status"] = "SATISFIED"
        for criterion in report["criteria"]:
            criterion["status"] = "SATISFIED"
    except ObjectiveAcceptanceError as exc:
        report["status"] = "HUMAN_REQUIRED"
        report["blockers"] = [str(exc)]
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError):
        report["blockers"] = ["canonical acceptance evidence is missing, failed or inconsistent"]
    report["evidence_sha256"] = content_hash(report)
    return report


def verify_current_completion_evidence(project, session, store, report):
    """Recheck mutable canonical inputs after validation and before persistence."""
    plan, acceptance, bindings = _snapshot(project, session, store)
    if bindings != report["bindings"]:
        raise ValueError("acceptance bindings changed")
    proofs = verify_integrated_plan(
        session.session_id, plan, project.repository_root, state_dir=store.state_dir,
        policy_hash=acceptance.policy_hash, constitution_id=acceptance.constitution_id,
    )
    if proofs != report["integration_evidence"]:
        raise ValueError("integrated evidence changed")
    current = {}
    for criterion in acceptance.criteria:
        if criterion.method != "human":
            continue
        expected = {"schema_version": "1.0", "decision": "ACCEPTED",
                    **bindings, "criterion_id": criterion.criterion_id,
                    "statement_sha256": criterion.statement_sha256}
        path = store.ensure_safe_path(
            store.artifacts_dir / session.session_id
            / f"human-acceptance-{content_hash(expected)}.json"
        )
        data = path.read_bytes()
        record = json.loads(data)
        if set(record) != {"payload", "envelope"} or record["payload"] != expected:
            raise ValueError("human acceptance changed")
        verify_envelope(expected, record["envelope"])
        current[path.name] = hashlib.sha256(data).hexdigest()
    if current != report["human_evidence"]:
        raise ValueError("human acceptance evidence changed")
