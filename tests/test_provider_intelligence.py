import json

import pytest

from agf_orchestrator.capability_profiles import CapabilityStatus
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.provider_intelligence import (
    ARCHITECT_REQUIREMENTS,
    ProviderIntelligenceError,
    ProviderIntelligenceStore,
    build_state,
    make_profile,
    sign_state,
    state_from_dict,
)

PROJECT = "project-efc8e8ef7be7050b"
TARGET = "a" * 40
NOW = "2026-08-11T12:00:00Z"
EXPIRES = "2026-08-12T12:00:00Z"
GATES = SelectionGates(True, True, True, True, True, True)
GATE_EVIDENCE = (
    ("policy_eligible", "active-policy:merge-policy-adr-0003:" + "a" * 64),
    ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;True"),
    ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
    ("budget_eligible", "bounded-timeout-seconds:90;True"),
    ("health_eligible", "invocation-verified:True"),
    ("empirical_evidence_eligible", "deterministic-canary-sha256:" + "b" * 64),
)
TEST_KEY = b"test-owner-key-which-is-long-enough-123456"


def candidate(status=CapabilityStatus.SUPPORTED):
    profile = make_profile(
        project_id=PROJECT,
        provider_id="provider-codex",
        provenance_source="runtime-canary:codex:test-v1",
        observed_at=NOW,
        expires_at=EXPIRES,
        capability_results={name: status for name in ARCHITECT_REQUIREMENTS},
    )
    return CapabilityCandidate(profile, priority=0)


def state(**kwargs):
    return build_state(
        project_id=PROJECT,
        target_sha=TARGET,
        constitution_id="constitution-agf-v1",
        constitution_record_hash="c" * 64,
        observed_at=NOW,
        expires_at=EXPIRES,
        candidates=(candidate(),),
        provider_interfaces=(("provider-codex", "codex"),),
        gates=GATES,
        gate_evidence=GATE_EVIDENCE,
        policy_generation=2,
        **kwargs,
    )


def test_state_is_durable_and_restarts_with_verified_hash(tmp_path):
    value = sign_state(state(), TEST_KEY, staging=True)
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    store.save(value)
    recovered = store.load()
    recovered.validate(now=NOW, target_sha=TARGET)
    assert recovered.to_dict() == value.to_dict()
    store.save(value)


def test_tampered_profile_or_state_hash_fails_closed(tmp_path):
    value = state()
    payload = value.to_dict()
    payload["candidates"][0]["profile"]["provider_id"] = "provider-other"
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)
    payload = value.to_dict()
    payload["state_sha256"] = "0" * 64
    with pytest.raises(ProviderIntelligenceError, match="state hash"):
        state_from_dict(payload)


def test_unknown_required_capability_remains_ineligible():
    value = build_state(
        project_id=PROJECT,
        target_sha=TARGET,
        constitution_id="constitution-agf-v1",
        constitution_record_hash="c" * 64,
        observed_at=NOW,
        expires_at=EXPIRES,
        candidates=(candidate(CapabilityStatus.UNKNOWN),),
        provider_interfaces=(("provider-codex", "codex"),),
        gates=GATES,
        policy_generation=2,
        gate_evidence=GATE_EVIDENCE,
    )
    assert all(
        item.status is CapabilityStatus.UNKNOWN for item in value.candidates[0].profile.capabilities
    )


def test_target_binding_and_stale_state_are_rejected():
    value = state()
    with pytest.raises(ProviderIntelligenceError, match="target SHA"):
        value.validate(target_sha="b" * 40)
    with pytest.raises(ProviderIntelligenceError, match="stale"):
        value.validate(now=EXPIRES)


def test_state_schema_rejects_unknown_or_malformed_payload():
    payload = json.loads(json.dumps(state().to_dict()))
    payload["unexpected"] = True
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)
