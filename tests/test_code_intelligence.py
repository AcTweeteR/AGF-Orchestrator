import json
from dataclasses import replace

import pytest
from provider_test_support import sign_state as sign_owner_state

from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.code_intelligence import (
    CodeIntelligenceError,
    CodeIntelligenceEvidence,
    CodeIntelligenceRequest,
    CodeLocation,
    IntelligenceOperation,
    IntelligenceStatus,
    compare_efficiency,
    evidence_from_dict,
    load_evidence,
    persist_evidence,
    seal,
)
from agf_orchestrator.code_intelligence import resolve_provider as _resolve_provider
from agf_orchestrator.provider_eligibility import ProviderEligibilityAuthority
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


def resolve_provider(candidates, **kwargs):
    kwargs.setdefault("target_sha", REVISION)
    return _resolve_provider(candidates, **kwargs)


def request(**changes):
    value = CodeIntelligenceRequest(
        PROJECT, REPOSITORY, REVISION, IntelligenceOperation.SYMBOL, "Widget",
        ("src",),
    )
    return replace(value, **changes)


def evidence(**changes):
    value = CodeIntelligenceEvidence(
        "1.0", "evidence-symbol-1", "provider-code-intelligence", PROJECT,
        REPOSITORY, REVISION, REVISION, IntelligenceOperation.SYMBOL, "Widget",
        (CodeLocation("src/widget.py", 10, 12, "Widget", "definition"),),
        ("symbol Widget",), "fixture-provider", NOW, IntelligenceStatus.VALID, "",
    )
    return seal(replace(value, **changes))


def test_valid_definition_references_and_efficiency_fixture():
    item = evidence(operation=IntelligenceOperation.DEFINITION)
    assert (
        item.assess(request(operation=IntelligenceOperation.DEFINITION))
        is IntelligenceStatus.VALID
    )
    assert compare_efficiency(("README.md", "src/widget.py", "src/other.py"), item).improved


def test_revision_project_repository_and_path_bindings_fail_closed():
    item = evidence()
    assert (
        item.assess(request(revision_sha=OTHER_REVISION))
        is IntelligenceStatus.MISMATCHED_REVISION
    )
    assert item.assess(request(project_id="project-other")) is IntelligenceStatus.MISMATCHED_PROJECT
    assert (
        item.assess(request(repository_id="github.com/other/repository"))
        is IntelligenceStatus.MISMATCHED_REPOSITORY
    )
    assert (
        item.assess(request(operation=IntelligenceOperation.REFERENCES))
        is IntelligenceStatus.MISMATCHED_OPERATION
    )
    assert item.assess(request(query="Other")) is IntelligenceStatus.MISMATCHED_QUERY
    outside = seal(replace(item, locations=(CodeLocation("tests/test.py", 1, 1),)))
    assert outside.assess(request()) is IntelligenceStatus.BLOCKED_PATH


def test_recursive_glob_is_segment_safe_and_repository_identity_is_canonical():
    nested = seal(replace(evidence(), locations=(CodeLocation("src/lib/deep.py", 1, 1),)))
    assert nested.assess(request(allowed_paths=("src/**/*.py",))) is IntelligenceStatus.VALID
    assert nested.assess(request(allowed_paths=("src/*.py",))) is IntelligenceStatus.BLOCKED_PATH
    with pytest.raises(CodeIntelligenceError):
        request(repository_id="not-a-canonical-repository").validate()


def test_missing_allowed_path_scope_fails_closed():
    with pytest.raises(CodeIntelligenceError):
        request(allowed_paths=()).validate()


def test_ambiguity_stale_and_malformed_evidence_are_distinct():
    ambiguous = evidence(status=IntelligenceStatus.AMBIGUOUS, locations=())
    assert ambiguous.assess(request()) is IntelligenceStatus.AMBIGUOUS
    stale = evidence(status=IntelligenceStatus.STALE, index_revision_sha=OTHER_REVISION)
    assert stale.assess(request()) is IntelligenceStatus.STALE
    missing = evidence(status=IntelligenceStatus.NOT_FOUND, locations=())
    assert missing.assess(request()) is IntelligenceStatus.NOT_FOUND
    payload = evidence().to_dict()
    payload["locations"][0]["path"] = "../unsafe.py"
    with pytest.raises(CodeIntelligenceError):
        evidence_from_dict(payload)


