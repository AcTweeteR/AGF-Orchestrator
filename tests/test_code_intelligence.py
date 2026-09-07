import json
from dataclasses import replace

import pytest

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
    resolve_provider,
    seal,
)
from agf_orchestrator.session_store import SessionStore

PROJECT = "project-demo"
REPOSITORY = "github.com/example/repository"
REVISION = "a" * 40
OTHER_REVISION = "b" * 40
NOW = "2026-08-24T12:00:00Z"


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


def test_optional_and_required_provider_unavailability_fail_closed():
    gates = SelectionGates(True, True, True, True, True, True)
    optional = resolve_provider((), project_id=PROJECT, required=False, now=NOW, gates=gates)
    required = resolve_provider((), project_id=PROJECT, required=True, now=NOW, gates=gates)
    assert optional.status is IntelligenceStatus.UNAVAILABLE
    assert required.status is IntelligenceStatus.UNAVAILABLE
    selected = resolve_provider(
        (provider_candidate(),), project_id=PROJECT, required=True, now=NOW, gates=gates
    )
    assert selected.status is IntelligenceStatus.VALID
    wrong_project = resolve_provider(
        (provider_candidate("project-other"),), project_id=PROJECT,
        required=True, now=NOW, gates=gates,
    )
    assert wrong_project.status is IntelligenceStatus.UNAVAILABLE
    unsupported = resolve_provider(
        (provider_candidate(capability_status=CapabilityStatus.UNSUPPORTED),),
        project_id=PROJECT, required=True, now=NOW,
        gates=gates,
    )
    assert unsupported.status is IntelligenceStatus.UNSUPPORTED_CAPABILITY


def test_fallback_is_existing_selector_policy_and_never_changes_scope():
    gates = SelectionGates(True, True, True, True, True, True, allow_fallback=True)
    fallback = resolve_provider(
        (provider_candidate("project-other", "provider-first", 0), provider_candidate(priority=1)),
        project_id=PROJECT, required=True, now=NOW, gates=gates,
    )
    assert fallback.status is IntelligenceStatus.VALID
    assert fallback.selection is not None and fallback.selection.fallback_used is True
    forbidden = resolve_provider(
        (provider_candidate("project-other", "provider-first", 0), provider_candidate(priority=1)),
        project_id=PROJECT, required=True, now=NOW,
        gates=replace(gates, allow_fallback=False),
    )
    assert forbidden.status is IntelligenceStatus.UNAVAILABLE
