"""Multiple independently reviewed Objectives share one signed Owner policy.

No production key or actual user repository is used in these tests.
"""
from __future__ import annotations

import copy
import json

import pytest
from test_owner_objective_publication import setup

from agf_orchestrator.objective_acceptance import (
    ObjectiveAcceptanceError,
    read_objective_acceptance,
)
from agf_orchestrator.objective_roster import (
    ObjectiveRosterError,
    validate_roster,
)
from tools.objective_review_bundle import draft, prepare
from tools.owner_objective_publication import publish


def _activate(project, proposal, operation):
    created = publish(project.project_id, operation, proposal)
    assert created["status"] == "PREPARED_NOT_ACTIVE"
    assert publish(
        project.project_id, operation, proposal, action="verify",
    )["status"] == "READY_FOR_OWNER_ACTIVATION"
    activated = publish(
        project.project_id, operation, proposal,
        action="activate", expected_manifest=created["manifest_hash"],
    )
    assert activated["status"] == "ACTIVATED"
    return activated


def _review_and_prepare(session):
    review = draft(session.session_id)
    assert review["component"]["objective"]["status"] == "DRAFT"
    component = copy.deepcopy(review["component"])
    component["objective"]["status"] = "APPROVED"
    prepared = prepare(session.session_id, component)
    assert prepared["authority_effect"] == "NONE"
    return json.loads(open(prepared["proposal_path"], encoding="utf-8").read())


def test_two_independently_approved_sessions_preserve_first_owner_objective(
    tmp_path, monkeypatch,
):
    manager, project, first_session, store, first_proposal, original = setup(
        tmp_path, monkeypatch,
    )
    _activate(project, first_proposal, "owner-first-accepted")
    prior = read_objective_acceptance(project, manager.get(first_session.session_id))
    assert store.active(project.project_id).generation_id == "generation-3"

    second_session = manager.start(
        "alpha", "Correct calculator sum and document the arithmetic in README.md",
    )
    assert second_session.status.value == "READY"
    unsigned = _review_and_prepare(second_session)
    assert unsigned["component"]["generation_id"] == "generation-4"
    _activate(project, unsigned, "owner-second-accepted")

    context = store.active(project.project_id)
    assert context.generation_id == "generation-4"
    assert {c.name for c in context.components} == {
        "constitution", "policy", "activation", "rollback",
        "registration", "provider_intelligence", "objective_acceptance",
        "objective_acceptances",
    }
    old_read = read_objective_acceptance(
        project, manager.get(first_session.session_id),
    )
    new_read = read_objective_acceptance(
        project, manager.get(second_session.session_id),
    )
    assert old_read.objective_sha256 == prior.objective_sha256
    assert old_read.approved_plan_sha256 == prior.approved_plan_sha256
    assert new_read.approved_plan_sha256 == unsigned["component"]["approved_plan_sha256"]
    assert new_read.generation_id == "generation-4"
    assert new_read.generation_hash == context.manifest_hash

    third_session = manager.start(
        "alpha", "Add a bounded calculator documentation regression check to README.md",
    )
    assert third_session.status.value == "READY"
    third_proposal = _review_and_prepare(third_session)
    assert third_proposal["component"]["generation_id"] == "generation-5"
    _activate(project, third_proposal, "owner-third-accepted")
    assert store.active(project.project_id).generation_id == "generation-5"
    assert read_objective_acceptance(
        project, manager.get(first_session.session_id)
    ).approved_plan_sha256 == prior.approved_plan_sha256
    assert read_objective_acceptance(
        project, manager.get(second_session.session_id)
    ).approved_plan_sha256 == unsigned["component"]["approved_plan_sha256"]
    assert read_objective_acceptance(
        project, manager.get(third_session.session_id)
    ).approved_plan_sha256 == third_proposal["component"]["approved_plan_sha256"]


def test_unsigned_additional_session_cannot_see_any_existing_approval(
    tmp_path, monkeypatch,
):
    manager, project, first_session, store, proposal, _ = setup(tmp_path, monkeypatch)
    _activate(project, proposal, "owner-first-only")
    second_session = manager.start(
        "alpha", "Improve calculator arithmetic documentation in README.md",
    )
    assert second_session.status.value == "READY"
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, second_session)
    review = draft(second_session.session_id)
    assert review["authority_effect"] == "NONE"
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, second_session)


def test_signed_roster_rejects_duplicates_and_cross_session_entries():
    old = {"session_id": "session-" + "a" * 20}
    new = {
        "session_id": "session-" + "b" * 20,
        "project_id": "project-0123456789abcdef",
        "schema_version": "2.0", "generation_id": "generation-4",
        "approved_plan_sha256": "a" * 64,
    }
    base = {
        "schema_version": "1.0",
        "project_id": "project-0123456789abcdef",
        "entries": {new["session_id"]: new},
    }
    assert len(validate_roster(base, base["project_id"], old)) == 1
    changed = copy.deepcopy(base)
    changed["entries"][old["session_id"]] = {**new, "session_id": old["session_id"]}
    with pytest.raises(ObjectiveRosterError, match="duplicated"):
        validate_roster(changed, base["project_id"], old)
    changed = copy.deepcopy(base)
    changed["entries"][new["session_id"]]["project_id"] = "project-ffffffffffffffff"
    with pytest.raises(ObjectiveRosterError, match="binding"):
        validate_roster(changed, base["project_id"], old)
    changed = copy.deepcopy(base)
    changed["entries"][new["session_id"]]["approved_plan_sha256"] = "tampered"
    with pytest.raises(ObjectiveRosterError, match="binding"):
        validate_roster(changed, base["project_id"], old)
    changed = copy.deepcopy(base)
    changed["entries"] = {f"session-{i:020x}": new for i in range(65)}
    with pytest.raises(ObjectiveRosterError, match="schema"):
        validate_roster(changed, base["project_id"], old)



def test_another_session_cannot_reuse_or_change_a_previously_signed_objective(
    tmp_path, monkeypatch,
):
    manager, project, old_session, store, proposal, _ = setup(tmp_path, monkeypatch)
    _activate(project, proposal, "owner-old-preserved")
    second = manager.start(
        "alpha", "Improve calculator arithmetic documentation with bounded checks",
    )
    _activate(project, _review_and_prepare(second), "owner-second-preserved")
    before = store.active(project.project_id)
    candidate = _review_and_prepare(second)
    assert candidate["component"]["generation_id"] == "generation-5"
    with pytest.raises(RuntimeError, match=(
        "freshly projected plan|owner-authorized target advance"
    )):
        publish(project.project_id, "owner-replace-previous", candidate)
    assert store.active(project.project_id) == before
    assert read_objective_acceptance(
        project, manager.get(second.session_id)
    ).objective_sha256 != ""


def test_tampering_signed_objective_roster_fails_closed(
    tmp_path, monkeypatch,
):
    manager, project, old_session, store, proposal, _ = setup(tmp_path, monkeypatch)
    _activate(project, proposal, "owner-original-untouched")
    second = manager.start(
        "alpha", "Document calculator sum behavior in README.md",
    )
    _activate(project, _review_and_prepare(second), "owner-second-untouched")
    directory = store._directory(project.project_id)
    signed = directory / "generation-4" / "objective_acceptances.json"
    before = signed.read_bytes()
    mutated = json.loads(before)
    mutated["entries"][second.session_id]["objective"]["title"] = "FORGED"
    signed.write_text(json.dumps(mutated))
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, second)
    signed.write_bytes(before)
    assert read_objective_acceptance(project, second).objective_sha256
