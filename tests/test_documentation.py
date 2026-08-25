import hashlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from provider_test_support import sign_state as sign_owner_state

from agf_orchestrator.capability_extensions import (
    IntegrationStability,
    KnowledgeMutability,
    KnowledgeProviderProfile,
    KnowledgeTransport,
    PrivacyClassification,
)
from agf_orchestrator.capability_extensions import seal as seal_profile
from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.documentation import (
    DependencyVersionEvidence,
    DocumentationCitation,
    DocumentationError,
    DocumentationEvidence,
    DocumentationFreshness,
    DocumentationOperation,
    DocumentationRequest,
    DocumentationStatus,
    ProviderBinding,
    citation_sha256,
    evidence_from_dict,
    latest_is_unsafe_for_project,
    load_evidence,
    load_provider_binding,
    persist_evidence,
    reconcile_evidence,
    seal,
    seal_claim,
)
from agf_orchestrator.documentation import (
    resolve_provider as _resolve_provider,
)
from agf_orchestrator.provider_eligibility import (
    ProviderEligibilityAuthority,
    canonical_knowledge_security_posture,
)
from agf_orchestrator.provider_intelligence import (
    ProviderIntelligenceStore,
    build_state,
)
from agf_orchestrator.session_store import SessionStore

PROJECT = "project-efc8e8ef7be7050b"
REPOSITORY = "github.com/example/repository"
REVISION = "a" * 40
OTHER_REVISION = "b" * 40
NOW = "2026-08-24T12:00:00Z"


def dependency(**changes):
    value = DependencyVersionEvidence(
        "pypi", "requests", "==1.8.3", "1.8.3", "1.8.3", None,
        "poetry.lock", NOW,
    )
    return replace(value, **changes)


def request(**changes):
    value = DocumentationRequest(
        PROJECT, REPOSITORY, REVISION, DocumentationOperation.RETRIEVE_TOPIC,
        dependency(), "timeouts", 3600, DEFAULT_BINDING,
    )
    return replace(value, **changes)


def evidence(**changes):
    citation = DocumentationCitation(
        "https://docs.example/requests/1.8.3", "timeouts", "timeout parameter"
    )
    value = DocumentationEvidence(
        "1.0", "docs-evidence-1", "knowledge-docs", DEFAULT_BINDING.binding_sha256,
        PROJECT, REPOSITORY,
        REVISION, DocumentationOperation.RETRIEVE_TOPIC, dependency(), "timeouts",
        "timeouts", "1.8.3", "fixture-docs",
        (citation,),
        (
            seal_claim(
                "requests.timeouts.timeout_type", "float-or-none",
                citation_sha256s=(citation_sha256(citation),),
            ),
        ),
        NOW, DocumentationFreshness.FRESH, DocumentationStatus.VALID, "",
    )
    return seal(replace(value, **changes))


def profile(
    *, network_required=False, privacy_review_required=False,
    capabilities=("documentation",),
    provider_id="knowledge-docs",
    expires_at="2026-08-25T12:00:00Z",
):
    return seal_profile(
        KnowledgeProviderProfile(
            "1.0", provider_id, PROJECT, 1, KnowledgeTransport.STDIO,
            capabilities, False, False, network_required, False,
            (
                PrivacyClassification.EXTERNAL_PUBLIC
                if network_required
                else PrivacyClassification.LOCAL_ONLY
            ),
            privacy_review_required, KnowledgeMutability.READ_ONLY,
            IntegrationStability.OFFICIAL, "fixture documentation profile", NOW,
            expires_at, "",
        )
    )


_DOCUMENTATION_STATE_ROOT = Path(
    tempfile.mkdtemp(prefix="agf-doc-authority-global-")
).resolve()
os.environ["AGF_STATE_DIR"] = str(_DOCUMENTATION_STATE_ROOT)
_DOCUMENTATION_PROVIDER_IDS = (
    "knowledge-docs", "knowledge-provider-a", "knowledge-provider-b",
    "knowledge-provider-database-directory", "knowledge-provider-database-symlink",
    "knowledge-provider-durable", "knowledge-provider-foreign-owner",
    "knowledge-provider-issued", "knowledge-provider-open-database",
    "knowledge-provider-permissions", "knowledge-tampered",
)


