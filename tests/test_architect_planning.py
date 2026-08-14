import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agf_orchestrator.architect_planning import (
    ArchitectPlanningError,
    ProviderArchitect,
    ProviderInvocationError,
    architect_response_schema,
    build_architect_request,
    provider_evidence_payload,
    validate_architect_response,
    validate_provider_selection_evidence,
    verify_provider_evidence,
)
from agf_orchestrator.capability_profiles import (
    CapabilityObservation,
    CapabilityProfile,
    CapabilityStatus,
    capability_profile_hash,
    sha256_text,
)
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.cli import _AdapterArchitectProvider
from tests.test_target_assessment import assess, context, repo

PROJECT = "project-test"
CAPABILITIES = (
    "repository-understanding", "structured-output", "reasoning", "context-capacity",
)
GATES = SelectionGates(
    policy_eligible=True, privacy_eligible=True, independence_eligible=True,
    budget_eligible=True, health_eligible=True, empirical_evidence_eligible=True,
)


def profile(provider, status=CapabilityStatus.SUPPORTED):
    source = f"approved:{provider}:architect"
    value = "verified" if status is CapabilityStatus.SUPPORTED else None
    current = CapabilityProfile(
        "1.0", f"profile-{provider}", PROJECT, provider, 1, source,
        sha256_text(source),
        "2026-08-10T00:00:00Z", "2026-08-11T00:00:00Z",
        tuple(CapabilityObservation(name, status, value) for name in CAPABILITIES),
        "0" * 64,
    )
    return replace(current, profile_sha256=capability_profile_hash(current))


def registration(repository):
    return SimpleNamespace(
        project_id=PROJECT, repository_root=repository.root, origin_url=repository.origin
    )


def response():
    return {
        "assessment_summary": "README is evidenced.",
        "proposed_outcome": "BOUNDED_IMPLEMENTATION",
        "rationale": "The objective explicitly targets the README.",
        "confidence": 0.9,
        "proposed_tasks": [{
            "objective": "Improve file:README.md",
            "justification": "README exists in baseline evidence.",
            "dependencies": [],
            "allowed_paths": ["README.md"],
            "prohibited_paths": [".git", "secrets"],
            "acceptance_criteria": ["README improvement is documented."],
            "validation_requirements": ["python -m pytest"],
            "evidence_references": ["README.md"],
            "risk_level": "low",
        }],
        "architecture_implications": ["documentation-only"],
        "preliminary_risk_indicators": ["review required"],
        "evidence_references": ["README.md"],
        "unresolved_unknowns": [],
    }


class FakeProvider:
    provider_id = "provider-b"

    def propose(self, request):
        assert request.request_hash
        return response()


class FailingProvider(FakeProvider):
    provider_id = "provider-a"

    def propose(self, request):
        raise ProviderInvocationError("provider unavailable")


def test_provider_selection_uses_capability_evidence_and_fallback(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a", CapabilityStatus.UNKNOWN), 0),
         CapabilityCandidate(profile("provider-b"), 1)),
        {"provider-b": FakeProvider()}, now="2026-08-10T12:00:00Z", project_id=PROJECT,
        gates=GATES,
    )
    request = build_architect_request(
        "Improve file:README.md",
        repository,
        assessment,
        registered_project=registration(repository),
    )
    proposal = architect.propose(request)
    assert proposal["tasks"][0]["allowed_paths"] == ["README.md"]
    assert architect.provider_selection["provider_id"] == "provider-b"
    assert architect.provider_selection["fallback_used"] is True


