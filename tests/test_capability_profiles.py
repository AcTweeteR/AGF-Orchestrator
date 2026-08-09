import pytest

from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityProfileError,
    CapabilityProfileRegistry,
    CapabilityStatus,
    canonical_profile_json,
    capability_profile_hash,
    profile_from_dict,
    sha256_text,
)


def profile(**changes):
    source = "approved-local-inventory:v1"
    values = {
        "schema_version": "1.0",
        "profile_id": "profile-codex",
        "project_id": "project-efc8e8ef7be7050b",
        "provider_id": "provider-codex",
        "profile_version": 1,
        "provenance_source": source,
        "provenance_sha256": sha256_text(source),
        "observed_at": "2026-08-09T12:00:00Z",
        "expires_at": "2026-08-10T12:00:00Z",
        "capabilities": (
            CapabilityObservation("tool-calling", CapabilityStatus.SUPPORTED, "v1"),
            CapabilityObservation("local-execution", CapabilityStatus.UNSUPPORTED, None),
            CapabilityObservation("agentic-loop", CapabilityStatus.UNKNOWN, None),
        ),
        "profile_sha256": "0" * 64,
    }
    values.update(changes)
    candidate = CapabilityProfile(**values)
    return candidate.with_hash() if hasattr(candidate, "with_hash") else candidate


def valid_profile():
    candidate = profile()
    return CapabilityProfile(
        **{**candidate.__dict__, "profile_sha256": capability_profile_hash(candidate)}
    )


def test_valid_profile_accepted_and_round_trips():
    current = valid_profile()
    current.validate()
    assert profile_from_dict(current.to_dict()) == current


def test_canonical_serialization_and_hash_are_stable():
    current = valid_profile()
    restored = profile_from_dict(current.to_dict())
    assert canonical_profile_json(current) == canonical_profile_json(restored)
    assert capability_profile_hash(current) == current.profile_sha256


def test_content_mutation_changes_and_rejects_hash():
    current = valid_profile()
    mutated = CapabilityProfile(**{
        **current.__dict__,
        "capabilities": (
            CapabilityObservation("tool-calling", CapabilityStatus.UNSUPPORTED, None),
        ),
    })
    assert capability_profile_hash(mutated) != current.profile_sha256
    with pytest.raises(CapabilityProfileError, match="profile_sha256"):
        mutated.validate()


def test_provenance_is_retained_and_bound():
    current = valid_profile()
    assert current.to_dict()["provenance_source"] == "approved-local-inventory:v1"
    with pytest.raises(CapabilityProfileError, match="provenance"):
        CapabilityProfile(**{**current.__dict__, "provenance_source": "changed"}).validate()


def test_supported_unsupported_and_unknown_are_explicit():
    current = valid_profile()
    assert current.require_supported("tool-calling") == "v1"
    with pytest.raises(CapabilityProfileError):
        current.require_supported("local-execution")
    with pytest.raises(CapabilityProfileError):
        current.require_supported("agentic-loop")


@pytest.mark.parametrize("payload_change", [
    {"schema_version": "2.0"},
    {"profile_version": 0},
    {"observed_at": "malformed"},
    {"capabilities": [{"name": "tool-calling", "status": "MAYBE", "value": "v1"}]},
])
def test_malformed_or_unsupported_versions_are_rejected(payload_change):
    payload = valid_profile().to_dict()
    payload.update(payload_change)
    with pytest.raises(CapabilityProfileError):
        profile_from_dict(payload)


def test_stale_profile_is_rejected_at_freshness_boundary():
    current = valid_profile()
    assert current.is_stale("2026-08-10T12:00:00Z")
    assert not current.is_stale("2026-08-09T12:00:00Z")
    with pytest.raises(CapabilityProfileError, match="stale"):
        current.validate_at("2026-08-10T12:00:00Z")


def test_expiry_must_follow_observation_and_timestamps_are_real():
    current = valid_profile()
    with pytest.raises(CapabilityProfileError, match="after"):
        CapabilityProfile(**{**current.__dict__, "expires_at": current.observed_at}).validate()
    with pytest.raises(CapabilityProfileError, match="real UTC"):
        CapabilityProfile(**{**current.__dict__, "observed_at": "2026-99-99T12:00:00Z"}).validate()
    no_expiry = CapabilityProfile(**{**current.__dict__, "expires_at": None})
    with pytest.raises(CapabilityProfileError, match="real UTC"):
        CapabilityProfile(
            **{**no_expiry.__dict__, "observed_at": "2026-99-99T12:00:00Z"}
        ).validate()
    with pytest.raises(CapabilityProfileError, match="real UTC"):
        no_expiry.is_stale("2026-99-99T12:00:00Z")


def test_wrong_binding_is_rejected():
    current = valid_profile()
    with pytest.raises(CapabilityProfileError, match="binding"):
        current.validate_binding("project-other", current.provider_id)
    with pytest.raises(CapabilityProfileError, match="binding"):
        current.validate_binding(current.project_id, "provider-other")


def test_repeated_evaluation_is_deterministic_and_no_promotion_occurs():
    current = valid_profile()
    assert [current.require_supported("tool-calling") for _ in range(3)] == ["v1"] * 3
    with pytest.raises(CapabilityProfileError):
        current.require_supported("agentic-loop")


def test_registry_enforces_project_binding_and_monotonic_versions():
    registry = CapabilityProfileRegistry("project-efc8e8ef7be7050b")
    current = valid_profile()
    registry.record(current)
    assert registry.get(current.provider_id, current.profile_id) == current
    with pytest.raises(CapabilityProfileError, match="monotonically"):
        registry.record(current)
    wrong = CapabilityProfile(**{**current.__dict__, "project_id": "project-other"})
    with pytest.raises(CapabilityProfileError, match="binding"):
        registry.record(wrong)


def test_secret_shaped_provenance_and_values_are_rejected():
    current = valid_profile()
    secret_source = "inventory:sk-abcdefghijklmnop"
    with pytest.raises(CapabilityProfileError, match="secret"):
        CapabilityProfile(
            **{
                **current.__dict__,
                "provenance_source": secret_source,
                "provenance_sha256": sha256_text(secret_source),
                "profile_sha256": "0" * 64,
            }
        ).validate()
    secret_capability = CapabilityObservation(
        "tool-calling", CapabilityStatus.SUPPORTED, "ghp_abcdefghijklmnop"
    )
    with pytest.raises(CapabilityProfileError, match="secret"):
        secret_capability.validate()