def _documentation_authority(profile_value, kwargs):
    source = "owner documentation provider registry fixture"
    capability_profile = CapabilityProfile(
        "1.0", f"profile-{profile_value.knowledge_provider_id}", PROJECT,
        profile_value.knowledge_provider_id, 1, source, sha256_text(source), NOW,
        "2030-08-25T12:00:00Z",
        (CapabilityObservation("documentation", CapabilityStatus.SUPPORTED, "owner"),), "",
    )
    candidate_profiles = []
    for provider_id in _DOCUMENTATION_PROVIDER_IDS:
        candidate_profile = replace(
            capability_profile,
            provider_id=provider_id,
            profile_id=f"profile-{provider_id}",
            profile_sha256="",
        )
        candidate_profiles.append(
            replace(candidate_profile, profile_sha256=capability_profile_hash(candidate_profile))
        )
    policy = kwargs.get("policy_authorized", True)
    privacy = kwargs.get("privacy_eligible", True)
    available = kwargs.get("available", True)
    authenticated = kwargs.get("authenticated", True)
    network = kwargs.get("network_allowed", True)
    gates = SelectionGates(policy, privacy, True, True, available, True)
    gate_evidence = (
        ("policy_eligible", "active-policy:merge-policy-adr-0003:" + "a" * 64),
        ("privacy_eligible", f"codex-safe-environment-v1;read-only-canary;{privacy}"),
        ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
        ("budget_eligible", "bounded-timeout-seconds:90;True"),
        ("health_eligible", f"invocation-verified:{available}"),
        ("empirical_evidence_eligible", "deterministic-canary-sha256:" + "b" * 64),
    )
    state = build_state(
        project_id=PROJECT, target_sha=REVISION,
        constitution_id="constitution-agf-v1", constitution_record_hash="c" * 64,
        observed_at=kwargs.get("now", NOW), expires_at="2030-08-25T12:00:00Z",
        candidates=tuple(CapabilityCandidate(item, priority=0) for item in candidate_profiles),
        provider_interfaces=tuple(
            (item.provider_id, "documentation") for item in candidate_profiles
        ),
        gates=gates, gate_evidence=gate_evidence, policy_generation=2,
        signing_key_id="test-owner-ed25519",
        requirements=("documentation",), decision_domain="documentation",
        provider_gate_evidence=(
            ("network_eligible", network),
            ("authentication_eligible", authenticated),
        ),
        provider_gate_evidence_by_candidate=tuple(
            (
                item.provider_id,
                item.profile_sha256,
                (
                    ("policy_eligible", policy),
                    ("privacy_eligible", privacy),
                    ("network_eligible", network),
                    ("authentication_eligible", authenticated),
                    ("health_eligible", available),
                    ("budget_eligible", True),
                    ("empirical_evidence_eligible", True),
                    ("independence_eligible", True),
                ),
            )
            for item in candidate_profiles
        ),
        provider_security_posture=tuple(
            (item.provider_id, canonical_knowledge_security_posture(profile_value))
            for item in candidate_profiles
        ),
    )
    has_caller_denial = any(
        kwargs.get(name) is False
        for name in (
            "available", "authenticated", "policy_authorized",
            "privacy_eligible", "network_allowed",
        )
    )
    generated_root = str(Path(tempfile.mkdtemp(prefix="agf-doc-authority-")).resolve())
    root = kwargs.get("state_root") or (
        generated_root
        if (
            has_caller_denial
            or kwargs.get("now", NOW) != NOW
            or profile_value.capabilities != ("documentation",)
        )
        else str(_DOCUMENTATION_STATE_ROOT)
    )
    store = ProviderIntelligenceStore(root)
    store.for_project(PROJECT, decision_domain="documentation").save(
        sign_owner_state(state)
    )
    return ProviderEligibilityAuthority(store)


def resolve_provider(profile_value, **kwargs):
    if kwargs.get("revision_scope", "revision-bound") == "revision-bound":
        kwargs.setdefault("target_sha", REVISION)
    return _resolve_provider(
        profile_value,
        eligibility_authority=_documentation_authority(profile_value, kwargs),
        **kwargs,
    )


def test_documentation_rejects_duck_typed_authority():
    class ForgedAuthority:
        def resolve_knowledge_profile(self, *_args, **_kwargs):
            raise AssertionError("forged authority was invoked")

    with pytest.raises(DocumentationError, match="canonical provider eligibility"):
        _resolve_provider(
            profile(), project_id=PROJECT, now=NOW, available=True, authenticated=True,
            policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
            eligibility_authority=ForgedAuthority(), target_sha=REVISION,
        )


DEFAULT_BINDING = resolve_provider(
    profile(), project_id=PROJECT, now=NOW, available=True, authenticated=True,
    policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
).binding
assert isinstance(DEFAULT_BINDING, ProviderBinding)


def test_revisionless_library_resolution_has_explicit_non_replayable_scope():
    result = resolve_provider(
        profile(), project_id=PROJECT, now=NOW, available=True, authenticated=True,
        policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
        revision_scope="resolve-library",
    )
    assert result.binding is not None
    library_binding = result.binding
    library_request = request(
        operation=DocumentationOperation.RESOLVE_LIBRARY,
        repository_id=None,
        revision_sha=None,
        provider_binding=library_binding,
    )
    library_evidence = evidence(
        operation=DocumentationOperation.RESOLVE_LIBRARY,
        repository_id=None,
        revision_sha=None,
        provider_binding_sha256=library_binding.binding_sha256,
    )
    assert library_evidence.assess(library_request, now=NOW) is DocumentationStatus.VALID
    retrieval_request = request(provider_binding=library_binding)
    retrieval_evidence = evidence(
        provider_binding_sha256=library_binding.binding_sha256,
    )
    assert retrieval_evidence.assess(
        retrieval_request, now=NOW
    ) is DocumentationStatus.PROVIDER_INELIGIBLE
    tampered = replace(library_binding, revision_scope="revision-bound")
    assert library_evidence.assess(
        library_request, now=NOW, provider_binding=tampered
    ) is DocumentationStatus.PROVIDER_INELIGIBLE


def test_exact_version_is_valid_and_latest_major_is_rejected():
    item = evidence()
    assert item.assess(request(), now=NOW) is DocumentationStatus.VALID
    latest = evidence(documentation_version="2.1.0")
    assert latest.assess(request(), now=NOW) is DocumentationStatus.VERSION_MISMATCH
    assert latest_is_unsafe_for_project(latest, request(), now=NOW)


def test_version_sources_and_ranges_fail_closed():
    mismatch = request(dependency=dependency(resolved_version="1.9.0"))
    assert (
        evidence(dependency=mismatch.dependency).assess(mismatch, now=NOW)
        is DocumentationStatus.CONTRADICTORY
    )
    ambiguous = request(dependency=dependency(locked_version=None, resolved_version=None))
    assert (
        evidence(dependency=ambiguous.dependency).assess(ambiguous, now=NOW)
        is DocumentationStatus.AMBIGUOUS_VERSION
    )
    minor = evidence(documentation_version="1.9.0")
    assert minor.assess(request(), now=NOW) is DocumentationStatus.VERSION_MISMATCH
    declared_mismatch = request(
        dependency=dependency(locked_version=None, resolved_version="1.9.0")
    )
    assert (
        evidence(dependency=declared_mismatch.dependency).assess(declared_mismatch, now=NOW)
        is DocumentationStatus.CONTRADICTORY
    )
    ranged = request(
        dependency=dependency(
            declared_constraint=">=1.8,<2.0", locked_version=None, resolved_version="1.9.0"
        )
    )
    assert evidence(
        dependency=ranged.dependency, documentation_version="1.9.0"
    ).assess(ranged, now=NOW) is DocumentationStatus.VALID


