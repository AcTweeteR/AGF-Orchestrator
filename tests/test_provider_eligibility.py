import json
from dataclasses import replace

import pytest
from provider_test_support import canonical_test_authority
from provider_test_support import sign_state as sign_owner_state

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
    _require_owner_eligible,
    canonical_knowledge_security_posture,
)
from agf_orchestrator.provider_intelligence import (
    ProviderIntelligenceStore,
    build_state,
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


def candidate(
    provider_id="knowledge-docs", *, docs=True, priority=0,
    capabilities=("documentation",),
):
    source = "owner provider registry fixture"
    capability_observations = tuple(
        CapabilityObservation(name, CapabilityStatus.SUPPORTED, "owner")
        for name in capabilities
    )
    if not docs:
        capability_observations = (
            CapabilityObservation("documentation", CapabilityStatus.UNKNOWN, None),
        )
    profile = CapabilityProfile(
        "1.0", f"profile-{provider_id}", PROJECT, provider_id, 1,
        source, sha256_text(source), NOW, EXPIRES, capability_observations, "",
    )
    return CapabilityCandidate(
        replace(profile, profile_sha256=capability_profile_hash(profile)), priority
    )


def state(
    provider_id="knowledge-docs", *, observed=NOW, expires=EXPIRES, gates=None,
    provider_gate_evidence=None, security_profile=None, candidates=None,
    provider_gate_evidence_by_candidate=(), requirements=("documentation",), **kwargs
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
    selected_candidates = candidates or (candidate(provider_id),)
    return build_state(
        project_id=PROJECT,
        target_sha=TARGET,
        constitution_id="constitution-agf-v1",
        constitution_record_hash="c" * 64,
        observed_at=observed,
        expires_at=expires,
        candidates=selected_candidates,
        provider_interfaces=tuple(
            (item.profile.provider_id, "knowledge") for item in selected_candidates
        ),
        gates=active_gates,
        gate_evidence=gate_evidence,
        policy_generation=2,
        signing_key_id="test-owner-ed25519",
        requirements=requirements,
        decision_domain="documentation",
        provider_gate_evidence=provider_gate_evidence or (
            ("network_eligible", True),
            ("authentication_eligible", True),
        ),
        provider_security_posture=tuple(
            (
                item.profile.provider_id,
                canonical_knowledge_security_posture(security_profile or knowledge_profile()),
            )
            for item in selected_candidates
        ),
        provider_gate_evidence_by_candidate=provider_gate_evidence_by_candidate,
        **kwargs,
    )


def make_authority(tmp_path, value=None):
    store = ProviderIntelligenceStore(tmp_path)
    signed = sign_owner_state(value or state())
    project_store = store.for_project(PROJECT, decision_domain="documentation")
    project_store.save(signed)
    return canonical_test_authority(store), project_store


def test_staging_hmac_store_cannot_become_trusted_authority(tmp_path):
    staging = ProviderIntelligenceStore(tmp_path, signing_key=KEY, staging=True)
    with pytest.raises(ProviderEligibilityError, match="owner-verifying"):
        ProviderEligibilityAuthority(staging)

    class FakeStore:
        owner_verifying = True

    with pytest.raises(ProviderEligibilityError, match="owner-verifying"):
        ProviderEligibilityAuthority(FakeStore())


def test_production_authority_is_pinned_to_configured_state_root(tmp_path, monkeypatch):
    canonical_root = tmp_path / "canonical"
    monkeypatch.setenv("AGF_STATE_DIR", str(canonical_root))
    canonical_store = ProviderIntelligenceStore(canonical_root)
    canonical_store.for_project(PROJECT, decision_domain="documentation").save(
        sign_owner_state(state())
    )
    authority = ProviderEligibilityAuthority(canonical_store)
    assert authority.resolve(
        project_id=PROJECT,
        provider_id="knowledge-docs",
        provider_kind="knowledge",
        capability_domain="documentation",
        now=NOW,
        target_sha=TARGET,
        required_capabilities=("documentation",),
        decision_domain="documentation",
    ).provider_id == "knowledge-docs"

    alternate = tmp_path / "copied-old-root"
    alternate_store = ProviderIntelligenceStore(alternate)
    alternate_store.for_project(PROJECT, decision_domain="documentation").save(
        sign_owner_state(state())
    )
    with pytest.raises(ProviderEligibilityError, match="canonical state root"):
        ProviderEligibilityAuthority(alternate_store)


def test_owner_verifying_authority_cannot_swap_its_store(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    with pytest.raises(AttributeError, match="immutable"):
        authority_value.store = ProviderIntelligenceStore(tmp_path / "replacement")


def test_authority_ignores_mutation_of_accepted_store_instance(tmp_path):
    authority_value, project_store = make_authority(tmp_path)
    accepted_store = ProviderIntelligenceStore(tmp_path)

    def forged_loader(*_args, **_kwargs):
        raise AssertionError("caller-controlled store loader was invoked")

    accepted_store.for_project = forged_loader
    accepted_store.load = forged_loader
    accepted_store.root = tmp_path / "attacker-controlled-root"

    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    assert decision.provider_id == "knowledge-docs"
    assert authority_value.store.root == tmp_path.resolve()
    assert project_store.path.exists()


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
    recovered = canonical_test_authority(ProviderIntelligenceStore(tmp_path)).verify(
        decision, now=NOW, target_sha=TARGET, required_capabilities=("documentation",)
    )
    assert recovered == decision


def test_decision_contains_explicit_snapshot_domain_and_candidate_identity(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    assert decision.source_decision_domain == "documentation"
    assert decision.candidate_profile_sha256 == state().candidates[0].profile.profile_sha256
    assert decision.candidate_priority == 0
    assert decision.source_state_sha256


def test_resolution_issues_fresh_decision_within_long_lived_state(tmp_path):
    state_value = state(
        observed="2026-08-25T00:00:00Z",
        expires="2026-08-27T00:00:00Z",
    )
    authority_value, _ = make_authority(tmp_path, state_value)
    issued_now = "2026-08-25T10:00:00Z"
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=issued_now, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    assert decision.source_observed_at == "2026-08-25T00:00:00Z"
    assert decision.decision_at == issued_now
    assert decision.expires_at == "2026-08-25T11:00:00Z"
    renewed = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now="2026-08-26T23:30:00Z",
        target_sha=TARGET, required_capabilities=("documentation",),
        decision_domain="documentation",
    )
    assert renewed.decision_at == "2026-08-26T23:30:00Z"
    assert renewed.expires_at == "2026-08-27T00:00:00Z"


def test_verify_does_not_rejuvenate_expired_decision(tmp_path):
    state_value = state(
        observed="2026-08-25T00:00:00Z",
        expires="2026-08-27T00:00:00Z",
    )
    authority_value, _ = make_authority(tmp_path, state_value)
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now="2026-08-26T10:00:00Z",
        target_sha=TARGET, required_capabilities=("documentation",),
        decision_domain="documentation",
    )
    with pytest.raises(ProviderEligibilityError, match="stale"):
        canonical_test_authority(ProviderIntelligenceStore(tmp_path)).verify(
            decision, now="2026-08-26T11:01:00Z", target_sha=TARGET,
            required_capabilities=("documentation",),
        )


def test_provider_scoped_authentication_cannot_be_shared_across_candidates(tmp_path):
    first = candidate("knowledge-a", priority=0)
    second = candidate("knowledge-b", priority=1)
    scoped = (
        (
            first.profile.provider_id, first.profile.profile_sha256,
            (
                ("policy_eligible", True), ("privacy_eligible", True),
                ("network_eligible", True), ("authentication_eligible", True),
                ("health_eligible", True), ("budget_eligible", True),
                ("empirical_evidence_eligible", True), ("independence_eligible", True),
            ),
        ),
        (
            second.profile.provider_id, second.profile.profile_sha256,
            (
                ("policy_eligible", True), ("privacy_eligible", True),
                ("network_eligible", True), ("authentication_eligible", False),
                ("health_eligible", True), ("budget_eligible", True),
                ("empirical_evidence_eligible", True), ("independence_eligible", True),
            ),
        ),
    )
    authority_value, _ = make_authority(
        tmp_path,
        state(candidates=(first, second), provider_gate_evidence_by_candidate=scoped),
    )
    first_decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-a", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    assert first_decision.authentication_eligible is True
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-b", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation",), decision_domain="documentation",
        )


