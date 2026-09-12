import copy
import json
from dataclasses import replace

import pytest
from test_objective_acceptance import contract, install_fixture_generation
from test_task_dependencies import prepared

from agf_orchestrator.authority_generation import (
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)
from agf_orchestrator.objective_acceptance import read_objective_acceptance
from tools import owner_ed25519_authority as owner
from tools.owner_objective_publication import _validate_objective_succession, publish
from tools.prepare_objective_acceptance import prepare_proposal


def setup(tmp_path, monkeypatch):
    _, state, manager, session, _, _ = prepared(
        tmp_path, monkeypatch, single=True, integrated=False,
        persist_intent=False, preserve_planning_lineage=True,
    )
    project = manager.registry.get(session.project_id)
    component = contract(project, session)
    root, sign = install_fixture_generation(tmp_path, monkeypatch, component,
                                            schema="1.0", policy_state=True)
    store = AuthorityGenerationStore(root)
    previous = store.active(project.project_id)
    monkeypatch.setattr(owner, "PINNED_OWNER_FINGERPRINT", previous.owner_fingerprint)
    monkeypatch.setattr(owner, "_generation_root", lambda: tmp_path / "fixture-owner")
    monkeypatch.setattr(owner, "sign_envelope", lambda value, _: sign(value))
    component["generation_id"] = "generation-3"
    proposal = prepare_proposal(session.session_id, component, state_dir=state)
    return manager, project, session, store, proposal, previous


def test_prepare_verify_activate_and_retry_with_existing_root(tmp_path, monkeypatch):
    manager, project, session, store, proposal, previous = setup(tmp_path, monkeypatch)
    selector = store._selector_path(project.project_id).read_bytes()
    floor = store._floor_path(project.project_id).read_bytes()
    result = publish(project.project_id, "operation-objective", proposal)
    assert result["status"] == "PREPARED_NOT_ACTIVE"
    assert store.active(project.project_id) == previous
    assert store._selector_path(project.project_id).read_bytes() == selector
    assert store._floor_path(project.project_id).read_bytes() == floor
    assert publish(project.project_id, "operation-objective", proposal) == result
    assert publish(project.project_id, "operation-objective", proposal,
                   action="verify")["status"] == "READY_FOR_OWNER_ACTIVATION"
    candidate = store.load(project.project_id, "generation-3")
    assert candidate.status is GenerationStatus.VERIFIED
    for old in previous.components:
        new = next(item for item in candidate.components if item.name == old.name)
        assert new.artifact_hash == old.artifact_hash
    with pytest.raises(RuntimeError, match="exact reviewed"):
        publish(project.project_id, "operation-objective", proposal, action="activate",
                expected_manifest="0" * 64)
    assert store.active(project.project_id) == previous
    outcome = publish(project.project_id, "operation-objective", proposal, action="activate",
                      expected_manifest=result["manifest_hash"])
    assert outcome["status"] == "ACTIVATED"
    assert publish(project.project_id, "operation-objective", proposal, action="activate",
                   expected_manifest=result["manifest_hash"])["status"] == "ALREADY_ACTIVE"
    acceptance = read_objective_acceptance(project, manager.get(session.session_id))
    assert acceptance.approved_plan_sha256 == proposal["component"]["approved_plan_sha256"]


@pytest.mark.parametrize("changed", ["plan", "hash", "scope", "generation"])
def test_changed_reviewed_proposal_is_not_silently_rewritten(tmp_path, monkeypatch, changed):
    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    bad = copy.deepcopy(proposal)
    if changed == "plan":
        bad["projected_plan"]["tasks"][0]["title"] = "Different plan"
    elif changed == "hash":
        bad["component"]["approved_plan_sha256"] = "a" * 64
    elif changed == "scope":
        bad["projected_plan"]["tasks"][0]["allowed_paths"] = ["*"]
    else:
        bad["component"]["generation_id"] = "generation-7"
    with pytest.raises((ValueError, RuntimeError)):
        publish(project.project_id, "operation-objective", bad)
    assert store.active(project.project_id) == previous
    assert not store._generation_path(project.project_id, "generation-3").exists()


