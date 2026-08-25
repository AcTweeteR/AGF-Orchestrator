import json
from dataclasses import replace

import pytest

from agf_orchestrator.capability_extensions import (
    IntegrationStability,
    KnowledgeMutability,
    KnowledgeProviderProfile,
    KnowledgeTransport,
    PrivacyClassification,
)
from agf_orchestrator.capability_extensions import (
    seal as seal_knowledge_profile,
)
from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.provider_eligibility import (
    ProviderEligibilityAuthority,
    ProviderEligibilityError,
)
from agf_orchestrator.provider_intelligence import (
    ProviderIntelligenceStore,
    build_state,
    sign_state,
)

PROJECT = "project-efc8e8ef7be7050b"
TARGET = "a" * 40
NOW = "2026-08-11T12:00:00Z"
EXPIRES = "2030-08-12T12:00:00Z"
KEY = b"test-owner-key-which-is-long-enough-123456"
GATE_EVIDENCE = (
    ("policy_eligible", "active-policy:merge-policy-adr-0003:" + "a" * 64),
    ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;True"),
    ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
    ("budget_eligible", "bounded-timeout-seconds:90;True"),
    ("health_eligible", "invocation-verified:True"),
    ("empirical_evidence_eligible", "deterministic-canary-sha256:" + "b" * 64),
)


def candidate(provider_id="knowledge-docs", *, docs=True, priority=0):
    source = "owner provider registry fixture"
    capabilities = (CapabilityObservation("documentation", CapabilityStatus.SUPPORTED, "owner"),)
    if not docs:
        capabilities = (CapabilityObservation("documentation", CapabilityStatus.UNKNOWN, None),)
    profile = CapabilityProfile(
        "1.0", f"profile-{provider_id}", PROJECT, provider_id, 1,
        source, sha256_text(source), NOW, EXPIRES, capabilities, "",
    )
    return CapabilityCandidate(
        replace(profile, profile_sha256=capability_profile_hash(profile)), priority
    )


def state(
    provider_id="knowledge-docs", *, observed=NOW, expires=EXPIRES, gates=None,
    provider_gate_evidence=None, **kwargs
):
    active_gates = gates or SelectionGates(True, True, True, True, True, True)
    gate_evidence = (
        GATE_EVIDENCE[0],
        (
            "privacy_eligible",
            f"codex-safe-environment-v1;read-only-canary;{active_gates.privacy_eligible}",
        ),
        (
            "independence_eligible",
            f"architect-advisory;reviewer-separate-stage;{active_gates.independence_eligible}",
        ),
        ("budget_eligible", f"bounded-timeout-seconds:90;{active_gates.budget_eligible}"),
        ("health_eligible", f"invocation-verified:{active_gates.health_eligible}"),
        GATE_EVIDENCE[5],
    )
    return build_state(
        project_id=PROJECT,
        target_sha=TARGET,
        constitution_id="constitution-agf-v1",
        constitution_record_hash="c" * 64,
        observed_at=observed,
        expires_at=expires,
        candidates=(candidate(provider_id),),
        provider_interfaces=((provider_id, "knowledge"),),
        gates=active_gates,
        gate_evidence=gate_evidence,
        policy_generation=2,
        requirements=("documentation",),
        decision_domain="documentation",
        provider_gate_evidence=provider_gate_evidence or (
            ("network_eligible", True),
            ("authentication_eligible", True),
        ),
        **kwargs,
    )


def make_authority(tmp_path, value=None):
    store = ProviderIntelligenceStore(tmp_path, signing_key=KEY, staging=True)
    signed = sign_state(value or state(), KEY, staging=True)
    project_store = store.for_project(PROJECT, decision_domain="documentation")
    project_store.save(signed)
    return ProviderEligibilityAuthority(store), project_store


def knowledge_profile(
    *, project_id=PROJECT, network_required=True, auth_required=True, credentials=False
):
    return seal_knowledge_profile(
        KnowledgeProviderProfile(
            "1.0", "knowledge-docs", project_id, 1, KnowledgeTransport.STDIO,
            ("documentation",), credentials, auth_required, network_required, False,
            PrivacyClassification.EXTERNAL_PRIVATE, True, KnowledgeMutability.READ_ONLY,
            IntegrationStability.OFFICIAL, "owner profile fixture", NOW, EXPIRES, "",
        )
    )


def test_owner_eligible_provider_resolves_and_survives_restart(tmp_path):
    authority_value, project_store = make_authority(tmp_path)
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",),
        decision_domain="documentation",
    )
    assert decision.policy_eligible and decision.network_eligible is True
    recovered = ProviderEligibilityAuthority(project_store).verify(
        decision, now=NOW, target_sha=TARGET, required_capabilities=("documentation",)
    )
    assert recovered == decision


