from __future__ import annotations

from dataclasses import replace

import pytest

from agf_orchestrator.capability_extensions import (
    CandidateStatus,
    CapabilityExtensionError,
    CheckStatus,
    IntegrationStability,
    InvocationPolicy,
    KnowledgeMutability,
    KnowledgeProviderProfile,
    KnowledgeTransport,
    PrivacyClassification,
    ProcedureProfile,
    ProcedureSelection,
    ToolCandidate,
    VerificationCheck,
    knowledge_provider_profile_from_dict,
    procedure_profile_from_dict,
    procedure_selection_from_dict,
    seal,
    tool_candidate_from_dict,
)
from agf_orchestrator.risk_models import RiskLevel


NOW = "2026-08-24T09:00:00Z"
LATER = "2026-08-25T09:00:00Z"


def procedure() -> ProcedureProfile:
    return seal(
        ProcedureProfile(
            schema_version="1.0",
            procedure_id="procedure-ci-repair",
            project_id="project-demo",
            profile_version=1,
            capabilities=("ci-repair", "repository-understanding"),
            max_risk=RiskLevel.MEDIUM,
            allowed_paths=("src/**", "tests/**"),
            provider_requirements=("structured-output",),
            required_evidence=("tests", "review"),
            invocation_policy=InvocationPolicy.AGF_SELECTABLE,
            provenance_source="local procedure registry",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def selection(profile: ProcedureProfile) -> ProcedureSelection:
    return seal(
        ProcedureSelection(
            schema_version="1.0",
            selection_id="selection-ci-repair",
            project_id="project-demo",
            session_id="session-demo",
            procedure_id=profile.procedure_id,
            procedure_profile_sha256=profile.profile_sha256,
            required_capabilities=("ci-repair",),
            selected_at=NOW,
            selection_sha256="",
        )
    )


def checks(status: CheckStatus = CheckStatus.PASS) -> tuple[VerificationCheck, ...]:
    return tuple(
        VerificationCheck(name=name, status=status, evidence_ref=f"evidence:{name}")
        for name in (
            "official_documentation",
            "authentication",
            "limits",
            "license",
            "privacy",
            "stability",
            "policy",
        )
    )


def tool(status: CandidateStatus = CandidateStatus.VERIFIED) -> ToolCandidate:
    return seal(
        ToolCandidate(
            schema_version="1.0",
            candidate_id="candidate-weather-api",
            project_id="project-demo",
            capability="weather-data",
            endpoint_label="Example weather service",
            catalog_source="fixture public API catalog",
            status=status,
            checks=checks() if status is CandidateStatus.VERIFIED else (),
            observed_at=NOW,
            candidate_sha256="",
        )
    )


def knowledge() -> KnowledgeProviderProfile:
    return seal(
        KnowledgeProviderProfile(
            schema_version="1.0",
            knowledge_provider_id="knowledge-research",
            project_id="project-demo",
            profile_version=1,
            transport=KnowledgeTransport.STDIO,
            capabilities=("grounded-research", "citations"),
            requires_credentials=True,
            requires_authenticated_session=True,
            network_required=True,
            browser_automation=True,
            privacy_classification=PrivacyClassification.EXTERNAL_PRIVATE,
            privacy_review_required=True,
            mutability=KnowledgeMutability.MIXED,
            stability=IntegrationStability.UNOFFICIAL,
            provenance_source="fixture MCP descriptor",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def test_procedure_is_deterministic_and_round_trips() -> None:
    profile = procedure()
    profile.validate(now=NOW)
    assert profile == procedure_profile_from_dict(profile.to_dict())
    assert profile.profile_sha256 == procedure().profile_sha256


def test_procedure_rejects_duplicate_capabilities_and_secret_text() -> None:
    profile = procedure()
    duplicate = seal(replace(profile, capabilities=("ci-repair", "ci-repair"), profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="unique"):
        duplicate.validate()
    secret = seal(replace(profile, provenance_source="api_key=abcdefghijklmnop", profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="secret-shaped"):
        secret.validate()


def test_procedure_rejects_stale_and_parent_traversal() -> None:
    profile = procedure()
    with pytest.raises(CapabilityExtensionError, match="stale"):
        profile.validate(now="2026-08-26T09:00:00Z")
    unsafe = seal(replace(profile, allowed_paths=("../src/**",), profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="traverse"):
        unsafe.validate()


def test_selection_binds_exact_profile_and_capabilities() -> None:
    profile = procedure()
    chosen = selection(profile)
    chosen.validate(profile)
    assert chosen == procedure_selection_from_dict(chosen.to_dict())
    wrong = replace(profile, project_id="project-other")
    wrong = seal(replace(wrong, profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="binding"):
        chosen.validate(wrong)


def test_selection_rejects_capability_not_declared_by_procedure() -> None:
    profile = procedure()
    chosen = seal(
        replace(
            selection(profile),
            required_capabilities=("database-migration",),
            selection_sha256="",
        )
    )
    with pytest.raises(CapabilityExtensionError, match="does not satisfy"):
        chosen.validate(profile)


def test_unverified_tool_candidate_is_never_usable() -> None:
    candidate = tool(CandidateStatus.UNVERIFIED)
    candidate.validate()
    with pytest.raises(CapabilityExtensionError, match="not verified"):
        candidate.require_verified()


def test_verified_tool_requires_all_mandatory_checks() -> None:
    candidate = tool()
    candidate.require_verified()
    assert candidate == tool_candidate_from_dict(candidate.to_dict())
    incomplete = seal(replace(candidate, checks=checks()[:-1], candidate_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="mandatory checks"):
        incomplete.validate()
    unknown_checks = tuple(
        replace(item, status=CheckStatus.UNKNOWN) if item.name == "privacy" else item
        for item in checks()
    )
    unknown = seal(replace(candidate, checks=unknown_checks, candidate_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="mandatory checks"):
        unknown.validate()


def test_knowledge_provider_round_trips_and_stales_fail_closed() -> None:
    profile = knowledge()
    profile.validate(now=NOW)
    assert profile == knowledge_provider_profile_from_dict(profile.to_dict())
    with pytest.raises(CapabilityExtensionError, match="stale"):
        profile.validate(now="2026-08-26T09:00:00Z")


def test_knowledge_provider_requires_known_transport_and_mutability() -> None:
    profile = knowledge()
    bad_transport = seal(replace(profile, transport=KnowledgeTransport.UNKNOWN, profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="transport must be known"):
        bad_transport.validate()
    bad_mutability = seal(replace(profile, mutability=KnowledgeMutability.UNKNOWN, profile_sha256=""))
    with pytest.raises(CapabilityExtensionError, match="mutability must be known"):
        bad_mutability.validate()


def test_unknown_fields_are_rejected() -> None:
    payload = procedure().to_dict()
    payload["authority"] = "granted"
    with pytest.raises(CapabilityExtensionError, match="unknown fields"):
        procedure_profile_from_dict(payload)