def test_project_repository_revision_topic_and_stale_bindings():
    item = evidence()
    assert (
        item.assess(request(project_id="project-other"), now=NOW)
        is DocumentationStatus.PROJECT_MISMATCH
    )
    assert (
        item.assess(request(repository_id="github.com/other/repository"), now=NOW)
        is DocumentationStatus.REPOSITORY_MISMATCH
    )
    assert (
        item.assess(request(revision_sha=OTHER_REVISION), now=NOW)
        is DocumentationStatus.REVISION_MISMATCH
    )
    assert item.assess(request(topic="retries"), now=NOW) is DocumentationStatus.TOPIC_MISMATCH
    stale = evidence(
        freshness=DocumentationFreshness.STALE, status=DocumentationStatus.STALE
    )
    assert stale.assess(request(), now=NOW) is DocumentationStatus.STALE
    old_dependency = dependency(observed_at="2026-08-24T00:00:00Z")
    old = evidence(
        observed_at="2026-08-24T00:00:00Z", dependency=old_dependency
    )
    assert (
        old.assess(request(dependency=old_dependency, max_age_seconds=60), now=NOW)
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )


def test_future_dated_evidence_never_becomes_fresh():
    immediate = evidence(observed_at="2026-08-24T12:00:01Z")
    assert immediate.assess(request(), now=NOW) is DocumentationStatus.FUTURE_DATED
    far_future = evidence(observed_at="2099-01-01T00:00:00Z")
    assert (
        far_future.assess(request(max_age_seconds=0), now=NOW)
        is DocumentationStatus.FUTURE_DATED
    )
    exact = evidence()
    assert exact.assess(request(max_age_seconds=0), now=NOW) is DocumentationStatus.VALID


def test_dependency_evidence_has_the_same_freshness_bound():
    old_dependency = request(
        dependency=dependency(observed_at="2026-08-24T00:00:00Z")
    )
    item = evidence(dependency=old_dependency.dependency)
    assert item.assess(old_dependency, now=NOW) is DocumentationStatus.STALE


def test_dependency_observation_cannot_follow_documentation_retrieval():
    newer_dependency = dependency(observed_at="2026-08-24T12:00:01Z")
    with pytest.raises(DocumentationError, match="dependency observation"):
        evidence(dependency=newer_dependency)


def test_dependency_and_documentation_equal_or_ordered_timestamps_are_valid():
    for observed_at in ("2026-08-24T12:00:00Z", "2026-08-24T11:59:59Z"):
        dep = dependency(observed_at=observed_at)
        assert evidence(dependency=dep).assess(
            request(dependency=dep), now=NOW
        ) is DocumentationStatus.VALID


def test_conflicting_sources_and_provider_responses_fail_closed():
    first = evidence()
    second = evidence(documentation_version="1.8.4", evidence_id="docs-evidence-2")
    assert (
        reconcile_evidence((first, second), request(), now=NOW)
        is DocumentationStatus.CONTRADICTORY
    )
    assert reconcile_evidence((), request(), now=NOW) is DocumentationStatus.UNAVAILABLE
    same_claim = evidence(
        evidence_id="docs-evidence-2", documentation_source="other-source"
    )
    assert reconcile_evidence((first, same_claim), request(), now=NOW) is DocumentationStatus.VALID
    reordered = evidence(
        evidence_id="docs-evidence-2", claims=tuple(reversed(first.claims))
    )
    assert reconcile_evidence((first, reordered), request(), now=NOW) is DocumentationStatus.VALID
    opposing = evidence(
        evidence_id="docs-evidence-2",
        claims=(seal_claim(
            "requests.timeouts.timeout_type", "integer-only",
            citation_sha256s=(citation_sha256(first.citations[0]),),
        ),),
    )
    assert (
        reconcile_evidence((first, opposing), request(), now=NOW)
        is DocumentationStatus.CONTRADICTORY
    )
    with pytest.raises(DocumentationError):
        evidence(evidence_id="docs-evidence-2", claims=())
    stale = evidence(
        evidence_id="docs-evidence-2", status=DocumentationStatus.STALE,
        freshness=DocumentationFreshness.STALE,
    )
    assert (
        reconcile_evidence((first, stale), request(), now=NOW)
        is DocumentationStatus.CONTRADICTORY
    )
    malformed = evidence().to_dict()
    malformed["documentation_version"] = "latest"
    with pytest.raises(DocumentationError):
        evidence_from_dict(malformed)
    unknown_claim_field = evidence().to_dict()
    unknown_claim_field["claims"][0]["unexpected"] = "tampered"
    with pytest.raises(DocumentationError):
        evidence_from_dict(unknown_claim_field)
    tampered_claim = evidence().to_dict()
    tampered_claim["claims"][0]["assertion_value"] = "tampered"
    with pytest.raises(DocumentationError):
        evidence_from_dict(tampered_claim)
    unavailable = evidence(
        status=DocumentationStatus.UNAVAILABLE, documentation_version=None
    )
    assert unavailable.assess(request(), now=NOW) is DocumentationStatus.UNAVAILABLE
    malformed = evidence().to_dict()
    malformed["citations"] = ["not-a-citation"]
    with pytest.raises(DocumentationError):
        evidence_from_dict(malformed)


def test_bounds_secret_safety_hash_and_authority_boundary():
    with pytest.raises(DocumentationError):
        evidence(citations=(DocumentationCitation("source", "topic", "x" * 2401),))
    with pytest.raises(DocumentationError):
        evidence(citations=(DocumentationCitation("source", "topic", "api_key: leaked"),))
    with pytest.raises(DocumentationError):
        evidence(citations=(DocumentationCitation("source", "topic", "api key: leaked"),))
    with pytest.raises(DocumentationError):
        evidence(
            citations=tuple(
                DocumentationCitation("source", str(index), "x" * 1000)
                for index in range(17)
            )
        )
    assert evidence().evidence_sha256 == seal(evidence()).evidence_sha256
    assert evidence().status is DocumentationStatus.VALID
    upgrade = evidence(documentation_version="2.0.0")
    assert upgrade.assess(request(), now=NOW) is DocumentationStatus.VERSION_MISMATCH


