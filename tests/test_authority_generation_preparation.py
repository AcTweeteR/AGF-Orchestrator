"""Successive owner generations use ephemeral fixture authority only."""

import json
from dataclasses import replace

import pytest
from test_authority_generation import LEGACY_KEY, PROJECT, generation

from agf_orchestrator.authority_generation import (
    AuthorityGenerationError,
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)


def active_store(tmp_path):
    store = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    initial = generation()
    store._save_prepared_owner_controlled(initial)
    store._activate_owner_controlled(PROJECT, initial.generation_id)
    return store, store.active(PROJECT)


def successor(active, status=GenerationStatus.VERIFIED, project_id=PROJECT):
    initial = generation()
    return build_generation(**{
        **initial.__dict__, "generation_id": "generation-3", "generation_number": 3,
        "project_id": project_id, "status": status, "operation_id": "operation-migration-3",
        "predecessor_id": active.generation_id, "predecessor_hash": active.manifest_hash,
        "components": tuple(replace(item, generation_id="generation-3", project_id=project_id)
                            for item in initial.components),
    })


@pytest.mark.parametrize("status", [
    GenerationStatus.PREPARING, GenerationStatus.PREPARED, GenerationStatus.VERIFIED,
])
def test_next_prepared_generation_preserves_current_authority_and_floor(tmp_path, status):
    store, active = active_store(tmp_path)
    next_generation = successor(active, status)
    store._save_prepared_owner_controlled(next_generation)
    restarted = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    assert restarted.active(PROJECT) == active
    assert restarted._floor(PROJECT) == 2
    if status is GenerationStatus.VERIFIED:
        restarted._activate_owner_controlled(PROJECT, next_generation.generation_id)
        assert restarted.active(PROJECT).generation_id == "generation-3"
        assert restarted._floor(PROJECT) == 3
    else:
        with pytest.raises(AuthorityGenerationError, match="not ready for cutover"):
            restarted._activate_owner_controlled(PROJECT, next_generation.generation_id)
        assert restarted.active(PROJECT) == active


def test_uncommitted_preparation_does_not_require_a_floor(tmp_path):
    store = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    store._save_prepared_owner_controlled(generation(GenerationStatus.PREPARED))
    assert not store._floor_path(PROJECT).exists()
    assert store._floor(PROJECT) == 0


@pytest.mark.parametrize("damage", ["missing", "downgraded"])
def test_committed_generation_rejects_missing_or_lowered_floor(tmp_path, damage):
    store, active = active_store(tmp_path)
    store._save_prepared_owner_controlled(successor(active))
    path = store._floor_path(PROJECT)
    if damage == "missing":
        path.unlink()
    else:
        payload = json.loads(path.read_text())
        payload["generation_number"] = 1
        path.write_text(json.dumps(payload))
    with pytest.raises(AuthorityGenerationError, match="floor"):
        store.active(PROJECT)
    with pytest.raises(AuthorityGenerationError, match="floor"):
        store._activate_owner_controlled(PROJECT, "generation-3")


def test_successful_next_cutover_does_not_allow_old_verified_replay(tmp_path):
    store, active = active_store(tmp_path)
    store._save_prepared_owner_controlled(successor(active))
    store._activate_owner_controlled(PROJECT, "generation-3")
    current = store.active(PROJECT)
    # Re-present the signed legacy VERIFIED manifest, which is not sufficient
    # to lower the committed floor or reactivate an older generation.
    store._save_prepared_owner_controlled(generation())
    with pytest.raises(AuthorityGenerationError, match="downgrade or replay"):
        store._activate_owner_controlled(PROJECT, "generation-2")
    assert store.active(PROJECT) == current
    assert store._floor(PROJECT) == 3


def test_generation_status_must_be_authenticated_before_floor_filtering(tmp_path):
    store, _ = active_store(tmp_path)
    path = store._generation_path(PROJECT, "generation-2")
    payload = json.loads(path.read_text())
    payload["status"] = "PREPARED"
    path.write_text(json.dumps(payload))
    with pytest.raises(AuthorityGenerationError):
        store._floor(PROJECT)


def test_signed_generation_cannot_cross_project_path_binding(tmp_path):
    store, active = active_store(tmp_path)
    other_project = "project-0123456789abcdef"
    value = successor(active, project_id=other_project)
    store._save_prepared_owner_controlled(value)
    source = store._generation_path(other_project, "generation-3")
    target = store._generation_path(PROJECT, "generation-3")
    target.write_bytes(source.read_bytes())
    with pytest.raises(AuthorityGenerationError, match="identity does not match path"):
        store.load(PROJECT, "generation-3")
    with pytest.raises(AuthorityGenerationError):
        store.active(PROJECT)
