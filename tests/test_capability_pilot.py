import pytest

from agf_orchestrator.capability_invalidation import CapabilityEvidenceRecord
from agf_orchestrator.capability_pilot import CapabilityPilot, CapabilityPilotError
from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates

PROJECT = "project-efc8e8ef7be7050b"
GATES = SelectionGates(True, True, True, True, True, True)


def pair(provider, priority):
    source = f"pilot:{provider}"
    profile = CapabilityProfile(
        "1.0", f"profile-{provider}", PROJECT, provider, 1, source,
        sha256_text(source), "2026-08-09T12:00:00Z", "2026-08-10T12:00:00Z",
        (CapabilityObservation("tool-calling", CapabilityStatus.SUPPORTED, "v1"),), "0" * 64,
    )
    profile = CapabilityProfile(
        **{**profile.__dict__, "profile_sha256": capability_profile_hash(profile)}
    )
    return (
        CapabilityCandidate(profile, priority),
        CapabilityEvidenceRecord(profile, "a" * 64, 1, "2026-08-09T12:00:00Z"),
    )


def test_disposable_pilot_proves_failure_fallback_restart_and_audit():
    primary, primary_evidence = pair("provider-primary", 0)
    fallback, fallback_evidence = pair("provider-fallback", 1)
    report = CapabilityPilot().run(
        (primary, fallback), (primary_evidence, fallback_evidence),
        project_id=PROJECT, required_capability="tool-calling",
        now="2026-08-09T12:00:01Z", gates=GATES,
    )
    assert report.selected_provider == "provider-fallback"
    assert report.fallback_used
    assert report.restart_verified
    assert [event.name for event in report.audit_events] == [
        "record", "failure", "restart", "eligibility", "selection",
    ]
    assert "profile=profile-provider-primary" in report.audit_events[3].detail
    assert "hash=" in report.audit_events[4].detail


def test_pilot_is_deterministic_on_repeated_runs():
    primary, primary_evidence = pair("provider-primary", 0)
    fallback, fallback_evidence = pair("provider-fallback", 1)
    kwargs = {
        "project_id": PROJECT, "required_capability": "tool-calling",
        "now": "2026-08-09T12:00:01Z", "gates": GATES,
    }
    first = CapabilityPilot().run(
        (primary, fallback), (primary_evidence, fallback_evidence), **kwargs
    )
    second = CapabilityPilot().run(
        (primary, fallback), (primary_evidence, fallback_evidence), **kwargs
    )
    assert first == second


def test_pilot_fails_when_no_safe_fallback_exists():
    primary, primary_evidence = pair("provider-primary", 0)
    fallback, fallback_evidence = pair("provider-fallback", 1)
    unknown_profile = fallback.profile.__class__(
        **{
            **fallback.profile.__dict__,
            "capabilities": (
                CapabilityObservation("tool-calling", CapabilityStatus.UNKNOWN, None),
            ),
            "profile_sha256": "0" * 64,
        }
    )
    unknown_profile = unknown_profile.__class__(
        **{**unknown_profile.__dict__, "profile_sha256": capability_profile_hash(unknown_profile)}
    )
    unknown_evidence = CapabilityEvidenceRecord(
        unknown_profile, fallback_evidence.provider_state_sha256,
        fallback_evidence.health_generation, fallback_evidence.recorded_at,
    )
    with pytest.raises(CapabilityPilotError, match="no safe fallback"):
        CapabilityPilot().run(
            (primary, CapabilityCandidate(unknown_profile, 1)),
            (primary_evidence, unknown_evidence), project_id=PROJECT,
            required_capability="tool-calling", now="2026-08-09T12:00:01Z", gates=GATES,
        )


def test_pilot_rejects_candidate_evidence_hash_mismatch():
    primary, primary_evidence = pair("provider-primary", 0)
    fallback, fallback_evidence = pair("provider-fallback", 1)
    with pytest.raises(CapabilityPilotError, match="binding mismatch"):
        CapabilityPilot().run(
            (primary, fallback), (fallback_evidence, primary_evidence),
            project_id=PROJECT, required_capability="tool-calling",
            now="2026-08-09T12:00:01Z", gates=GATES,
        )