def test_candidate_collision_and_legacy_cutover_cannot_bypass_publication(tmp_path, monkeypatch):
    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    publish(project.project_id, "operation-objective", proposal)
    manifest = store._generation_path(project.project_id, "generation-3").read_bytes()
    with pytest.raises(RuntimeError, match="differs"):
        publish(project.project_id, "operation-another", proposal)
    with pytest.raises(RuntimeError, match="owner_objective_publication"):
        owner.cutover_ed25519_generation(project.project_id, "generation-3")
    assert store._generation_path(project.project_id, "generation-3").read_bytes() == manifest
    assert store.active(project.project_id) == previous


def test_incomplete_preparation_resumes_without_changing_reviewed_content(tmp_path, monkeypatch):
    import tools.owner_objective_publication as publication

    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    write = publication._immutable

    def interrupted(path, value, safe):
        if path.name == "objective_acceptance.json":
            raise OSError("process interrupted before manifest")
        return write(path, value, safe)

    monkeypatch.setattr(publication, "_immutable", interrupted)
    with pytest.raises(OSError):
        publish(project.project_id, "operation-objective", proposal)
    assert store.active(project.project_id) == previous
    monkeypatch.setattr(publication, "_immutable", write)
    result = publish(project.project_id, "operation-objective", proposal)
    assert result["status"] == "PREPARED_NOT_ACTIVE"


def test_canonical_plan_drift_blocks_activation(tmp_path, monkeypatch):
    manager, project, session, store, proposal, previous = setup(tmp_path, monkeypatch)
    result = publish(project.project_id, "operation-objective", proposal)
    path = manager.store.ensure_safe_path(session.plan_path)
    changed = json.loads(path.read_text())
    changed["tasks"][0]["title"] = "Changed since review"
    path.write_text(json.dumps(changed))
    with pytest.raises((ValueError, RuntimeError)):
        publish(project.project_id, "operation-objective", proposal, action="activate",
                expected_manifest=result["manifest_hash"])
    assert store.active(project.project_id) == previous


def test_changed_predecessor_cannot_be_legitimized_by_verification(tmp_path, monkeypatch):
    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    result = publish(project.project_id, "operation-objective", proposal)
    other = build_generation(**{
        **previous.__dict__, "generation_id": "generation-4", "generation_number": 4,
        "status": GenerationStatus.VERIFIED, "signature": None,
        "components": tuple(replace(item, generation_id="generation-4")
                            for item in previous.components),
    })
    other = replace(other, signature=owner.sign_envelope(other._unsigned(), None))
    store._save_prepared_owner_controlled(other)
    activated = build_generation(**{**other.__dict__, "status": GenerationStatus.ACTIVE})
    store._activate_owner_controlled(project.project_id, "generation-4",
                                    active_signature=owner.sign_envelope(
                                        activated._unsigned(), None))
    for action in ("prepare", "verify", "activate"):
        with pytest.raises(RuntimeError, match="predecessor"):
            publish(project.project_id, "operation-objective", proposal, action=action,
                    expected_manifest=result["manifest_hash"])
    assert store.active(project.project_id).generation_id == "generation-4"


