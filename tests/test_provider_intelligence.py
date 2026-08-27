import json

import pytest

from agf_orchestrator.capability_profiles import CapabilityStatus
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.provider_intelligence import (
    ARCHITECT_GATE_NAMES,
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
EXPIRES = "2030-08-12T12:00:00Z"
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


def candidate(status=CapabilityStatus.SUPPORTED, provider_id="provider-codex", priority=0):
    profile = make_profile(
        project_id=PROJECT,
        provider_id=provider_id,
        provenance_source="runtime-canary:codex:test-v1",
        observed_at=NOW,
        expires_at=EXPIRES,
        capability_results={name: status for name in ARCHITECT_REQUIREMENTS},
    )
    return CapabilityCandidate(profile, priority=priority)


def state(**kwargs):
    defaults = dict(
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
    )
    defaults.update(kwargs)
    return build_state(**defaults)


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


def test_owner_state_is_separate_per_decision_domain(tmp_path):
    root = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True)
    architect = root.for_project(PROJECT, decision_domain="architect")
    documentation = root.for_project(PROJECT, decision_domain="documentation")
    architect_value = sign_state(state(), TEST_KEY, staging=True)
    documentation_value = sign_state(
        state(
            decision_domain="documentation",
            requirements=("documentation",),
            provider_interfaces=(("provider-codex", "documentation"),),
        ),
        TEST_KEY,
        staging=True,
    )
    architect.save(architect_value)
    documentation.save(documentation_value)
    assert architect.load().decision_domain == "architect"
    assert documentation.load().decision_domain == "documentation"
    assert architect.path != documentation.path


def test_unknown_decision_domain_is_rejected(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True)
    with pytest.raises(ProviderIntelligenceError):
        store.for_project(PROJECT, decision_domain="unknown-domain")


def test_non_architect_domains_use_common_gate_schema_without_architect_evidence():
    generic = tuple((name, f"owner-evidence:{name}:True") for name in ARCHITECT_GATE_NAMES)
    value = state(
        decision_domain="documentation",
        requirements=("documentation",),
        provider_interfaces=(("provider-codex", "documentation"),),
        gate_evidence=generic,
    )
    value.validate(now=NOW, target_sha=TARGET)


def test_architect_gate_evidence_remains_domain_specific():
    with pytest.raises(ProviderIntelligenceError):
        state(gate_evidence=GATE_EVIDENCE[:-1]).validate()


def test_architect_rejects_candidate_scoped_gate_evidence():
    scoped = (
        (
            "provider-codex",
            candidate().profile.profile_sha256,
            tuple((name, True) for name in ARCHITECT_GATE_NAMES),
        ),
    )
    with pytest.raises(ProviderIntelligenceError, match="candidate-scoped"):
        state(provider_gate_evidence_by_candidate=scoped).validate()


@pytest.mark.parametrize(
    "malformed",
    [[], ["provider-codex"], ["provider-codex", "0" * 64],
     ["provider-codex", "0" * 64, [] , "extra"]],
)
def test_malformed_scoped_gate_evidence_is_typed(malformed):
    payload = state().to_dict()
    payload["provider_gate_evidence_by_candidate"] = [malformed]
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)


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


@pytest.mark.parametrize("value", [True, False, None])
def test_nullable_selection_gates_are_structurally_preserved(value):
    gates = SelectionGates(value, True, True, True, True, True)
    value_state = state(
        decision_domain="documentation", requirements=("documentation",),
        gates=gates, gate_evidence=GATE_EVIDENCE,
    )
    value_state.validate(now=NOW, target_sha=TARGET)


@pytest.mark.parametrize("value", [1, 0, 1.0, 0.0, "true", [], {}, ()])
def test_non_boolean_selection_gates_are_rejected_at_state_boundary(value):
    gates = SelectionGates(value, True, True, True, True, True)
    value_state = state(
        decision_domain="documentation", requirements=("documentation",),
        gates=gates, gate_evidence=GATE_EVIDENCE,
    )
    with pytest.raises(ProviderIntelligenceError, match="selection gate types"):
        value_state.validate(now=NOW, target_sha=TARGET)


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


@pytest.mark.parametrize(
    "posture",
    [[[]], [["provider-codex"]], [["provider-codex", "payload", "extra"]],
     [123, "payload"], ["provider-codex", None], "not-a-list"],
)
def test_malformed_provider_security_posture_is_typed(posture):
    payload = state().to_dict()
    payload["provider_security_posture"] = posture
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)


