import json
from dataclasses import asdict, replace

import pytest
from test_delivery_reconciliation import git
from test_objective_acceptance import contract, install_fixture_generation
from test_task_dependencies import digest, prepared

from agf_orchestrator.delivery_reconciliation import DeliveryIntentStore
from agf_orchestrator.execution_journal import record_execution_start
from agf_orchestrator.objective_acceptance import (
    AcceptanceCriterion,
    ObjectiveAcceptanceError,
    content_hash,
)
from agf_orchestrator.objective_models import objective_from_dict
from agf_orchestrator.objective_plan import project_objective_plan
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_models import SessionStatus


def approved_first_plan(tmp_path, monkeypatch, *, wrong_hash=False):
    root, state, manager, session, plan, item = prepared(
        tmp_path, monkeypatch, single=True, integrated=False, persist_intent=False,
        preserve_planning_lineage=True,
    )
    registry = ProjectRegistry(state)
    project = registry.get(session.project_id)
    project = replace(project, policy=replace(project.policy, allow_live_execution=True))
    registry._save([project])
    payload = contract(project, session)
    objective = objective_from_dict(payload["objective"])
    criteria = tuple(AcceptanceCriterion(**entry) for entry in payload["criteria"])
    projected = project_objective_plan(plan, session, objective, criteria)
    payload.update(schema_version="2.0", approved_plan_sha256=(
        "f" * 64 if wrong_hash else content_hash(projected.to_dict())
    ))
    authority, _ = install_fixture_generation(tmp_path, monkeypatch, payload, policy_state=True)
    policy_hash = content_hash(json.loads((authority / "policy.json").read_text()))
    item = replace(item, plan_hash=digest(projected.to_dict()),
                   task_hash=digest(asdict(projected.tasks[0])),
                   policy_hash=policy_hash, constitution_id="fixture-constitution")
    item = replace(item, content_sha256=digest(item._payload()))
    return root, state, manager, session, plan, projected, item


def test_signed_first_plan_preserves_drafts_and_closes_after_integration(tmp_path, monkeypatch):
    root, state, manager, session, draft, projected, item = approved_first_plan(
        tmp_path, monkeypatch,
    )
    previous_path = session.plan_path
    bound = manager.bind_objective_plan(session.session_id)
    actual = json.loads(manager.store.ensure_safe_path(bound.plan_path).read_text())
    assert actual == projected.to_dict()
    assert manager.store.ensure_safe_path(previous_path).is_file()
    assert draft.objective_id is None
    assert manager.resume(session.session_id).status is SessionStatus.READY
    DeliveryIntentStore(state).put(item)
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    manager.resume(session.session_id)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "SUCCESS", result
    # Pre-approval history is still verified, never erased or trusted by label.
    manager.store.ensure_safe_path(previous_path).write_text("{}")
    assert manager.complete(session.session_id, execute=True,
                            confirm_execution=True)["status"] == "BLOCKED"


def test_owner_plan_hash_must_match_complete_projection(tmp_path, monkeypatch):
    _, _, manager, session, _, _, _ = approved_first_plan(tmp_path, monkeypatch, wrong_hash=True)
    before = manager.get(session.session_id).to_dict()
    with pytest.raises(ObjectiveAcceptanceError, match="plan hash"):
        manager.bind_objective_plan(session.session_id)
    assert manager.get(session.session_id).to_dict() == before


def test_prior_dispatch_without_intent_cannot_become_planning_draft(tmp_path, monkeypatch):
    _, _, manager, session, draft, _, _ = approved_first_plan(tmp_path, monkeypatch)
    record_execution_start(session.session_id, draft, draft.tasks[0].task_id)
    with pytest.raises(ObjectiveAcceptanceError, match="dispatch history"):
        manager.bind_objective_plan(session.session_id)


def test_prior_intent_cannot_be_reclassified_as_draft(tmp_path, monkeypatch):
    _, state, manager, session, _, _, item = approved_first_plan(tmp_path, monkeypatch)
    DeliveryIntentStore(state).put(item)
    with pytest.raises(ObjectiveAcceptanceError, match="existing delivery"):
        manager.bind_objective_plan(session.session_id)


@pytest.mark.parametrize("journal_kind", ["direct", "continuation"])
def test_closure_rejects_dispatch_before_signed_baseline(tmp_path, monkeypatch, journal_kind):
    root, state, manager, session, _, _, item = approved_first_plan(tmp_path, monkeypatch)
    old_plan_hash = session.artifact_hashes["plan"]
    manager.bind_objective_plan(session.session_id)
    # Simulate retained evidence discovered after binding. Closure independently
    # checks provenance rather than trusting the bind action or session flag.
    binding = {"session_id": session.session_id, "project_id": session.project_id,
               "plan_sha256": old_plan_hash, "task_id": "task-001"}
    name = ("execution-started-prior.json" if journal_kind == "direct"
            else "continuation-prior-attempt-0-started.json")
    manager.store.write_artifact(session.session_id, name, json.dumps(
        binding if journal_kind == "direct" else {"binding": binding},
    ))
    DeliveryIntentStore(state).put(item)
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    manager.resume(session.session_id)
    result = manager.complete(session.session_id, execute=True, confirm_execution=True)
    assert result["status"] == "BLOCKED", result
    assert not result["objective_completed"]


def test_unsigned_proposal_is_reviewable_and_cannot_bind_session(tmp_path, monkeypatch):
    from tools.prepare_objective_acceptance import prepare_proposal

    _, _, manager, session, _, projected, _ = approved_first_plan(
        tmp_path, monkeypatch, wrong_hash=True,
    )
    project = manager.registry.get(session.project_id)
    before = manager.get(session.session_id).to_dict()
    proposal = prepare_proposal(session.session_id, contract(project, session))
    assert proposal["status"] == "PROPOSAL"
    assert proposal["authority_effect"] == "NONE"
    assert proposal["projected_plan"] == projected.to_dict()
    assert proposal["component"]["approved_plan_sha256"] == content_hash(projected.to_dict())
    assert manager.get(session.session_id).to_dict() == before
    # Preparing caller content cannot replace the active owner's signed hash.
    with pytest.raises(ObjectiveAcceptanceError, match="plan hash"):
        manager.bind_objective_plan(session.session_id)