def test_revisionless_selection_uses_documentation_domain_for_requirement_permutations(tmp_path):
    selected = candidate(capabilities=("documentation", "citations"))
    value = state(
        candidates=(selected,), requirements=("citations", "documentation"),
    )
    authority_value, _ = make_authority(tmp_path, value)
    for required in (("documentation", "citations"), ("citations", "documentation")):
        result = authority_value.select(
            (selected,), project_id=PROJECT, required_capabilities=required,
            provider_kind="knowledge", now=NOW, revision_scope="resolve-library",
        )
        assert result.provider_id == selected.profile.provider_id


def test_selection_loads_one_verified_snapshot_for_all_candidates(tmp_path, monkeypatch):
    store = ProviderIntelligenceStore(tmp_path)
    store.for_project(PROJECT, decision_domain="documentation").save(sign_owner_state(state()))
    original_load = ProviderIntelligenceStore.load
    load_count = 0

    def counted_load(store):
        nonlocal load_count
        load_count += 1
        return original_load(store)

    monkeypatch.setattr(ProviderIntelligenceStore, "load", counted_load)
    authority_value = canonical_test_authority(store)
    selected = authority_value.select(
        state().candidates, project_id=PROJECT,
        required_capabilities=("documentation",), provider_kind="knowledge",
        now=NOW, target_sha=TARGET,
    )
    assert selected.provider_id == "knowledge-docs"
    assert load_count == 1