def test_provider_required_optional_network_privacy_and_capability_gates():
    kwargs = dict(
        project_id=PROJECT, now=NOW, available=True, authenticated=True,
        policy_authorized=True, privacy_eligible=True, network_allowed=True,
    )
    eligible = resolve_provider(profile(), required=True, **kwargs)
    assert eligible.status is DocumentationStatus.VALID
    assert eligible.binding is not None
    assert resolve_provider(profile(), required=False, **kwargs).status is DocumentationStatus.VALID
    assert (
        resolve_provider(profile(), required=True, **{**kwargs, "available": False}).status
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    assert (
        resolve_provider(
            profile(network_required=True),
            required=True,
            **{**kwargs, "network_allowed": False},
        ).status
        is DocumentationStatus.NETWORK_BLOCKED
    )
    assert (
        resolve_provider(
            profile(privacy_review_required=True),
            required=True,
            **{**kwargs, "privacy_eligible": False},
        ).status
        is DocumentationStatus.PRIVACY_BLOCKED
    )
    assert (
        resolve_provider(profile(capabilities=("citations",)), required=True, **kwargs).status
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )


def test_runtime_denials_restrict_owner_authorization_without_granting_it(tmp_path):
    owner_authority = _documentation_authority(profile(), {"state_root": str(tmp_path / "offline")})
    base = dict(
        project_id=PROJECT, now=NOW, available=True, authenticated=True,
        policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
        eligibility_authority=owner_authority, target_sha=REVISION,
    )
    for flag, expected in (
        ("available", DocumentationStatus.PROVIDER_INELIGIBLE),
        ("policy_authorized", DocumentationStatus.PROVIDER_INELIGIBLE),
    ):
        constrained = {**base, flag: False}
        assert _resolve_provider(profile(), **constrained).status is expected

    network_authority = _documentation_authority(
        profile(network_required=True), {"state_root": str(tmp_path / "network")}
    )
    network_result = _resolve_provider(
        profile(network_required=True), **{**base, "eligibility_authority": network_authority,
                                          "network_allowed": False}
    )
    assert network_result.status is DocumentationStatus.NETWORK_BLOCKED

    privacy_authority = _documentation_authority(
        profile(privacy_review_required=True), {"state_root": str(tmp_path / "privacy")}
    )
    privacy_result = _resolve_provider(
        profile(privacy_review_required=True),
        **{**base, "eligibility_authority": privacy_authority, "privacy_eligible": False},
    )
    assert privacy_result.status is DocumentationStatus.PRIVACY_BLOCKED

    offline_result = _resolve_provider(
        profile(), **{**base, "network_allowed": False}
    )
    assert offline_result.status is DocumentationStatus.VALID


def test_runtime_authentication_denial_applies_only_to_credentialed_profiles(tmp_path):
    base = dict(
        project_id=PROJECT, now=NOW, available=True, authenticated=False,
        policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
    )
    offline = resolve_provider(profile(), **base)
    assert offline.status is DocumentationStatus.VALID

    credentialed = profile()
    credentialed = replace(credentialed, requires_credentials=True)
    credentialed = seal_profile(credentialed)
    authority = _documentation_authority(
        credentialed, {"state_root": str(tmp_path / "credentialed")}
    )
    result = _resolve_provider(
        credentialed, **{**base, "eligibility_authority": authority, "target_sha": REVISION}
    )
    assert result.status is DocumentationStatus.PROVIDER_INELIGIBLE


def binding_for(provider_id):
    result = resolve_provider(
        profile(provider_id=provider_id), project_id=PROJECT, now=NOW,
        available=True, authenticated=True, policy_authorized=True,
        privacy_eligible=True, network_allowed=True, required=True,
    )
    assert result.binding is not None
    return result.binding


def test_provider_eligibility_is_bound_to_evidence_and_fallback():
    binding_a = binding_for("knowledge-provider-a")
    binding_b = binding_for("knowledge-provider-b")
    request_a = request(provider_binding=binding_a)
    assert evidence(
        provider_id=binding_a.provider_id,
        provider_binding_sha256=binding_a.binding_sha256,
    ).assess(request_a, now=NOW) is DocumentationStatus.VALID
    evidence_b = evidence(
        provider_id=binding_b.provider_id,
        provider_binding_sha256=binding_b.binding_sha256,
    )
    assert evidence_b.assess(request_a, now=NOW) is DocumentationStatus.PROVIDER_INELIGIBLE
    blocked_b = resolve_provider(
        profile(provider_id=binding_b.provider_id), project_id=PROJECT, now=NOW,
        available=True, authenticated=True, policy_authorized=False,
        privacy_eligible=True, network_allowed=True, required=True,
    )
    assert blocked_b.binding is None
    assert (
        evidence_b.assess(request(provider_binding=None), now=NOW)
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    request_b = request(provider_binding=binding_b)
    assert evidence_b.assess(request_b, now=NOW) is DocumentationStatus.VALID
    assert evidence(
        provider_id=binding_a.provider_id,
        provider_binding_sha256=binding_a.binding_sha256,
    ).assess(request_b, now=NOW) is DocumentationStatus.PROVIDER_INELIGIBLE
    tampered = evidence().to_dict()
    tampered["provider_id"] = "knowledge-provider-b"
    with pytest.raises(DocumentationError):
        evidence_from_dict(tampered)
    assert (
        evidence().assess(request(), now="2026-08-26T12:00:00Z")
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    cross_project = request(project_id="project-other", provider_binding=DEFAULT_BINDING)
    cross_project_evidence = evidence(project_id="project-other")
    assert (
        cross_project_evidence.assess(cross_project, now=NOW)
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    future_resolution = resolve_provider(
        profile(), project_id=PROJECT, now="2026-08-24T12:00:01Z",
        available=True, authenticated=True, policy_authorized=True,
        privacy_eligible=True, network_allowed=True, required=True,
    )
    assert future_resolution.binding is not None
    future_request = request(provider_binding=future_resolution.binding)
    future_evidence = evidence(
        provider_binding_sha256=future_resolution.binding.binding_sha256,
    )
    assert (
        future_evidence.assess(future_request, now=NOW)
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    assert (
        future_evidence.assess(future_request, now="2026-08-24T12:00:02Z")
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )


def test_provider_binding_requires_authenticated_agf_issuance():
    issued = binding_for("knowledge-provider-issued")
    assert evidence(
        provider_id=issued.provider_id,
        provider_binding_sha256=issued.binding_sha256,
    ).assess(request(provider_binding=issued), now=NOW) is DocumentationStatus.VALID

    fabricated = replace(issued, issuance_token="x" * 43)
    unsigned = {**fabricated.to_dict(), "binding_sha256": ""}
    fabricated = replace(
        fabricated,
        binding_sha256=hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    with pytest.raises(DocumentationError):
        evidence(
            provider_id=fabricated.provider_id,
            provider_binding_sha256=fabricated.binding_sha256,
        ).assess(request(provider_binding=fabricated), now=NOW)

    tampered = replace(issued, policy_authorized=False)
    with pytest.raises(DocumentationError):
        evidence(
            provider_id=tampered.provider_id,
            provider_binding_sha256=issued.binding_sha256,
        ).assess(request(provider_binding=tampered), now=NOW)


def test_provider_binding_issuance_is_not_persisted_in_documentation_evidence():
    payload = evidence().to_dict()
    assert "issuance_token" not in payload


def test_provider_binding_issuance_survives_restart_and_artifact_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("AGF_STATE_DIR", str(_DOCUMENTATION_STATE_ROOT))
    issued = binding_for("knowledge-provider-durable")
    authority = _documentation_authority(profile(provider_id=issued.provider_id), {})
    reloaded = replace(ProviderBinding(**issued.to_dict()), authority=authority)
    reloaded.validate(now=NOW)
    store = SessionStore(tmp_path / "session-state")
    item = evidence(
        provider_id=issued.provider_id,
        provider_binding_sha256=issued.binding_sha256,
    )
    persist_evidence(store, "session-one", item, provider_binding=issued)
    loaded = load_provider_binding(
        store, "session-one", issued.binding_sha256, eligibility_authority=authority
    )
    assert loaded.authority is authority
    assert item.assess(request(provider_binding=loaded), now=NOW) is DocumentationStatus.VALID
    path = (
        store.artifacts_dir
        / "session-one"
        / f"provider-binding-{issued.binding_sha256}.json"
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            issued.provider_id, "knowledge-tampered"
        )
    )
    with pytest.raises(DocumentationError):
        load_provider_binding(
            store, "session-one", issued.binding_sha256, eligibility_authority=authority
        )


def test_provider_binding_requires_durable_canonical_state(tmp_path, monkeypatch):
    issued = binding_for("knowledge-provider-foreign-owner")
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "missing-authority"))
    reloaded = ProviderBinding(**issued.to_dict())
    with pytest.raises(DocumentationError):
        reloaded.validate(now=NOW)


def test_reconciliation_requires_semantic_claim_agreement_across_providers():
    binding_a = binding_for("knowledge-provider-a")
    binding_b = binding_for("knowledge-provider-b")
    first = evidence(
        provider_id=binding_a.provider_id,
        provider_binding_sha256=binding_a.binding_sha256,
    )
    second = evidence(
        evidence_id="docs-evidence-2",
        provider_id=binding_b.provider_id,
        provider_binding_sha256=binding_b.binding_sha256,
        documentation_source=first.documentation_source,
    )
    assert (
        reconcile_evidence(
            (first, second), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.VALID
    )
    opposing = replace(
        second,
        claims=(seal_claim(
            "requests.timeouts.timeout_type", "integer-only",
            citation_sha256s=(citation_sha256(second.citations[0]),),
        ),),
    )
    opposing = seal(opposing)
    assert (
        reconcile_evidence(
            (first, opposing), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.CONTRADICTORY
    )
    same_topic = replace(
        second,
        claims=(seal_claim(
            "requests.timeouts.unit", "seconds",
            citation_sha256s=(citation_sha256(second.citations[0]),),
        ),),
    )
    same_topic = seal(same_topic)
    assert (
        reconcile_evidence(
            (first, same_topic), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.CONTRADICTORY
    )
    alternate_citation = DocumentationCitation("other-source", "timeouts", "same fact")
    corroborating = replace(
        second,
        citations=(alternate_citation,),
        claims=(seal_claim(
            "requests.timeouts.timeout_type", "float-or-none",
            citation_sha256s=(citation_sha256(alternate_citation),),
        ),),
    )
    corroborating = seal(corroborating)
    assert (
        reconcile_evidence(
            (first, corroborating), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.VALID
    )


def test_compound_secret_patterns_are_bounded():
    for value in (
        "AWS_SECRET_ACCESS_KEY=leaked",
        "AWS_ACCESS_KEY_ID: leaked",
        "secret access key: leaked",
        "client secret: leaked",
        "private key: leaked",
    ):
        with pytest.raises(DocumentationError):
            DocumentationCitation("source", "topic", value).validate()
    for value in (
        '{"password":"hunter2"}',
        '{"client_secret": "opaque"}',
        '{"api_key":"opaque"}',
        '{"access_token":"opaque"}',
        '{"nested":{"refresh_token":"opaque"}}',
        '{"password": ""}',
        "{'private_key':'opaque'}",
        '{&quot;password&quot;:&quot;hunter2&quot;}',
    ):
        with pytest.raises(DocumentationError):
            DocumentationCitation("source", "topic", value).validate()
    for value in (
        'the field "password" is required',
        'password is documented as a field name',
    ):
        DocumentationCitation("source", "topic", value).validate()
    for value in (
        "https://bucket.example/object?X-Amz-Signature=abc123",
        "https://bucket.example/object?region=us&X-Amz-Signature=abc123",
        "https://bucket.example/object?foo=1&sig=opaque-token",
        "https://bucket.example/object?x=1&X-Amz-Credential=access%2Fscope",
        "https://bucket.example/object?access_token=opaque-token&format=json",
        "https://bucket.example/object?foo=1&X-AMZ-SIGNATURE=",
        "https://bucket.example/object?sig=one&sig=two",
        "https://bucket.example/object?%58-Amz-Signature=encoded-name",
        "https://bucket.example/object?region=us&amp;X-Amz-Signature=html-escaped",
        "https://bucket.example/object?region=us&#38;sig=numeric-escaped",
        "https://alice&#58;s3cr3t&#64;example.test/manual",
        "https://alice&#x3A;s3cr3t&#x40;example.test/manual",
    ):
        with pytest.raises(DocumentationError):
            DocumentationCitation("source", "topic", value).validate()
    for value in (
        "https://bucket.example/object?region=eu&format=json",
        "https://bucket.example/object?significant=true",
        "not-a-url?sig=just-text",
        "https://alice&amp;#58;s3cr3t&amp;#64;example.test/manual",
    ):
        DocumentationCitation("source", "topic", value).validate()
    for value in ("private key rotation", "client secret lifecycle", "secret access key format"):
        DocumentationCitation("source", "topic", value).validate()
    pem_headers = (
        "-----BEGIN " + "PRIVATE KEY-----",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    )
    for header in pem_headers:
        value = header + "\nmaterial\n" + header.replace("BEGIN", "END")
        with pytest.raises(DocumentationError):
            DocumentationCitation("source", "topic", value).validate()
    for value in (
        "https://alice:s3cr3t@example.test/manual",
        "postgresql://admin:s3cr3t@db.example/app",
        "custom+db://service:p%40ss@host.example/path",
    ):
        with pytest.raises(DocumentationError):
            DocumentationCitation("source", "topic", value).validate()
    for value in (
        "https://example.test/manual",
        "https://alice@example.test/manual",
        "postgresql://db.example/app",
    ):
        DocumentationCitation("source", "topic", value).validate()


def test_claims_must_reference_existing_citations():
    with pytest.raises(DocumentationError):
        seal(evidence(claims=(seal_claim(
            "requests.timeouts.timeout_type", "float-or-none",
            citation_sha256s=("b" * 64,),
        ),)))
    with pytest.raises(DocumentationError):
        seal_claim(
            "requests.timeouts.timeout_type", "float-or-none",
            citation_sha256s=tuple("a" * 64 for _ in range(9)),
        )
    with pytest.raises(DocumentationError):
        seal_claim(
            "requests.timeouts.timeout_type", "float-or-none",
            citation_sha256s=("a" * 64, "a" * 64),
        )


def test_provider_binding_without_profile_expiry_has_bounded_ttl():
    result = resolve_provider(
        profile(expires_at=None), project_id=PROJECT, now=NOW,
        available=True, authenticated=True, policy_authorized=True,
        privacy_eligible=True, network_allowed=True, required=True,
    )
    assert result.binding is not None
    assert result.binding.expires_at == "2026-08-24T13:00:00Z"
    item = evidence(
        provider_binding_sha256=result.binding.binding_sha256,
    )
    bound_request = request(provider_binding=result.binding)
    assert item.assess(bound_request, now="2026-08-24T12:59:59Z") is DocumentationStatus.VALID
    assert (
        item.assess(bound_request, now="2026-08-24T13:00:00Z")
        is DocumentationStatus.PROVIDER_INELIGIBLE
    )
    distant = resolve_provider(
        profile(expires_at="2099-01-01T00:00:00Z"), project_id=PROJECT, now=NOW,
        available=True, authenticated=True, policy_authorized=True,
        privacy_eligible=True, network_allowed=True, required=True,
    )
    assert distant.binding is not None
    assert distant.binding.expires_at == "2026-08-24T13:00:00Z"


def test_persistence_restart_tamper_and_cross_session_replay(tmp_path):
    store = SessionStore(tmp_path / "state")
    item = evidence()
    persist_evidence(store, "session-one", item)
    assert load_evidence(store, "session-one", item.evidence_id) == item
    with pytest.raises(DocumentationError):
        load_evidence(store, "session-two", item.evidence_id)
    path = store.artifacts_dir / "session-one" / f"documentation-{item.evidence_id}.json"
    payload = json.loads(path.read_text())
    payload["requested_topic"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(DocumentationError):
        load_evidence(store, "session-one", item.evidence_id)


def test_repository_identity_and_binding_validation():
    with pytest.raises(DocumentationError):
        request(repository_id="not-a-repository").validate()
    with pytest.raises(DocumentationError):
        request(repository_id="/tmp/repository").validate()
    with pytest.raises(DocumentationError):
        request(repository_id="github.com/example/../repository").validate()
    with pytest.raises(DocumentationError):
        request(repository_id=None, revision_sha=REVISION).validate()
    with pytest.raises(DocumentationError):
        request(repository_id=None, revision_sha=None).validate()
    npm = request(dependency=dependency(registry="npm"))
    assert evidence().assess(npm, now=NOW) is DocumentationStatus.DEPENDENCY_MISMATCH


@pytest.mark.parametrize("registry,package_id", [
    ("npm", "@scope/package"),
    ("npm", "package-name"),
    ("pypi", "requests_http"),
    ("go", "github.com/gin-gonic/gin"),
    ("maven", "org.slf4j:slf4j-api"),
])
def test_registry_aware_package_identifiers(registry, package_id):
    dependency(registry=registry, package_id=package_id).validate()


def test_go_registry_accepts_v_prefixed_versions_without_global_normalization():
    go = dependency(
        registry="go", package_id="github.com/gin-gonic/gin",
        declared_constraint="v1.10.0", locked_version="v1.10.0",
        resolved_version="v1.10.0",
    )
    go.validate()
    assert go.demonstrated_version() == "v1.10.0"
    assert evidence(
        dependency=go, documentation_version="v1.10.0"
    ).assess(request(dependency=go), now=NOW) is DocumentationStatus.VALID
    prerelease = replace(
        go, declared_constraint=">=v1.10.0-rc.1",
        locked_version="v1.10.0-rc.2", resolved_version="v1.10.0-rc.2",
    )
    prerelease.validate()
    build = replace(
        go, declared_constraint="==v1.10.0+linux",
        locked_version="v1.10.0+linux", resolved_version="v1.10.0+linux",
    )
    build.validate()
    assert build.demonstrated_version() == "v1.10.0+linux"


def test_non_go_registries_reject_v_prefix():
    with pytest.raises(DocumentationError):
        dependency(registry="npm", resolved_version="v1.10.0").validate()


@pytest.mark.parametrize("registry,package_id", [
    ("unknown", "package"),
    ("go", "github.com/example/../secret"),
    ("maven", "org.slf4j"),
    ("npm", "@scope/"),
])
def test_unsupported_or_malformed_package_identifiers_fail_closed(registry, package_id):
    with pytest.raises(DocumentationError):
        dependency(registry=registry, package_id=package_id).validate()


def test_semver_prerelease_ordering_fails_closed_or_orders_correctly():
    prerelease = request(
        dependency=dependency(
            registry="npm",
            declared_constraint=">=2.0.0", locked_version=None, resolved_version="2.0.0-rc.1"
        )
    )
    assert evidence(dependency=prerelease.dependency, documentation_version="2.0.0-rc.1").assess(
        prerelease, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    beta = request(
        dependency=dependency(
            registry="npm",
            declared_constraint=">=2.0.0-rc.1", locked_version=None, resolved_version="2.0.0-beta.2"
        )
    )
    assert evidence(dependency=beta.dependency, documentation_version="2.0.0-beta.2").assess(
        beta, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    final = request(
        dependency=dependency(
            registry="npm",
            declared_constraint=">=2.0.0", locked_version=None, resolved_version="2.0.0"
        )
    )
    assert evidence(dependency=final.dependency, documentation_version="2.0.0").assess(
        final, now=NOW
    ) is DocumentationStatus.VALID
    exact = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="==2.0.0-rc.1", locked_version=None, resolved_version="2.0.0-rc.1"
        )
    )
    assert evidence(dependency=exact.dependency, documentation_version="2.0.0-rc.1").assess(
        exact, now=NOW
    ) is DocumentationStatus.VALID
    caret_zero = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="^0.2.0", locked_version=None, resolved_version="0.9.0"
        )
    )
    assert evidence(dependency=caret_zero.dependency, documentation_version="0.9.0").assess(
        caret_zero, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    build = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="==1.0.0+cpu", locked_version=None,
            resolved_version="1.0.0+cpu",
        )
    )
    assert evidence(
        dependency=build.dependency, documentation_version="1.0.0+gpu"
    ).assess(build, now=NOW) is DocumentationStatus.VERSION_MISMATCH
    bare = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="1.8.3", locked_version=None, resolved_version="1.8.3"
        )
    )
    assert evidence(dependency=bare.dependency).assess(bare, now=NOW) is DocumentationStatus.VALID
    both = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="==1.0.0-rc.1+cpu", locked_version=None,
            resolved_version="1.0.0-rc.1+cpu",
        )
    )
    assert evidence(
        dependency=both.dependency, documentation_version="1.0.0-rc.1+cpu"
    ).assess(both, now=NOW) is DocumentationStatus.VALID
    tilde_four = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="~1.2.3.4", locked_version=None, resolved_version="1.2.99.0"
        )
    )
    assert evidence(
        dependency=tilde_four.dependency, documentation_version="1.2.99.0"
    ).assess(tilde_four, now=NOW) is DocumentationStatus.CONTRADICTORY


