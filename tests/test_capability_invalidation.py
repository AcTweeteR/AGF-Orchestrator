from dataclasses import replace

import pytest

from agf_orchestrator.capability_invalidation import (
    CapabilityEvidenceRecord,
    CapabilityInvalidationError,
    CapabilityInvalidator,
    InvalidationReason,
)
from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)

PROJECT = "project-efc8e8ef7be7050b"
STATE = "a" * 64


def evidence(provider="provider-codex", version=1, state=STATE, health=1):
    source = f"approved:{provider}:{version}"
    profile = CapabilityProfile(
        "1.0", f"profile-{provider}", PROJECT, provider, version, source,
        sha256_text(source), "2026-08-09T12:00:00Z", "2026-08-10T12:00:00Z",
        (CapabilityObservation("tool-calling", CapabilityStatus.SUPPORTED, "v1"),), "0" * 64,
    )
    profile = CapabilityProfile(
        **{**profile.__dict__, "profile_sha256": capability_profile_hash(profile)}
    )
    return CapabilityEvidenceRecord(profile, state, health, "2026-08-09T12:00:00Z")


def test_current_evidence_is_eligible():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    assert store.eligible(
        current, project_id=PROJECT, provider_state_sha256=STATE,
        health_generation=1, now="2026-08-09T12:00:01Z",
    )


@pytest.mark.parametrize("reason", [
    InvalidationReason.PROVIDER_UPGRADE,
    InvalidationReason.HEALTH_CHANGE,
    InvalidationReason.CONFLICT,
    InvalidationReason.STALE,
])
def test_invalidation_tombstone_blocks_reuse(reason):
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    tombstone = store.invalidate(current, reason, "2026-08-09T13:00:00Z")
    assert tombstone.profile_sha256 == current.profile.profile_sha256
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        store.eligible(
            current, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-09T13:00:01Z",
        )
    with pytest.raises(CapabilityInvalidationError, match="resurrected"):
        store.record(current)


def test_provider_upgrade_and_health_change_invalidate_old_evidence():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    assert store.invalidate_provider(
        "provider-codex", "b" * 64, "2026-08-09T13:00:00Z", InvalidationReason.PROVIDER_UPGRADE
    )
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        store.eligible(
            current, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-09T13:00:01Z",
        )
    fresh = evidence(version=2, state="b" * 64, health=2)
    store.record(fresh)
    with pytest.raises(CapabilityInvalidationError, match="health"):
        store.eligible(
            fresh, project_id=PROJECT, provider_state_sha256="b" * 64,
            health_generation=3, now="2026-08-09T13:00:01Z",
        )


def test_mismatch_creates_tombstone_and_survives_restart_readback():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    with pytest.raises(CapabilityInvalidationError, match="state changed"):
        store.eligible(
            current, project_id=PROJECT, provider_state_sha256="b" * 64,
            health_generation=1, now="2026-08-09T13:00:00Z",
        )
    restored = CapabilityInvalidator.from_state(store.export_state())
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        restored.eligible(
            current, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-09T13:00:01Z",
        )
    assert restored.invalidation_digest() == store.invalidation_digest()


def test_conflicting_same_version_evidence_is_tombstoned():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    conflict_profile = replace(
        current.profile,
        capabilities=(CapabilityObservation("tool-calling", CapabilityStatus.UNSUPPORTED, None),),
        profile_sha256="0" * 64,
    )
    conflict_profile = replace(
        conflict_profile, profile_sha256=capability_profile_hash(conflict_profile)
    )
    conflict = CapabilityEvidenceRecord(conflict_profile, STATE, 1, current.recorded_at)
    with pytest.raises(CapabilityInvalidationError, match="conflicting"):
        store.record(conflict)
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        store.record(conflict)


def test_malformed_tombstone_and_state_are_rejected():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    store.invalidate(current, InvalidationReason.STALE, "2026-08-09T13:00:00Z")
    serialized = store.export_state().replace("2026-08-09T13:00:00Z", "bad")
    with pytest.raises(CapabilityInvalidationError, match="state hash"):
        CapabilityInvalidator.from_state(serialized)
    with pytest.raises(CapabilityInvalidationError, match="timestamp"):
        store.invalidate(current, InvalidationReason.STALE, "bad")


def test_cross_project_and_state_mismatch_fail_closed():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    with pytest.raises(CapabilityInvalidationError, match="binding"):
        store.eligible(
            current, project_id="project-other", provider_state_sha256=STATE,
            health_generation=1, now="2026-08-09T12:00:01Z",
        )
    with pytest.raises(CapabilityInvalidationError, match="state changed"):
        store.eligible(
            current, project_id=PROJECT, provider_state_sha256="b" * 64,
            health_generation=1, now="2026-08-09T12:00:01Z",
        )


def test_stale_evidence_and_invalid_metadata_are_rejected():
    store = CapabilityInvalidator()
    stale = evidence()
    with pytest.raises(CapabilityInvalidationError, match="stale"):
        store.eligible(
            stale, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-10T12:00:00Z",
        )
    restored = CapabilityInvalidator.from_state(store.export_state())
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        restored.eligible(
            stale, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-10T12:00:00Z",
        )
    with pytest.raises(CapabilityInvalidationError, match="invalid"):
        store.record(CapabilityEvidenceRecord(stale.profile, "not-a-hash", 1, stale.recorded_at))


def test_versioned_replacement_and_tombstone_digest_are_deterministic():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    store.invalidate(current, InvalidationReason.REPLAY, "2026-08-09T13:00:00Z")
    digest = store.invalidation_digest()
    assert digest == store.invalidation_digest()
    with pytest.raises(CapabilityInvalidationError, match="resurrected"):
        store.record(current)
    store.record(evidence(version=2))


def test_restart_preserves_non_tombstoned_version_history():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    restored = CapabilityInvalidator.from_state(store.export_state())
    with pytest.raises(CapabilityInvalidationError, match="monotonically"):
        restored.record(current)


def test_newer_profile_version_supersedes_old_evidence():
    store = CapabilityInvalidator()
    current = evidence()
    store.record(current)
    newer = evidence(version=2)
    store.record(newer)
    with pytest.raises(CapabilityInvalidationError, match="invalidated"):
        store.eligible(
            current, project_id=PROJECT, provider_state_sha256=STATE,
            health_generation=1, now="2026-08-09T12:00:01Z",
        )