def test_duplicate_conflicting_and_oversized_results_rejected():
    item = evidence()
    duplicate = replace(item, locations=(item.locations[0], item.locations[0]))
    with pytest.raises(CodeIntelligenceError):
        seal(duplicate)
    with pytest.raises(CodeIntelligenceError):
        compare_efficiency(
            ("src/widget.py",),
            replace(item, locations=(CodeLocation("missing.py", 1, 1),)),
        )
    with pytest.raises(CodeIntelligenceError):
        compare_efficiency(
            ("src/widget.py",), evidence(status=IntelligenceStatus.STALE),
        )


def test_persistence_restart_tamper_and_cross_session_replay(tmp_path):
    store = SessionStore(tmp_path / "state")
    item = evidence()
    persist_evidence(store, "session-one", item)
    assert load_evidence(store, "session-one", item.evidence_id) == item
    with pytest.raises(CodeIntelligenceError):
        load_evidence(store, "session-two", item.evidence_id)
    path = store.artifacts_dir / "session-one" / f"code-intelligence-{item.evidence_id}.json"
    payload = json.loads(path.read_text())
    payload["query"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(CodeIntelligenceError):
        load_evidence(store, "session-one", item.evidence_id)


def provider_candidate(
    project_id=PROJECT, provider_id="provider-code-intelligence", priority=0,
    capability_status=CapabilityStatus.SUPPORTED,
):
    source = "fixture provider profile"
    profile = CapabilityProfile(
        "1.0", "profile-code-intelligence", project_id, provider_id, 1,
        source, sha256_text(source), NOW, "2026-08-25T12:00:00Z",
        (CapabilityObservation("code-intelligence", capability_status, "1"),), "",
    )
    return CapabilityCandidate(
        replace(profile, profile_sha256=capability_profile_hash(profile)), priority
    )


def eligibility_authority(tmp_path, candidates):
    gate_evidence = (
        ("policy_eligible", "active-policy:merge-policy-adr-0003:" + "a" * 64),
        ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;True"),
        ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
        ("budget_eligible", "bounded-timeout-seconds:90;True"),
        ("health_eligible", "invocation-verified:True"),
        ("empirical_evidence_eligible", "deterministic-canary-sha256:" + "b" * 64),
    )
    value = build_state(
        project_id=PROJECT, target_sha=REVISION,
        constitution_id="constitution-agf-v1", constitution_record_hash="c" * 64,
        observed_at=NOW, expires_at="2030-08-25T12:00:00Z",
        candidates=tuple(candidates),
        provider_interfaces=tuple((item.profile.provider_id, "code") for item in candidates),
        gates=SelectionGates(True, True, True, True, True, True),
        gate_evidence=gate_evidence, policy_generation=2,
        signing_key_id="test-owner-ed25519",
        requirements=("code-intelligence",), decision_domain="code-intelligence",
    )
    store = ProviderIntelligenceStore(tmp_path)
    project_store = store.for_project(PROJECT, decision_domain="code-intelligence")
    project_store.save(sign_owner_state(value))
    return ProviderEligibilityAuthority(store)


def test_optional_and_required_provider_unavailability_fail_closed(tmp_path):
    gates = SelectionGates(True, True, True, True, True, True)
    authority = eligibility_authority(tmp_path, ())
    optional = resolve_provider((), project_id=PROJECT, required=False, now=NOW, gates=gates,
                               eligibility_authority=authority)
    required = resolve_provider((), project_id=PROJECT, required=True, now=NOW, gates=gates,
                                eligibility_authority=authority)
    assert optional.status is IntelligenceStatus.UNAVAILABLE
    assert required.status is IntelligenceStatus.UNAVAILABLE
    selected_candidate = provider_candidate()
    selected = resolve_provider(
        (selected_candidate,), project_id=PROJECT, required=True, now=NOW, gates=gates,
        eligibility_authority=eligibility_authority(tmp_path / "selected", (selected_candidate,)),
    )
    assert selected.status is IntelligenceStatus.VALID
    wrong_project = resolve_provider(
        (provider_candidate("project-other"),), project_id=PROJECT,
        required=True, now=NOW, gates=gates, eligibility_authority=authority,
    )
    assert wrong_project.status is IntelligenceStatus.UNAVAILABLE
    unsupported_candidate = provider_candidate(capability_status=CapabilityStatus.UNSUPPORTED)
    unsupported = resolve_provider(
        (unsupported_candidate,),
        project_id=PROJECT, required=True, now=NOW,
        gates=gates, eligibility_authority=eligibility_authority(
            tmp_path / "unsupported", (unsupported_candidate,)
        ),
    )
    assert unsupported.status is IntelligenceStatus.UNAVAILABLE


def test_caller_gates_cannot_authorize_without_canonical_authority():
    result = resolve_provider(
        (provider_candidate(),), project_id=PROJECT, required=True, now=NOW,
        gates=SelectionGates(True, True, True, True, True, True),
    )
    assert result.status is IntelligenceStatus.UNAVAILABLE
    assert "canonical provider eligibility" in result.reason


def test_code_intelligence_rejects_duck_typed_authority():
    class ForgedAuthority:
        def select(self, *_args, **_kwargs):
            raise AssertionError("forged authority was invoked")

    result = resolve_provider(
        (provider_candidate(),), project_id=PROJECT, required=True, now=NOW,
        gates=SelectionGates(True, True, True, True, True, True),
        eligibility_authority=ForgedAuthority(),
    )
    assert result.status is IntelligenceStatus.UNAVAILABLE
    assert "canonical provider eligibility" in result.reason


def test_fallback_is_existing_selector_policy_and_never_changes_scope(tmp_path):
    gates = SelectionGates(True, True, True, True, True, True, allow_fallback=True)
    first = provider_candidate("project-other", "provider-first", 0)
    second = provider_candidate(priority=1)
    fallback = resolve_provider(
        (first, second),
        project_id=PROJECT, required=True, now=NOW, gates=gates,
        eligibility_authority=eligibility_authority(tmp_path, (second,)),
    )
    assert fallback.status is IntelligenceStatus.VALID
    # A caller cannot omit the owner candidate and turn it into a fallback;
    # the owner state contains only ``second``, so it is the primary.
    assert fallback.selection is not None and fallback.selection.fallback_used is False
    forbidden = resolve_provider(
        (first, second),
        project_id=PROJECT, required=True, now=NOW,
        gates=replace(gates, allow_fallback=False),
        eligibility_authority=eligibility_authority(tmp_path / "forbidden", (second,)),
    )
    # Caller-supplied fallback flags are observations only; the owner state
    # remains authoritative and permits the configured fallback.
    assert forbidden.status is IntelligenceStatus.VALID


def test_fallback_uses_selector_priority_order_not_input_order(tmp_path):
    low_priority = provider_candidate(provider_id="provider-low", priority=0)
    high_priority = provider_candidate(provider_id="provider-high", priority=1)
    authority = eligibility_authority(tmp_path, (low_priority, high_priority))
    result = resolve_provider(
        (high_priority, low_priority), project_id=PROJECT, required=True, now=NOW,
        gates=SelectionGates(True, True, True, True, True, True, allow_fallback=False),
        eligibility_authority=authority,
    )
    assert result.status is IntelligenceStatus.VALID
    assert result.selection is not None
    assert result.selection.provider_id == "provider-low"
    assert result.selection.fallback_used is False


def test_caller_cannot_add_or_omit_owner_candidates_or_change_priority(tmp_path):
    owner_low = provider_candidate(provider_id="provider-low", priority=0)
    owner_high = provider_candidate(provider_id="provider-high", priority=1)
    authority = eligibility_authority(tmp_path, (owner_low, owner_high))

    # The caller omits the owner primary, supplies a fabricated candidate,
    # and changes the apparent priority. Selection must still use the
    # owner-authenticated candidate set and its priority/order.
    fabricated = provider_candidate(provider_id="provider-fabricated", priority=-100)
    result = resolve_provider(
        (replace(owner_high, priority=-100), fabricated),
        project_id=PROJECT,
        required=True,
        now=NOW,
        gates=SelectionGates(True, True, True, True, True, True),
        eligibility_authority=authority,
    )
    assert result.status is IntelligenceStatus.VALID
    assert result.selection is not None
    assert result.selection.provider_id == "provider-low"
    assert result.selection.profile_id == owner_low.profile.profile_id
    assert result.selection.fallback_used is False