@pytest.mark.parametrize("constraint", ["^1.0.0", "~1.2", ">=1.0.0"])
def test_undeclared_prereleases_do_not_satisfy_ranges(constraint):
    ranged = request(
        dependency=dependency(
            registry="npm",
            declared_constraint=constraint,
            locked_version=None,
            resolved_version="1.5.0-rc.1",
        )
    )
    assert evidence(
        dependency=ranged.dependency, documentation_version="1.5.0-rc.1"
    ).assess(ranged, now=NOW) is DocumentationStatus.CONTRADICTORY


def test_wildcard_range_does_not_admit_undeclared_prerelease():
    ranged = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="*", locked_version=None, resolved_version="2.0.0-rc.1"
        )
    )
    assert evidence(
        dependency=ranged.dependency, documentation_version="2.0.0-rc.1"
    ).assess(ranged, now=NOW) is DocumentationStatus.CONTRADICTORY


def test_explicit_prerelease_series_can_satisfy_range():
    ranged = request(
        dependency=dependency(
            registry="npm",
            declared_constraint=">=1.0.0-rc.1,<2.0.0",
            locked_version=None,
            resolved_version="1.0.0-rc.2",
        )
    )
    assert evidence(
        dependency=ranged.dependency, documentation_version="1.0.0-rc.2"
    ).assess(ranged, now=NOW) is DocumentationStatus.VALID