def test_architect_rejects_non_executable_validation_requirement(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    payload = response()
    payload["proposed_tasks"][0]["validation_requirements"] = [
        "Run the existing test with an available runner."
    ]
    with pytest.raises(ArchitectPlanningError, match="executable"):
        validate_architect_response(
            json.dumps(payload),
            build_architect_request(
                "Improve file:README.md", repository, assessment,
                registered_project=registration(repository),
            ),
        )


@pytest.mark.parametrize("command", ["python -m pytest", "git diff --check"])
def test_architect_accepts_valid_validation_commands(tmp_path, command):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    payload = response()
    payload["proposed_tasks"][0]["validation_requirements"] = [command]
    validate_architect_response(
        json.dumps(payload),
        build_architect_request(
            "Improve file:README.md", repository, assessment,
            registered_project=registration(repository),
        ),
    )


@pytest.mark.parametrize(
    "command",
    [
        "pytest;rm", "pytest&&false", "pytest & false", "pytest&false",
        "pytest$(id)", "`id`", "pytest\nid", "pytest\rid",
    ],
)
def test_architect_rejects_shell_control_in_validation_requirement(tmp_path, command):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    payload = response()
    payload["proposed_tasks"][0]["validation_requirements"] = [command]
    with pytest.raises(ArchitectPlanningError, match="shell|executable"):
        validate_architect_response(
            json.dumps(payload),
            build_architect_request(
                "Improve file:README.md", repository, assessment,
                registered_project=registration(repository),
            ),
        )


def test_architect_accepts_executable_relative_to_target(tmp_path):
    root = repo(tmp_path)
    script = root / "check.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o700)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    payload = response()
    payload["proposed_tasks"][0]["validation_requirements"] = ["./check.sh"]
    validate_architect_response(
        json.dumps(payload),
        build_architect_request(
            "Improve file:README.md", repository, assessment,
            registered_project=registration(repository),
        ),
    )


def test_unknown_only_provider_is_rejected(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a", CapabilityStatus.UNKNOWN), 0),),
        {}, now="2026-08-10T12:00:00Z", project_id=PROJECT,
        gates=GATES,
    )
    with pytest.raises(ArchitectPlanningError):
        architect.propose(build_architect_request(
            "Improve file:README.md", repository, assessment,
            registered_project=registration(repository),
        ))


def test_provider_failure_uses_next_eligible_provider(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a"), 0),
         CapabilityCandidate(profile("provider-b"), 1)),
        {"provider-a": FailingProvider(), "provider-b": FakeProvider()},
        now="2026-08-10T12:00:00Z", project_id=PROJECT, gates=GATES,
    )
    request = build_architect_request(
        "Improve file:README.md", repository, assessment,
        registered_project=registration(repository),
    )
    assert architect.propose(request) is not None
    assert architect.provider_selection["provider_id"] == "provider-b"
    assert architect.provider_selection["fallback_used"] is True


def test_provider_failure_respects_fallback_gate(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a"), 0),
         CapabilityCandidate(profile("provider-b"), 1)),
        {"provider-a": FailingProvider(), "provider-b": FakeProvider()},
        now="2026-08-10T12:00:00Z", project_id=PROJECT,
        gates=replace(GATES, allow_fallback=False),
    )
    request = build_architect_request(
        "Improve file:README.md", repository, assess(repository, PROJECT),
        registered_project=registration(repository),
    )
    with pytest.raises(ArchitectPlanningError, match="fallback is not permitted"):
        architect.propose(request)
    assert architect.provider_selection["status"] == "BLOCKED"


def test_provider_request_state_isolated_between_proposals(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a"), 0),),
        {"provider-a": FakeProvider()}, now="2026-08-10T12:00:00Z",
        project_id=PROJECT, gates=GATES,
    )
    first = build_architect_request(
        "Improve file:README.md", repository, assessment,
        registered_project=registration(repository),
    )
    second = build_architect_request(
        "Assess file:README.md", repository, assessment,
        registered_project=registration(repository),
    )
    architect.propose(first)
    architect.propose(second)
    assert len(architect.attempts) == 1
    assert architect.attempts[0]["request_hash"] == second.request_hash


