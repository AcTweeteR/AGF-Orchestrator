import pytest

from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import (
    CapabilityCandidate,
    CapabilitySelectionError,
    CapabilitySelector,
    SelectionGates,
)

PROJECT = "project-efc8e8ef7be7050b"
VERIFIED_GATES = SelectionGates(True, True, True, True, True, True)


def make_profile(provider_id, status=CapabilityStatus.SUPPORTED, expires_at="2026-08-10T12:00:00Z"):
    source = f"approved:{provider_id}"
    profile = CapabilityProfile(
        "1.0", f"profile-{provider_id}", PROJECT, provider_id, 1, source,
        sha256_text(source), "2026-08-09T12:00:00Z", expires_at,
        (CapabilityObservation("tool-calling", status, "v1"),), "0" * 64,
    )
    return CapabilityProfile(
        **{**profile.__dict__, "profile_sha256": capability_profile_hash(profile)}
    )


def candidate(provider_id, priority=0, status=CapabilityStatus.SUPPORTED, **kwargs):
    return CapabilityCandidate(make_profile(provider_id, status, **kwargs), priority)


def test_selects_deterministically_by_priority_then_identity():
    selector = CapabilitySelector()
    result = selector.select(
        [candidate("provider-z", 2), candidate("provider-a", 1)],
        project_id=PROJECT, required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
        gates=VERIFIED_GATES,
    )
    assert (result.provider_id, result.fallback_used) == ("provider-a", False)


def test_unknown_and_unsupported_capabilities_are_ineligible():
    selector = CapabilitySelector()
    for status in (CapabilityStatus.UNKNOWN, CapabilityStatus.UNSUPPORTED):
        with pytest.raises(CapabilitySelectionError, match="not supported"):
            selector.select(
                [candidate("provider-a", status=status)], project_id=PROJECT,
                required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
                gates=VERIFIED_GATES,
            )


def test_fallback_is_explicit_and_recorded():
    selector = CapabilitySelector()
    result = selector.select(
        [candidate("provider-a", 0, CapabilityStatus.UNKNOWN), candidate("provider-b", 1)],
        project_id=PROJECT, required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
        gates=VERIFIED_GATES,
    )
    assert result.provider_id == "provider-b"
    assert result.fallback_used
    assert result.rejected_reasons == (
        "provider-a: required capability is not supported: tool-calling",
    )


def test_fallback_can_be_disallowed():
    with pytest.raises(CapabilitySelectionError, match="fallback"):
        CapabilitySelector().select(
            [candidate("provider-a", 0, CapabilityStatus.UNKNOWN), candidate("provider-b", 1)],
            project_id=PROJECT, required_capabilities=["tool-calling"],
            now="2026-08-09T12:00:00Z",
            gates=SelectionGates(True, True, True, True, True, True, allow_fallback=False),
        )


def test_failed_governed_gate_blocks_selection():
    with pytest.raises(CapabilitySelectionError, match="failed gates: privacy"):
        CapabilitySelector().select(
            [candidate("provider-a")], project_id=PROJECT,
            required_capabilities=["tool-calling"], now="2026-08-09T12:00:02Z",
            gates=SelectionGates(True, False, True, True, True, True),
        )


def test_stale_and_wrong_project_profiles_block():
    with pytest.raises(CapabilitySelectionError, match="stale"):
        CapabilitySelector().select(
            [candidate("provider-a", expires_at="2026-08-09T12:00:01Z")], project_id=PROJECT,
            required_capabilities=["tool-calling"], now="2026-08-09T12:00:02Z",
            gates=VERIFIED_GATES,
        )
    with pytest.raises(CapabilitySelectionError, match="binding"):
        CapabilitySelector().select(
            [candidate("provider-a")], project_id="project-other",
            required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
            gates=VERIFIED_GATES,
        )


def test_diagnostic_only_provider_is_never_selected():
    with pytest.raises(CapabilitySelectionError, match="diagnostic-only"):
        CapabilitySelector().select(
            [CapabilityCandidate(make_profile("provider-qwen"), 0, diagnostic_only=True)],
            project_id=PROJECT, required_capabilities=["tool-calling"],
            now="2026-08-09T12:00:00Z", gates=VERIFIED_GATES,
        )


def test_empty_requirements_and_empty_candidates_fail_closed():
    with pytest.raises(CapabilitySelectionError, match="required_capabilities"):
        CapabilitySelector().select(
            [], project_id=PROJECT, required_capabilities=[], now="2026-08-09T12:00:00Z"
        )
    with pytest.raises(CapabilitySelectionError, match="no eligible"):
        CapabilitySelector().select(
            [], project_id=PROJECT, required_capabilities=["tool-calling"],
            now="2026-08-09T12:00:00Z", gates=VERIFIED_GATES,
        )


def test_missing_gate_evidence_fails_closed():
    with pytest.raises(CapabilitySelectionError, match="missing:policy"):
        CapabilitySelector().select(
            [candidate("provider-a")], project_id=PROJECT,
            required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
        )


def test_missing_empirical_evidence_fails_closed():
    with pytest.raises(CapabilitySelectionError, match="missing:empirical_evidence"):
        CapabilitySelector().select(
            [candidate("provider-a")], project_id=PROJECT,
            required_capabilities=["tool-calling"], now="2026-08-09T12:00:00Z",
            gates=SelectionGates(True, True, True, True, True),
        )


def test_qwen_is_diagnostic_only_even_if_caller_lies():
    with pytest.raises(CapabilitySelectionError, match="diagnostic-only"):
        CapabilitySelector().select(
            [CapabilityCandidate(make_profile("provider-qwen"), 0, diagnostic_only=False)],
            project_id=PROJECT, required_capabilities=["tool-calling"],
            now="2026-08-09T12:00:00Z", gates=VERIFIED_GATES,
        )