@pytest.mark.parametrize("value", [
    "1.8.3-rc..1", "1.8.3-.rc1", "1.8.3-rc1.", "1.8.3+",
    "1.8.3-rc..1+build", "1.8.3-rc1+..build",
])
def test_malformed_prerelease_and_build_versions_rejected_before_assessment(value):
    with pytest.raises(DocumentationError):
        dependency(resolved_version=value).validate()


def test_hostile_numeric_version_lengths_fail_closed():
    oversized_component = "1" * 33 + ".0.0"
    with pytest.raises(DocumentationError):
        dependency(resolved_version=oversized_component).validate()
    with pytest.raises(DocumentationError):
        dependency(resolved_version="9" * 5000).validate()


def test_valid_prerelease_and_build_version_schema():
    dependency(resolved_version="1.8.3-rc.1+build.7").validate()


def test_partial_tilde_range_uses_major_upper_bound():
    ranged = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="~1", locked_version=None, resolved_version="1.2.3"
        )
    )
    assert evidence(
        dependency=ranged.dependency, documentation_version="1.2.3"
    ).assess(ranged, now=NOW) is DocumentationStatus.VALID


def test_partial_caret_zero_major_ranges_use_semver_upper_bounds():
    major_zero = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="^0", locked_version=None, resolved_version="0.5.0"
        )
    )
    assert evidence(
        dependency=major_zero.dependency, documentation_version="0.5.0"
    ).assess(major_zero, now=NOW) is DocumentationStatus.VALID
    minor_zero = request(
        dependency=dependency(
            registry="npm",
            declared_constraint="^0.0", locked_version=None, resolved_version="0.0.5"
        )
    )
    assert evidence(
        dependency=minor_zero.dependency, documentation_version="0.0.5"
    ).assess(minor_zero, now=NOW) is DocumentationStatus.VALID


