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
from agf_orchestrator.capability_extensions import seal as seal_profile
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
    evidence_from_dict,
    latest_is_unsafe_for_project,
    load_evidence,
    persist_evidence,
    reconcile_evidence,
    resolve_provider,
    seal,
    seal_claim,
)
from agf_orchestrator.session_store import SessionStore

PROJECT = "project-demo"
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
    value = DocumentationEvidence(
        "1.0", "docs-evidence-1", "knowledge-docs", DEFAULT_BINDING.binding_sha256,
        PROJECT, REPOSITORY,
        REVISION, DocumentationOperation.RETRIEVE_TOPIC, dependency(), "timeouts",
        "timeouts", "1.8.3", "fixture-docs",
        (
            DocumentationCitation(
                "https://docs.example/requests/1.8.3", "timeouts", "timeout parameter"
            ),
        ),
        (seal_claim("requests.timeouts.timeout_type", "float-or-none"),),
        NOW, DocumentationFreshness.FRESH, DocumentationStatus.VALID, "",
    )
    return seal(replace(value, **changes))


def profile(
    *, network_required=False, privacy_review_required=False,
    capabilities=("documentation",),
    provider_id="knowledge-docs",
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
            "2026-08-25T12:00:00Z", "",
        )
    )


DEFAULT_BINDING = resolve_provider(
    profile(), project_id=PROJECT, now=NOW, available=True, authenticated=True,
    policy_authorized=True, privacy_eligible=True, network_allowed=True, required=True,
).binding
assert isinstance(DEFAULT_BINDING, ProviderBinding)


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
    old = evidence(observed_at="2026-08-24T00:00:00Z")
    assert old.assess(request(max_age_seconds=60), now=NOW) is DocumentationStatus.STALE


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
        claims=(seal_claim("requests.timeouts.timeout_type", "integer-only"),),
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
        is DocumentationStatus.UNAVAILABLE
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
        claims=(seal_claim("requests.timeouts.timeout_type", "integer-only"),),
    )
    opposing = seal(opposing)
    assert (
        reconcile_evidence(
            (first, opposing), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.CONTRADICTORY
    )
    same_topic = replace(second, claims=(seal_claim("requests.timeouts.unit", "seconds"),))
    same_topic = seal(same_topic)
    assert (
        reconcile_evidence(
            (first, same_topic), request(provider_binding=binding_a), now=NOW,
            provider_bindings=(binding_a, binding_b),
        ) is DocumentationStatus.CONTRADICTORY
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
    for value in ("private key rotation", "client secret lifecycle", "secret access key format"):
        DocumentationCitation("source", "topic", value).validate()


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


def test_semver_prerelease_ordering_fails_closed_or_orders_correctly():
    prerelease = request(
        dependency=dependency(
            declared_constraint=">=2.0.0", locked_version=None, resolved_version="2.0.0-rc.1"
        )
    )
    assert evidence(dependency=prerelease.dependency, documentation_version="2.0.0-rc.1").assess(
        prerelease, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    beta = request(
        dependency=dependency(
            declared_constraint=">=2.0.0-rc.1", locked_version=None, resolved_version="2.0.0-beta.2"
        )
    )
    assert evidence(dependency=beta.dependency, documentation_version="2.0.0-beta.2").assess(
        beta, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    final = request(
        dependency=dependency(
            declared_constraint=">=2.0.0", locked_version=None, resolved_version="2.0.0"
        )
    )
    assert evidence(dependency=final.dependency, documentation_version="2.0.0").assess(
        final, now=NOW
    ) is DocumentationStatus.VALID
    exact = request(
        dependency=dependency(
            declared_constraint="==2.0.0-rc.1", locked_version=None, resolved_version="2.0.0-rc.1"
        )
    )
    assert evidence(dependency=exact.dependency, documentation_version="2.0.0-rc.1").assess(
        exact, now=NOW
    ) is DocumentationStatus.VALID
    caret_zero = request(
        dependency=dependency(
            declared_constraint="^0.2.0", locked_version=None, resolved_version="0.9.0"
        )
    )
    assert evidence(dependency=caret_zero.dependency, documentation_version="0.9.0").assess(
        caret_zero, now=NOW
    ) is DocumentationStatus.CONTRADICTORY
    build = request(
        dependency=dependency(
            declared_constraint="==1.0.0+cpu", locked_version=None,
            resolved_version="1.0.0+cpu",
        )
    )
    assert evidence(
        dependency=build.dependency, documentation_version="1.0.0+gpu"
    ).assess(build, now=NOW) is DocumentationStatus.VERSION_MISMATCH
    bare = request(
        dependency=dependency(
            declared_constraint="1.8.3", locked_version=None, resolved_version="1.8.3"
        )
    )
    assert evidence(dependency=bare.dependency).assess(bare, now=NOW) is DocumentationStatus.VALID
    both = request(
        dependency=dependency(
            declared_constraint="==1.0.0-rc.1+cpu", locked_version=None,
            resolved_version="1.0.0-rc.1+cpu",
        )
    )
    assert evidence(
        dependency=both.dependency, documentation_version="1.0.0-rc.1+cpu"
    ).assess(both, now=NOW) is DocumentationStatus.VALID
    tilde_four = request(
        dependency=dependency(
            declared_constraint="~1.2.3.4", locked_version=None, resolved_version="1.2.99.0"
        )
    )
    assert evidence(
        dependency=tilde_four.dependency, documentation_version="1.2.99.0"
    ).assess(tilde_four, now=NOW) is DocumentationStatus.CONTRADICTORY


@pytest.mark.parametrize("value", [
    "1.8.3-rc..1", "1.8.3-.rc1", "1.8.3-rc1.", "1.8.3+",
    "1.8.3-rc..1+build", "1.8.3-rc1+..build",
])
def test_malformed_prerelease_and_build_versions_rejected_before_assessment(value):
    with pytest.raises(DocumentationError):
        dependency(resolved_version=value).validate()


def test_valid_prerelease_and_build_version_schema():
    dependency(resolved_version="1.8.3-rc.1+build.7").validate()