def test_store_load_translates_malformed_security_posture(tmp_path):
    value = sign_state(state(), TEST_KEY, staging=True)
    payload = value.to_dict()
    payload["provider_security_posture"] = [["provider-codex"]]
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProviderIntelligenceError):
        store.load()


def _persisted_state(
    *, generation: int, profile_version: int, expired: bool, target=TARGET, observed=None
):
    observed = observed or ("2020-01-01T00:00:00Z" if expired else "2026-08-11T12:00:00Z")
    expires = "2020-01-02T00:00:00Z" if expired else "2030-01-01T00:00:00Z"
    profile = make_profile(
        project_id=PROJECT,
        provider_id="provider-codex",
        provenance_source="runtime-canary:codex:test-v1",
        observed_at=observed,
        expires_at=expires,
        capability_results={name: CapabilityStatus.SUPPORTED for name in ARCHITECT_REQUIREMENTS},
        profile_version=profile_version,
    )
    return build_state(
        project_id=PROJECT,
        target_sha=target,
        constitution_id="constitution-agf-v1",
        constitution_record_hash="c" * 64,
        observed_at=observed,
        expires_at=expires,
        candidates=(CapabilityCandidate(profile, priority=0),),
        provider_interfaces=(("provider-codex", "codex"),),
        gates=GATES,
        gate_evidence=GATE_EVIDENCE,
        policy_generation=generation,
    )


def test_owner_recovery_accepts_expired_signed_state_but_rejects_tampering(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    value = sign_state(
        _persisted_state(generation=2, profile_version=1, expired=True), TEST_KEY, staging=True
    )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(value.to_dict()))
    assert store._load_for_owner_recovery().policy_generation == 2
    payload = value.to_dict()
    payload["signature"] = "0" * 64
    store.path.write_text(json.dumps(payload))
    with pytest.raises(ProviderIntelligenceError, match="signature"):
        store._load_for_owner_recovery()


def test_owner_recovery_replaces_expired_state_only_with_higher_generation(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    old = sign_state(
        _persisted_state(generation=2, profile_version=1, expired=True), TEST_KEY, staging=True
    )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(old.to_dict()))
    new = sign_state(
        _persisted_state(generation=3, profile_version=2, expired=False), TEST_KEY, staging=True
    )
    store.save(new)
    assert store.load().policy_generation == 3


def test_owner_bootstrap_replaces_same_project_state_after_target_advance(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    old = sign_state(
        _persisted_state(generation=2, profile_version=1, expired=True), TEST_KEY, staging=True
    )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(old.to_dict()))
    advanced = sign_state(
        _persisted_state(generation=2, profile_version=2, expired=False, target="b" * 40),
        TEST_KEY,
        staging=True,
    )
    store.save(advanced)
    assert store._load_for_owner_recovery().target_sha == "b" * 40


def test_explicit_owner_renewal_replaces_fresh_same_target_evidence(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    old = sign_state(
        _persisted_state(generation=2, profile_version=1, expired=False), TEST_KEY, staging=True
    )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(old.to_dict()))
    renewed = sign_state(
        _persisted_state(
            generation=2,
            profile_version=2,
            expired=False,
            observed="2026-08-11T12:01:00Z",
        ),
        TEST_KEY,
        staging=True,
    )
    store._save_locked(renewed, allow_renewal=True)
    assert store._load_for_owner_recovery().state_sha256 == renewed.state_sha256


def test_same_target_renewal_requires_explicit_owner_authorization(tmp_path):
    store = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True).for_project(
        PROJECT
    )
    old = sign_state(
        _persisted_state(generation=2, profile_version=1, expired=False), TEST_KEY, staging=True
    )
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(old.to_dict()))
    renewed = sign_state(
        _persisted_state(generation=2, profile_version=2, expired=False), TEST_KEY, staging=True
    )
    with pytest.raises(ProviderIntelligenceError, match="different evidence"):
        store._save_locked(renewed)


def test_duplicate_provider_candidates_are_rejected():
    duplicate = candidate(priority=1)
    value = state(
        candidates=(candidate(), duplicate),
    )
    with pytest.raises(ProviderIntelligenceError, match="candidate bindings"):
        value.validate()