def test_target_revision_is_required_and_exactly_bound(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha="b" * 40,
            required_capabilities=("documentation",), decision_domain="documentation",
        )
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW,
            required_capabilities=("documentation",), decision_domain="documentation",
        )


def test_fabricated_decision_self_hash_is_not_authority(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
            decision_domain="documentation",
    )
    forged = replace(decision, policy_eligible=False, decision_sha256="0" * 64)
    forged = replace(forged, decision_sha256=__import__("hashlib").sha256(
        json.dumps(forged._unsigned(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest())
    with pytest.raises(ProviderEligibilityError):
        authority_value.verify(
            forged, now=NOW, target_sha=TARGET, required_capabilities=("documentation",)
        )


def test_policy_privacy_health_budget_and_empirical_denials_fail_closed(tmp_path):
    for field in (
        "policy_eligible", "privacy_eligible", "health_eligible",
        "budget_eligible", "empirical_evidence_eligible",
    ):
        gates = SelectionGates(True, True, True, True, True, True)
        gates = replace(gates, **{field: False})
        authority_value, _ = make_authority(tmp_path / field, state(gates=gates))
        with pytest.raises(ProviderEligibilityError):
            authority_value.resolve(
                project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
                capability_domain="documentation", now=NOW,
                target_sha=TARGET, required_capabilities=("documentation",),
            )


def test_knowledge_profile_requires_owner_network_and_authentication(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    assert authority_value.resolve_knowledge_profile(
        knowledge_profile(), now=NOW, target_sha=TARGET, required_capability="documentation"
    ).provider_id == "knowledge-docs"
    no_network = make_authority(tmp_path / "no-network", state(
        provider_gate_evidence=(("authentication_eligible", True),)
    ))[0]
    with pytest.raises(ProviderEligibilityError):
        no_network.resolve_knowledge_profile(
            knowledge_profile(), now=NOW, target_sha=TARGET, required_capability="documentation"
        )


def test_credentials_require_owner_authentication_eligibility(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    assert authority_value.resolve_knowledge_profile(
        knowledge_profile(credentials=True), now=NOW, target_sha=TARGET,
        required_capability="documentation",
    ).provider_id == "knowledge-docs"
    no_auth = make_authority(
        tmp_path / "no-auth",
        state(provider_gate_evidence=(("network_eligible", True),)),
    )[0]
    with pytest.raises(ProviderEligibilityError):
        no_auth.resolve_knowledge_profile(
            knowledge_profile(credentials=True), now=NOW, target_sha=TARGET,
            required_capability="documentation",
        )
    denied = make_authority(
        tmp_path / "denied-auth",
        state(provider_gate_evidence=(
            ("network_eligible", True), ("authentication_eligible", False)
        )),
    )[0]
    with pytest.raises(ProviderEligibilityError):
        denied.resolve_knowledge_profile(
            knowledge_profile(credentials=True), now=NOW, target_sha=TARGET,
            required_capability="documentation",
        )


def test_cross_project_provider_and_expired_state_fail_closed(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-other", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
        )
    expired = state(observed="2020-01-01T00:00:00Z", expires="2020-01-02T00:00:00Z")
    expired_store = ProviderIntelligenceStore(tmp_path / "expired", signing_key=KEY, staging=True)
    expired_project_store = expired_store.for_project(PROJECT, decision_domain="documentation")
    expired_project_store.path.parent.mkdir(parents=True)
    expired_project_store.path.write_text(
        json.dumps(sign_state(expired, KEY, staging=True).to_dict())
    )
    expired_authority = ProviderEligibilityAuthority(expired_store)
    with pytest.raises(ProviderEligibilityError):
        expired_authority.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
        )


def test_provider_kind_cannot_cross_replay_owner_domain(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="code-intelligence",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
        )


def test_requirements_are_owner_scoped_and_duplicates_fail_closed(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="code-intelligence", now=NOW,
            target_sha=TARGET, required_capabilities=("code-intelligence",),
            decision_domain="documentation",
        )
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW,
            target_sha=TARGET, required_capabilities=("documentation", "documentation"),
            decision_domain="documentation",
        )


def test_future_state_and_tampered_persisted_state_fail_closed(tmp_path):
    future = state(observed="2099-01-01T00:00:00Z", expires="2099-01-02T00:00:00Z")
    future_authority, _ = make_authority(tmp_path / "future", future)
    with pytest.raises(ProviderEligibilityError):
        future_authority.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
        )
    tampered_authority, project_store = make_authority(tmp_path / "tampered")
    payload = json.loads(project_store.path.read_text())
    payload["candidates"][0]["profile"]["provider_id"] = "knowledge-other"
    project_store.path.write_text(json.dumps(payload))
    with pytest.raises(ProviderEligibilityError):
        tampered_authority.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",),
        )