def test_requirement_order_is_canonical_and_duplicates_fail_closed(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    ordered = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    same = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    assert same == ordered
    with pytest.raises(ProviderEligibilityError):
        authority_value.resolve(
            project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
            capability_domain="documentation", now=NOW, target_sha=TARGET,
            required_capabilities=("documentation", "documentation"),
            decision_domain="documentation",
        )


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


@pytest.mark.parametrize("field", ["network_eligible", "authentication_eligible"])
def test_explicit_optional_owner_denials_never_become_eligible(tmp_path, field):
    authority_value, _ = make_authority(tmp_path / field)
    decision = authority_value.resolve(
        project_id=PROJECT, provider_id="knowledge-docs", provider_kind="knowledge",
        capability_domain="documentation", now=NOW, target_sha=TARGET,
        required_capabilities=("documentation",), decision_domain="documentation",
    )
    denied = replace(decision, **{field: False})
    with pytest.raises(ProviderEligibilityError):
        _require_owner_eligible(denied)


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


def test_generic_resolution_enforces_owner_security_posture(tmp_path):
    denied_network = make_authority(
        tmp_path / "network",
        state(provider_gate_evidence=(
            ("network_eligible", False),
            ("authentication_eligible", True),
        )),
    )[0]
    with pytest.raises(ProviderEligibilityError, match="network"):
        denied_network.resolve(
            project_id=PROJECT,
            provider_id="knowledge-docs",
            provider_kind="knowledge",
            capability_domain="documentation",
            now=NOW,
            target_sha=TARGET,
            required_capabilities=("documentation",),
            decision_domain="documentation",
        )

    denied_auth = make_authority(
        tmp_path / "auth",
        state(
            provider_gate_evidence=(
                ("network_eligible", True),
                ("authentication_eligible", False),
            ),
            security_profile=knowledge_profile(credentials=True),
        ),
    )[0]
    with pytest.raises(ProviderEligibilityError, match="authentication"):
        denied_auth.resolve(
            project_id=PROJECT,
            provider_id="knowledge-docs",
            provider_kind="knowledge",
            capability_domain="documentation",
            now=NOW,
            target_sha=TARGET,
            required_capabilities=("documentation",),
            decision_domain="documentation",
        )


def test_revisionless_selection_uses_documentation_domain(tmp_path):
    authority_value, _ = make_authority(tmp_path)
    selected = authority_value.select(
        (),
        project_id=PROJECT,
        required_capabilities=("documentation",),
        provider_kind="knowledge",
        now=NOW,
        revision_scope="resolve-library",
    )
    assert selected.provider_id == "knowledge-docs"
    assert selected.fallback_used is False


def test_selection_audit_retains_rejected_owner_candidates(tmp_path):
    primary = candidate("knowledge-primary", docs=False, priority=0)
    secondary = candidate("knowledge-secondary", priority=1)
    scoped = tuple(
        (
            item.profile.provider_id,
            item.profile.profile_sha256,
            (
                ("policy_eligible", True), ("privacy_eligible", True),
                ("network_eligible", True), ("authentication_eligible", True),
                ("health_eligible", True), ("budget_eligible", True),
                ("empirical_evidence_eligible", True), ("independence_eligible", True),
            ),
        )
        for item in (primary, secondary)
    )
    authority_value, _ = make_authority(
        tmp_path, state(candidates=(primary, secondary), provider_gate_evidence_by_candidate=scoped)
    )
    selected = authority_value.select(
        (), project_id=PROJECT, required_capabilities=("documentation",),
        provider_kind="knowledge", now=NOW, revision_scope="revision-bound",
        target_sha=TARGET,
    )
    assert selected.provider_id == "knowledge-secondary"
    assert selected.fallback_used is True
    assert selected.considered_candidates == (
        "knowledge-primary", "knowledge-secondary",
    )
    assert selected.rejected_reasons == (
        "knowledge-primary: UNSUPPORTED_CAPABILITY",
    )


def test_credentials_require_owner_authentication_eligibility(tmp_path):
    authority_value, _ = make_authority(
        tmp_path, state(security_profile=knowledge_profile(credentials=True))
    )
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


def test_caller_cannot_downgrade_owner_security_posture(tmp_path):
    owner_profile = knowledge_profile(network_required=True, auth_required=True, credentials=True)
    authority_value, _ = make_authority(
        tmp_path, state(security_profile=owner_profile)
    )
    downgraded = knowledge_profile(network_required=False, auth_required=False, credentials=False)
    with pytest.raises(ProviderEligibilityError, match="security posture"):
        authority_value.resolve_knowledge_profile(
            downgraded, now=NOW, target_sha=TARGET, required_capability="documentation"
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
    expired_store = ProviderIntelligenceStore(tmp_path / "expired")
    expired_project_store = expired_store.for_project(PROJECT, decision_domain="documentation")
    expired_project_store.path.parent.mkdir(parents=True)
    expired_project_store.path.write_text(
        json.dumps(sign_owner_state(expired).to_dict())
    )
    expired_authority = canonical_test_authority(expired_store)
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