def test_provider_evidence_reconstructs_fallback_and_rejects_tampering(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    candidates = (
        CapabilityCandidate(profile("provider-a"), 0),
        CapabilityCandidate(profile("provider-b"), 1),
    )
    architect = ProviderArchitect(
        candidates, {"provider-a": FailingProvider(), "provider-b": FakeProvider()},
        now="2026-08-10T12:00:00Z", project_id=PROJECT, gates=GATES,
    )
    request = build_architect_request(
        "Improve file:README.md", repository, assessment,
        registered_project=registration(repository),
    )
    architect.propose(request)
    selection = {
        **architect.provider_selection,
        "architect_request_hash": request.request_hash,
    }
    evidence = provider_evidence_payload(
        architect, request, session_id="session-test", plan_path="plan.json",
        plan_hash="a" * 64, target_sha=repository.head_sha,
    )
    assert evidence["source"] == "adapter"
    assert evidence["evidence_kind"] == "observation"
    assert evidence["attestation"] == "unavailable"
    persisted_selection = json.loads(json.dumps(selection))
    verify_provider_evidence(
        json.loads(json.dumps(evidence)), persisted_selection,
        request=request, session_id="session-test",
        plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
        now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
        authoritative_gates=GATES,
    )
    with pytest.raises(ArchitectPlanningError, match="RETRY_REQUIRED"):
        validate_provider_selection_evidence(
            persisted_selection, project_id=PROJECT, now="2026-08-10T12:00:00Z",
            authoritative_candidates=candidates, authoritative_gates=GATES,
        )
    tampered = json.loads(json.dumps(evidence))
    tampered["attempts"][0]["provider_id"] = "provider-b"
    with pytest.raises(ArchitectPlanningError):
        verify_provider_evidence(
            tampered, persisted_selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
            authoritative_gates=GATES,
        )
    duplicate = json.loads(json.dumps(evidence))
    duplicate["attempts"].append(dict(duplicate["attempts"][0]))
    duplicate["attempts"][-1]["sequence"] = 2
    with pytest.raises(ArchitectPlanningError):
        verify_provider_evidence(
            duplicate, persisted_selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
            authoritative_gates=GATES,
        )
    bad_outcome = json.loads(json.dumps(evidence))
    bad_outcome["selection_audit"][0]["outcome"] = "INVALID_PROVIDER_OUTPUT"
    with pytest.raises(ArchitectPlanningError):
        verify_provider_evidence(
            bad_outcome, persisted_selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
            authoritative_gates=GATES,
        )
    bad_type = json.loads(json.dumps(evidence))
    bad_type["selection_audit"][0]["type"] = "decision"
    with pytest.raises(ArchitectPlanningError):
        verify_provider_evidence(
            bad_type, persisted_selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
            authoritative_gates=GATES,
        )
    truncated = json.loads(json.dumps(evidence))
    truncated["attempts"] = truncated["attempts"][:1]
    with pytest.raises(ArchitectPlanningError):
        verify_provider_evidence(
            truncated, persisted_selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z", authoritative_candidates=candidates,
            authoritative_gates=GATES,
        )


@pytest.mark.parametrize("field,value", [
    ("source", "provider"),
    ("evidence_kind", "attestation"),
    ("attestation", "verified"),
])
def test_provider_evidence_cannot_upgrade_adapter_observation(tmp_path, field, value):
    root = repo(tmp_path)
    repository = context(root)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a"), 0),),
        {"provider-a": FakeProvider()}, now="2026-08-10T12:00:00Z",
        project_id=PROJECT, gates=GATES,
    )
    request = build_architect_request(
        "Improve file:README.md", repository, assess(repository, PROJECT),
        registered_project=registration(repository),
    )
    architect.propose(request)
    selection = {**architect.provider_selection, "architect_request_hash": request.request_hash}
    evidence = provider_evidence_payload(
        architect, request, session_id="session-test", plan_path="plan.json",
        plan_hash="a" * 64, target_sha=repository.head_sha,
    )
    evidence[field] = value
    with pytest.raises(ArchitectPlanningError, match="trust boundary"):
        verify_provider_evidence(
            evidence, selection, request=request, session_id="session-test",
            plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
            now="2026-08-10T12:00:00Z",
            authoritative_candidates=(CapabilityCandidate(profile("provider-a"), 0),),
            authoritative_gates=GATES,
        )


def test_exhausted_provider_attempts_persist_blocked_state(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a"), 0),),
        {"provider-a": FailingProvider()}, now="2026-08-10T12:00:00Z",
        project_id=PROJECT, gates=GATES,
    )
    request = build_architect_request(
        "Improve file:README.md", repository, assessment,
        registered_project=registration(repository),
    )
    with pytest.raises(ArchitectPlanningError):
        architect.propose(request)
    assert architect.provider_selection["status"] == "BLOCKED"
    assert architect.provider_selection["selection_audit"][0]["attempt_id"]
    assert architect.provider_selection["selection_audit"][0]["outcome"] == (
        "TRANSPORT_FAILURE"
    )