def test_reconciliation_uses_normalized_documentation_version_identity():
    first = evidence(documentation_version="1.8.3")
    second = evidence(evidence_id="docs-evidence-2", documentation_version="1.8.3.0")
    assert reconcile_evidence((first, second), request(), now=NOW) is DocumentationStatus.VALID


@pytest.mark.parametrize("version", ["1.0", "1.0rc1", "1!2.0.0", "1.0.post1", "1.0.dev1"])
def test_pypi_versions_use_pep440_canonicalization(version):
    item = dependency(
        registry="pypi",
        declared_constraint=f"=={version}",
        locked_version=version,
        resolved_version=version,
    )
    assert item.demonstrated_version() == version


@pytest.mark.parametrize(
    ("constraint", "resolved", "expected"),
    [("~=1.4.2", "1.4.5", DocumentationStatus.VALID),
     ("~=1.4.2", "1.5.0", DocumentationStatus.CONTRADICTORY),
     ("~=1.4", "1.9", DocumentationStatus.VALID),
     ("~=bad", "1.4.5", DocumentationStatus.CONTRADICTORY)],
)
def test_pypi_compatible_release_specifier_uses_pep440(constraint, resolved, expected):
    item = request(
        dependency=dependency(
            registry="pypi", package_id="requests", declared_constraint=constraint,
            locked_version=None, resolved_version=resolved,
        )
    )
    assert evidence(
        dependency=item.dependency, documentation_version=resolved
    ).assess(item, now=NOW) is expected


