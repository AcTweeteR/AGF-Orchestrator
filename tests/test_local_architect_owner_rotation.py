"""Signed Owner local-provider rotation is explicit, monotonically versioned, and non-bypassable."""

import json

import pytest
from test_local_architect_evidence import _local_state
from test_provider_intelligence import PROJECT, TEST_KEY, state

from agf_orchestrator.provider_intelligence import (
    ProviderIntelligenceError,
    ProviderIntelligenceStore,
    is_codex_to_local_owner_rotation,
    sign_state,
)


def _states():
    old = state()
    new = _local_state(profile_version=2)
    return old, new


def _store(tmp_path, old):
    store = ProviderIntelligenceStore(
        tmp_path, signing_key=TEST_KEY, staging=True
    ).for_project(PROJECT)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    signed = sign_state(old, TEST_KEY, staging=True)
    store.path.write_text(json.dumps(signed.to_dict()))
    return store, signed


def test_exact_owner_rotation_requires_explicit_renewal_and_real_signed_state(tmp_path):
    old, new = _states()
    store, previous = _store(tmp_path, old)
    rotated = sign_state(new, TEST_KEY, staging=True)
    assert is_codex_to_local_owner_rotation(old, new)
    with pytest.raises(ProviderIntelligenceError, match="different evidence"):
        store._save_locked(rotated)
    assert store.load().state_sha256 == previous.state_sha256
    store._save_locked(rotated, allow_renewal=True)
    assert store._load_for_owner_recovery().state_sha256 == rotated.state_sha256
    assert store._load_for_owner_recovery().provider_interfaces == (
        ("provider-omniroute-local", "omniroute-local"),
    )


def test_rotation_requires_valid_cryptographic_signature(tmp_path):
    old, new = _states()
    store, previous = _store(tmp_path, old)
    rotated = sign_state(new, TEST_KEY, staging=True)
    tampered = rotated.to_dict()
    tampered["signature"] = "0" * 64
    from agf_orchestrator.provider_intelligence import state_from_dict
    invalid = state_from_dict(tampered)
    with pytest.raises(ProviderIntelligenceError, match="owner signature"):
        store._save_locked(invalid, allow_renewal=True)
    assert store._load_for_owner_recovery().state_sha256 == previous.state_sha256


@pytest.mark.parametrize("field", [
    "target_id", "generation", "profile_version", "constitution",
    "interface", "provider_id", "observed",
])
def test_rotation_never_broadens_owner_admission(tmp_path, field):
    from dataclasses import replace

    old, new = _states()
    store, previous = _store(tmp_path, old)
    if field == "target_id":
        new = replace(new, project_id="project-unrelated")
    elif field == "generation":
        new = replace(new, policy_generation=old.policy_generation + 2)
    elif field == "profile_version":
        new = replace(new, candidates=(replace(
            new.candidates[0], profile=replace(
                new.candidates[0].profile,
                profile_version=old.candidates[0].profile.profile_version,
            ),
        ),))
    elif field == "constitution":
        new = replace(new, constitution_record_hash="d" * 64)
    elif field == "interface":
        new = replace(new, provider_interfaces=(
            ("provider-omniroute-local", "codex"),
        ))
    elif field == "provider_id":
        new = replace(new, provider_interfaces=(("provider-codex", "codex"),))
    elif field == "observed":
        new = replace(new, observed_at=old.observed_at)
    assert not is_codex_to_local_owner_rotation(old, new)
    # The rotation must not be admitted simply because it is signed.
    # Existing regular renewal may advance profiles only for same identity.
    # Most malformed candidate variants are also rejected by state.validate.
    try:
        candidate = sign_state(new, TEST_KEY, staging=True)
        store._save_locked(candidate, allow_renewal=True)
    except (ProviderIntelligenceError, ValueError):
        pass
    else:
        pytest.fail("invalid Owner interface rotation was accepted")
    assert store._load_for_owner_recovery().state_sha256 == previous.state_sha256
