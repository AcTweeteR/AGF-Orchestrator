"""Unsigned, exact-session Objective review bundle for independent Owner approval.

DRAFT emits the actual planned tasks and a complete draft mapping. PREPARE
consumes a human-edited candidate and validates it against canonical AGF state.
Neither operation imports Owner signing, creates signatures, approves, executes,
updates authority selectors, or invokes an external provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from agf_orchestrator.authority_generation import AuthorityGenerationStore
from agf_orchestrator.locking import project_lock, session_lock
from agf_orchestrator.models import plan_from_dict
from agf_orchestrator.objective_acceptance import (
    content_hash,
    criterion_statements,
)
from agf_orchestrator.objective_models import (
    ObjectiveStatus,
    objective_from_dict,
    objective_hash,
)
from agf_orchestrator.objective_plan import require_unexecuted_planning
from agf_orchestrator.preflight import collect_repository
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.remote_identity import canonical_remote_identity
from agf_orchestrator.session_store import SessionStore
from tools.prepare_objective_acceptance import prepare_proposal

_SESSION_ID = re.compile(r"^session-[0-9a-f]{20,24}$")
_BASE_COMPONENTS = {
    "schema_version", "generation_id", "project_id", "session_id",
    "repository_identity", "goal_sha256", "objective", "objective_sha256",
    "criteria",
}


def _source(session_id: str, *, state_root: str | Path | None = None):
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValueError("Owner review session identifier is invalid")
    sessions = SessionStore(state_root)
    registry = ProjectRegistry(sessions.state_dir)
    with session_lock(sessions.state_dir, session_id, "objective-review-draft"):
        session = sessions.load(session_id)
        with project_lock(sessions.state_dir, session.project_id, "objective-review-draft"):
            project = registry.verify_read_only(session.project_id)
            if project.status.value != "ACTIVE":
                raise ValueError("Owner review project is not ACTIVE")
            require_unexecuted_planning(session, sessions)
            repo = collect_repository(project.repository_root)
            if (
                repo.head_sha != session.base_sha
                or repo.branch != project.default_branch
                or canonical_remote_identity(repo.origin)
                != canonical_remote_identity(project.origin_url)
            ):
                raise ValueError("Owner review session target is stale")
            path = sessions.ensure_safe_path(session.plan_path)
            if path.is_symlink() or sessions.artifact_hash(str(path)) != (
                session.artifact_hashes["plan"]
            ):
                raise ValueError("Owner review canonical plan is tampered")
            plan = plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
            if plan.status.value != "READY":
                raise ValueError("Owner review requires a READY executable plan")
            generation = AuthorityGenerationStore(
                Path.home() / ".agf-orchestrator"
            ).active(project.project_id)
            if generation.scheme != "Ed25519" or generation.status.value != "ACTIVE":
                raise ValueError("Owner review requires active Ed25519 authority")
            if any(c.name == "objective_acceptance" for c in generation.components):
                raise ValueError(
                    "Existing Objective authority requires same-Objective succession; "
                    "new unrelated sessions must not replace it"
                )
            return session, project, plan, generation, sessions


def _review_draft(session, project, plan, generation) -> dict[str, Any]:
    if not plan.tasks or len(plan.tasks) > 200:
        raise ValueError("Owner review plan has no bounded tasks")
    all_ids = [task.task_id for task in plan.tasks]
    all_validators = sorted({
        command for task in plan.tasks for command in task.validation_commands
    })
    if not all_validators:
        raise ValueError("Owner review plan has no validation commands")
    requirements = []
    req_to_task = {}
    for task in plan.tasks:
        req_id = "requirement-" + hashlib.sha256(
            task.task_id.encode("utf-8")
        ).hexdigest()[:16]
        req_to_task[req_id] = task
        requirements.append({
            "requirement_id": req_id,
            "statement": task.objective,
            "mandatory": True,
            "acceptance_criteria": task.acceptance_criteria,
        })
    identifier = hashlib.sha256(
        f"{session.session_id}:{session.goal}".encode("utf-8")
    ).hexdigest()[:20]
    objective = {
        "schema_version": "1.0", "objective_id": "objective-" + identifier,
        "title": session.goal[:160], "statement": session.goal,
        "requirements": requirements,
        "constraints": [
            "All changes must stay inside each reviewed task allowed_paths."
        ],
        "prohibited_outcomes": [
            "No code execution, publication or merge without constitutional authorization."
        ],
        "completion_criteria": [
            "Every task satisfies all its reviewed validation commands."
        ],
        "owner_namespace": "owner-review-" + project.project_id,
        "status": "DRAFT",
    }
    draft_objective = objective_from_dict(objective)
    mapping = []
    for key, statement in criterion_statements(draft_objective).items():
        if key.startswith("requirement:"):
            task = req_to_task[key.split(":")[1]]
            method, refs, cmds = (
                "validation", [task.task_id], list(task.validation_commands)
            )
        elif key.startswith("completion:"):
            method, refs, cmds = "validation", all_ids, all_validators
        else:
            method, refs, cmds = "human", all_ids, []
        mapping.append({
            "criterion_id": key, "statement_sha256": content_hash(statement),
            "method": method, "task_ids": refs, "validation_commands": cmds,
        })
    component = {
        "schema_version": "1.0",
        "generation_id": f"generation-{generation.generation_number + 1}",
        "project_id": project.project_id,
        "session_id": session.session_id,
        "repository_identity": canonical_remote_identity(project.origin_url),
        "goal_sha256": content_hash(" ".join(session.goal.split())),
        "objective": objective,
        "objective_sha256": objective_hash(draft_objective),
        "criteria": mapping,
    }
    return {
        "status": "DRAFT_FOR_OWNER_REVIEW", "authority_effect": "NONE",
        "target_sha": session.base_sha, "session_id": session.session_id,
        "project_id": project.project_id, "component": component,
        "planned_tasks": [
            {
                "task_id": task.task_id,
                "title": task.title,
                "objective": task.objective,
                "allowed_paths": task.allowed_paths,
                "validation_commands": task.validation_commands,
                "risk_level": task.risk_level,
            }
            for task in plan.tasks
        ],
        "instructions": (
            "Review every task, scope, criterion, constraint and validation. "
            "Edit the component before approval. Change objective.status from "
            "DRAFT to APPROVED only after reviewing the exact text. Preparing "
            "a proposal is NOT an Owner signature or authorization."
        ),
    }


def draft(session_id: str, *, state_root: str | Path | None = None) -> dict[str, Any]:
    session, project, plan, generation, _ = _source(
        session_id, state_root=state_root,
    )
    return _review_draft(session, project, plan, generation)


def _persist_unsigned(
    sessions: SessionStore, session_id: str, proposal: dict[str, Any],
) -> tuple[Path, str]:
    digest = content_hash(proposal)
    directory = sessions.ensure_safe_path(
        sessions.state_dir / "owner-objective-reviews" / session_id
    )
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise ValueError("Owner review directory is unsafe")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    dest = sessions.ensure_safe_path(directory / f"{digest}.json")
    encoded = (json.dumps(
        proposal, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(dest, flags, 0o600)
    except FileExistsError:
        # Byte-identical retry is allowed; changed content generates a new path.
        if dest.is_symlink() or dest.read_bytes() != encoded:
            raise ValueError("Owner review proposal destination is occupied")
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    return dest, digest


def prepare(
    session_id: str, reviewed_component: dict[str, Any],
    *, state_root: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(reviewed_component, dict) or set(reviewed_component) != _BASE_COMPONENTS:
        raise ValueError("Owner reviewed component must have the exact bound schema")
    session, project, plan, generation, sessions = _source(
        session_id, state_root=state_root,
    )
    if (
        reviewed_component["project_id"] != project.project_id
        or reviewed_component["session_id"] != session.session_id
        or reviewed_component["generation_id"]
        != f"generation-{generation.generation_number + 1}"
        or reviewed_component["repository_identity"]
        != canonical_remote_identity(project.origin_url)
        or reviewed_component["goal_sha256"]
        != content_hash(" ".join(session.goal.split()))
    ):
        raise ValueError("Owner reviewed component bindings are stale or modified")
    objective = objective_from_dict(reviewed_component["objective"])
    if objective.status is not ObjectiveStatus.APPROVED:
        raise ValueError("Owner review requires an explicitly APPROVED Objective candidate")
    component = dict(reviewed_component)
    component["objective_sha256"] = objective_hash(objective)
    # All hashes, exact criterion coverage, scope, validators, original plan
    # lineage and task references are checked by canonical AGF preparation.
    proposal = prepare_proposal(session_id, component, state_dir=sessions.state_dir)
    dest, digest = _persist_unsigned(sessions, session_id, proposal)
    return {
        "status": "UNSIGNED_PROPOSAL_PREPARED",
        "authority_effect": "NONE",
        "project_id": project.project_id, "session_id": session_id,
        "target_sha": session.base_sha,
        "generation_id": component["generation_id"],
        "proposal_sha256": digest,
        "proposal_path": str(dest),
        "approved_plan_sha256": proposal["component"]["approved_plan_sha256"],
        "owner_review_required": True,
        "instructions": (
            "Independently inspect the saved JSON, then use the separate Owner "
            "publication prepare/verify/activate process with explicit reviewed "
            "manifest. Desktop cannot grant Owner signatures."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("draft", "prepare"))
    parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "draft":
            result = draft(args.session)
        else:
            raw = sys.stdin.read(200_000)
            if not raw.strip() or len(raw) >= 200_000:
                raise ValueError("Owner reviewed JSON is empty or oversized")
            result = prepare(args.session, json.loads(raw))
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Owner review refused: {exc}\n")
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