def test_no_eligible_provider_reconstructs_blocked_without_attempts(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    architect = ProviderArchitect(
        (CapabilityCandidate(profile("provider-a", CapabilityStatus.UNKNOWN), 0),),
        {}, now="2026-08-10T12:00:00Z", project_id=PROJECT, gates=GATES,
    )
    request = build_architect_request(
        "Assess", repository, assessment, registered_project=registration(repository)
    )
    with pytest.raises(ArchitectPlanningError):
        architect.propose(request)
    selection = {
        **architect.provider_selection,
        "architect_request_hash": request.request_hash,
    }
    evidence = provider_evidence_payload(
        architect, request, session_id="session-test", plan_path="plan.json",
        plan_hash="a" * 64, target_sha=repository.head_sha, selection=selection,
    )
    selection = json.loads(json.dumps(selection))
    verify_provider_evidence(
        json.loads(json.dumps(evidence)), selection,
        request=request, session_id="session-test",
        plan_path="plan.json", plan_hash="a" * 64, target_sha=repository.head_sha,
        now="2026-08-10T12:00:00Z",
        authoritative_candidates=(
            CapabilityCandidate(profile("provider-a", CapabilityStatus.UNKNOWN), 0),
        ),
        authoritative_gates=GATES,
    )


def test_malformed_response_and_no_work_are_deterministic(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    request = build_architect_request(
        "Assess", repository, assessment, registered_project=registration(repository)
    )
    assert request.to_dict()["request_hash"] == request.request_hash
    with pytest.raises(ArchitectPlanningError):
        validate_architect_response("not json", request)
    no_work = response()
    no_work.update({
        "proposed_outcome": "NO_JUSTIFIED_WORK",
        "proposed_tasks": [],
        "evidence_references": ["README.md"],
    })
    assert validate_architect_response(no_work, request) is None


def test_malformed_structured_response_is_rejected_strictly(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    request = build_architect_request(
        "Assess", repository, assess(repository, PROJECT),
        registered_project=registration(repository),
    )
    malformed = response()
    malformed["confidence"] = True
    with pytest.raises(ArchitectPlanningError, match="confidence"):
        validate_architect_response(malformed, request)
    malformed = response()
    malformed["proposed_tasks"][0]["allowed_paths"] = ["README.md", 7]
    with pytest.raises(ArchitectPlanningError, match="list fields"):
        validate_architect_response(malformed, request)


def test_provider_schema_matches_validator_fields_and_semantics():
    schema = architect_response_schema()
    assert set(schema["properties"]) == {
        "assessment_summary", "proposed_outcome", "rationale", "confidence",
        "proposed_tasks", "architecture_implications", "preliminary_risk_indicators",
        "evidence_references", "unresolved_unknowns",
    }
    assert schema["properties"]["proposed_tasks"]["items"]["required"]
    # The native Codex schema subset rejects conditional allOf/if/then
    # constructs.  These outcome-dependent invariants remain enforced by
    # validate_architect_response after the provider returns its object.
    assert "allOf" not in schema


def test_provider_schema_is_native_codex_compatible():
    schema = architect_response_schema()
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert '"if"' not in serialized
    assert '"then"' not in serialized


def test_architect_provider_prompt_binds_closed_evidence_inventory(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    request = build_architect_request(
        "Assess", repository, assessment, registered_project=registration(repository)
    )
    instruction = _AdapterArchitectProvider._instruction(request)
    assert "closed evidence inventory" in instruction
    assert assessment.evidence_hash in instruction
    assert "Do not cite any other string" in instruction


def test_architect_provider_prompt_requires_exact_validation_commands(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    request = build_architect_request(
        "Assess", repository, assessment, registered_project=registration(repository)
    )
    instruction = _AdapterArchitectProvider._instruction(request)
    assert "exact executable command strings" in instruction
    assert "Never write prose such as 'Run the tests'" in instruction


def test_architect_request_requires_exact_registered_repository_binding(tmp_path):
    root = repo(tmp_path)
    repository = context(root)
    assessment = assess(repository, PROJECT)
    wrong = registration(repository)
    wrong.repository_root = str(root / "other")
    with pytest.raises(ArchitectPlanningError, match="repository root"):
        build_architect_request(
            "Assess", repository, assessment, registered_project=wrong
        )
