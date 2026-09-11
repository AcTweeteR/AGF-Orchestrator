"""Prepare reviewable Objective content without signing or activating authority.

Run explicitly with a canonical session and a criterion-complete draft component.
The output is only a proposal for the existing owner-controlled authority process.
"""

import argparse
import json
from pathlib import Path

from agf_orchestrator.locking import project_lock, session_lock
from agf_orchestrator.models import plan_from_dict
from agf_orchestrator.objective_acceptance import (
    ObjectiveAcceptanceError,
    content_hash,
    parse_objective_proposal,
)
from agf_orchestrator.objective_plan import project_objective_plan, require_unexecuted_planning
from agf_orchestrator.preflight import collect_repository
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.remote_identity import canonical_remote_identity
from agf_orchestrator.session_store import SessionStore


def prepare_proposal(session_id, component, *, state_dir=None):
    """Return unsigned content; do not mutate the session or owner state."""
    if (not isinstance(component, dict)
            or not isinstance(component.get("generation_id"), str)
            or not component["generation_id"].strip()):
        raise ObjectiveAcceptanceError("proposal requires a generation identity")
    store = SessionStore(state_dir)
    registry = ProjectRegistry(store.state_dir)
    with session_lock(store.state_dir, session_id, "objective-proposal"):
        session = store.load(session_id)
        project = registry.get(session.project_id)
        with project_lock(store.state_dir, project.project_id, "objective-proposal"):
            return _prepare_proposal_locked(session, project, component, store, registry)


def _prepare_proposal_locked(session, project, component, store, registry):
    """Content validation while the caller holds canonical session/project locks."""
    require_unexecuted_planning(session, store)
    repository = collect_repository(project.repository_root)
    if (project.status.value != "ACTIVE" or repository.head_sha != session.base_sha
            or repository.branch != project.default_branch
            or canonical_remote_identity(repository.origin)
            != canonical_remote_identity(project.origin_url)):
        raise ObjectiveAcceptanceError("proposal target differs from canonical session")
    objective, criteria = parse_objective_proposal(
        component, project, session, component.get("generation_id"),
    )
    path = store.ensure_safe_path(session.plan_path)
    plan = plan_from_dict(json.loads(path.read_text()))
    projected = project_objective_plan(plan, session, objective, criteria)
    proposal = {**component, "schema_version": "2.0",
                "approved_plan_sha256": content_hash(projected.to_dict())}
    parse_objective_proposal(proposal, project, session, proposal["generation_id"])
    if (registry.get(project.project_id) != project
            or collect_repository(project.repository_root) != repository):
        raise ObjectiveAcceptanceError("proposal target changed during preparation")
    return {"status": "PROPOSAL", "authority_effect": "NONE",
            "component": proposal, "projected_plan": projected.to_dict()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--component", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        proposal = prepare_proposal(args.session, json.loads(args.component.read_text()))
        # Exclusive creation preserves earlier review evidence; this tool has no
        # authority-store destination, signer, selector or activation operation.
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(proposal, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write("\n")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Objective proposal failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