def test_pypi_prerelease_opt_in_is_set_wide_not_release_core_specific():
    item = request(
        dependency=dependency(
            registry="pypi", declared_constraint=">=1.0rc1,<2",
            locked_version=None, resolved_version="1.5rc1",
        )
    )
    assert evidence(
        dependency=item.dependency, documentation_version="1.5rc1"
    ).assess(item, now=NOW) is DocumentationStatus.VALID
    no_opt_in = replace(item, dependency=replace(
        item.dependency, declared_constraint=">=1,<2", resolved_version="1.5rc1"
    ))
    assert evidence(
        dependency=no_opt_in.dependency, documentation_version="1.5rc1"
    ).assess(no_opt_in, now=NOW) is DocumentationStatus.CONTRADICTORY


@pytest.mark.parametrize("value", ["1..0", "1.0rc..1", "1" * 33 + ".0"])
def test_pypi_malformed_versions_fail_closed(value):
    with pytest.raises(DocumentationError):
        dependency(registry="pypi", resolved_version=value).validate()


def test_maven_versions_use_bounded_qualifier_ordering():
    item = dependency(
        registry="maven",
        package_id="org.example:demo",
        declared_constraint="==1.0.0.Final",
        locked_version="1.0.0.Final",
        resolved_version="1.0.0.Final",
    )
    assert item.demonstrated_version() == "1.0.0.Final"
    prerelease = replace(
        item,
        declared_constraint="<1.0.0.Final",
        locked_version="1.0.0.RC1",
        resolved_version="1.0.0.RC1",
    )
    assert prerelease.demonstrated_version() == "1.0.0.RC1"


@pytest.mark.parametrize(
    "alias, long_form", [("a2", "alpha2"), ("b2", "beta2"), ("m3", "milestone3")]
)
def test_maven_qualifier_aliases_have_equivalent_identity(alias, long_form):
    left = dependency(
        registry="maven", package_id="org.example:demo",
        declared_constraint=f"==1.0-{alias}", locked_version=f"1.0-{alias}",
        resolved_version=f"1.0-{alias}",
    )
    right = replace(left, locked_version=f"1.0-{long_form}", resolved_version=f"1.0-{long_form}")
    assert left.demonstrated_version() == f"1.0-{alias}"
    assert right.demonstrated_version() == f"1.0-{long_form}"


def test_maven_alias_ordering_is_numeric_and_fail_closed_for_unknown_qualifiers():
    before = dependency(
        registry="maven", package_id="org.example:demo", declared_constraint=">1.0-a2",
        locked_version="1.0-alpha1", resolved_version="1.0-alpha1",
    )
    with pytest.raises(DocumentationError):
        before.demonstrated_version()
    after = replace(before, locked_version="1.0-alpha3", resolved_version="1.0-alpha3")
    assert after.demonstrated_version() == "1.0-alpha3"
    with pytest.raises(DocumentationError):
        dependency(
            registry="maven", package_id="org.example:demo", resolved_version="1.0-unknown1"
        ).validate()


@pytest.mark.parametrize("value", ["1..0", "1.0.0.unknown", "1.0.0."])
def test_maven_malformed_or_unknown_versions_fail_closed(value):
    with pytest.raises(DocumentationError):
        dependency(
            registry="maven", package_id="org.example:demo", resolved_version=value
        ).validate()


@pytest.mark.parametrize(
    ("constraint", "resolved", "expected"),
    [("1", "1.5.0", DocumentationStatus.VALID),
     ("1", "2.0.0", DocumentationStatus.CONTRADICTORY),
     ("1.2", "1.2.9", DocumentationStatus.VALID),
     ("1.2", "1.3.0", DocumentationStatus.CONTRADICTORY),
     ("1.2.3", "1.2.3", DocumentationStatus.VALID)],
)
def test_npm_bare_partial_versions_are_ranges(constraint, resolved, expected):
    item = request(
        dependency=dependency(
            registry="npm", package_id="lodash", declared_constraint=constraint,
            locked_version=None, resolved_version=resolved,
        )
    )
    assert evidence(
        dependency=item.dependency, documentation_version=resolved
    ).assess(item, now=NOW) is expected