def test_crash_after_atomic_activation_recovers_exactly_once(tmp_path, monkeypatch):
    _, project, _, store, proposal, _ = setup(tmp_path, monkeypatch)
    result = publish(project.project_id, "operation-objective", proposal)
    original = AuthorityGenerationStore._commit_metadata

    def crash_after_commit(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError("process exit after durable commit")

    monkeypatch.setattr(AuthorityGenerationStore, "_commit_metadata", crash_after_commit)
    with pytest.raises(OSError):
        publish(project.project_id, "operation-objective", proposal, action="activate",
                expected_manifest=result["manifest_hash"])
    monkeypatch.setattr(AuthorityGenerationStore, "_commit_metadata", original)
    active = store.active(project.project_id)
    assert active.generation_id == "generation-3"
    recovered = publish(project.project_id, "operation-objective", proposal, action="activate",
                        expected_manifest=result["manifest_hash"])
    assert recovered["status"] == "ALREADY_ACTIVE"
    assert store.active(project.project_id) == active


def test_disable_during_signing_prevents_activation(tmp_path, monkeypatch):
    from agf_orchestrator.project_models import ProjectStatus

    manager, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    result = publish(project.project_id, "operation-objective", proposal)
    sign = owner.sign_envelope

    def disable(value, root):
        signature = sign(value, root)
        manager.registry.set_status(project.project_id, ProjectStatus.DISABLED)
        return signature

    monkeypatch.setattr(owner, "sign_envelope", disable)
    with pytest.raises(RuntimeError, match="project changed"):
        publish(project.project_id, "operation-objective", proposal, action="activate",
                expected_manifest=result["manifest_hash"])
    assert store.active(project.project_id) == previous


def test_failed_write_before_atomic_publication_leaves_retry_possible(tmp_path, monkeypatch):
    import tools.owner_objective_publication as publication

    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    original = publication.os.fsync
    monkeypatch.setattr(publication.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        publish(project.project_id, "operation-objective", proposal)
    record = store._directory(project.project_id) / "generation-3" / "objective-publication.json"
    assert not record.exists()
    assert store.active(project.project_id) == previous
    monkeypatch.setattr(publication.os, "fsync", original)
    assert publish(project.project_id, "operation-objective", proposal)["status"] == (
        "PREPARED_NOT_ACTIVE"
    )


def test_legacy_generation_cannot_remove_installed_objective(tmp_path, monkeypatch):
    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    result = publish(project.project_id, "operation-objective", proposal)
    publish(project.project_id, "operation-objective", proposal, action="activate",
            expected_manifest=result["manifest_hash"])
    legacy = build_generation(**{
        **previous.__dict__, "generation_id": "generation-4", "generation_number": 4,
        "status": GenerationStatus.VERIFIED, "signature": None,
        "components": tuple(replace(item, generation_id="generation-4")
                            for item in previous.components),
    })
    legacy = replace(legacy, signature=owner.sign_envelope(legacy._unsigned(), None))
    store._save_prepared_owner_controlled(legacy)
    owner.verify_ed25519_generation(project.project_id, "generation-4")
    with pytest.raises((RuntimeError, ValueError), match="cannot remove Objective"):
        owner.cutover_ed25519_generation(project.project_id, "generation-4")
    assert store.active(project.project_id).generation_id == "generation-3"


def test_same_objective_can_follow_owner_authorized_target_advance(tmp_path, monkeypatch):
    manager, project, session, store, proposal, _ = setup(tmp_path, monkeypatch)
    first = publish(project.project_id, "operation-objective", proposal)
    publish(
        project.project_id,
        "operation-objective",
        proposal,
        action="activate",
        expected_manifest=first["manifest_hash"],
    )
    session = manager.get(session.session_id)
    session.artifact_hashes["external_advancement"] = "e" * 64
    manager.store.save(session)
    component = copy.deepcopy(proposal["component"])
    component["generation_id"] = "generation-4"
    successor = prepare_proposal(session.session_id, component, state_dir=manager.store.state_dir)
    successor["component"]["approved_plan_sha256"] = "f" * 64
    import tools.owner_objective_publication as publication
    monkeypatch.setattr(publication, "_prepare_proposal_locked", lambda *_: successor)

    result = publish(project.project_id, "operation-objective-successor", successor)

    assert result["status"] == "PREPARED_NOT_ACTIVE"
    assert store.active(project.project_id).generation_id == "generation-3"
    changed = copy.deepcopy(successor)
    changed["component"]["objective"]["title"] = "Changed Objective"
    with pytest.raises(RuntimeError, match="changes accepted content"):
        _validate_objective_succession(
            proposal["component"], changed["component"], manager.get(session.session_id)
        )
    unchanged_plan = copy.deepcopy(successor["component"])
    unchanged_plan["approved_plan_sha256"] = proposal["component"]["approved_plan_sha256"]
    with pytest.raises(RuntimeError, match="freshly projected plan"):
        _validate_objective_succession(
            proposal["component"], unchanged_plan, manager.get(session.session_id)
        )
