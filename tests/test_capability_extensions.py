from __future__ import annotations

from dataclasses import replace

import pytest

import agf_orchestrator.capability_extensions as ext
from agf_orchestrator.risk_models import RiskLevel

NOW = "2026-08-24T09:00:00Z"
LATER = "2026-08-25T09:00:00Z"


def procedure() -> ext.ProcedureProfile:
    return ext.seal(
        ext.ProcedureProfile(
            schema_version="1.0",
            procedure_id="procedure-ci-repair",
            project_id="project-demo",
            profile_version=1,
            capabilities=("ci-repair", "repository-understanding"),
            max_risk=RiskLevel.MEDIUM,
            allowed_paths=("src/**", "tests/**"),
            provider_requirements=("structured-output",),
            required_evidence=("tests", "review"),
            invocation_policy=ext.InvocationPolicy.AGF_SELECTABLE,
            provenance_source="local procedure registry",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def selection(profile: ext.ProcedureProfile) -> ext.ProcedureSelection:
    return ext.seal(
        ext.ProcedureSelection(
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


def checks(
    status: ext.CheckStatus = ext.CheckStatus.PASS,
) -> tuple[ext.VerificationCheck, ...]:
    return tuple(
        ext.VerificationCheck(name=name, status=status, evidence_ref=f"evidence:{name}")
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


def tool(
    status: ext.CandidateStatus = ext.CandidateStatus.VERIFIED,
) -> ext.ToolCandidate:
    return ext.seal(
        ext.ToolCandidate(
            schema_version="1.0",
            candidate_id="candidate-weather-api",
            project_id="project-demo",
            capability="weather-data",
            endpoint_label="Example weather service",
            catalog_source="fixture public API catalog",
            status=status,
            checks=checks() if status is ext.CandidateStatus.VERIFIED else (),
            observed_at=NOW,
            candidate_sha256="",
        )
    )


def knowledge() -> ext.KnowledgeProviderProfile:
    return ext.seal(
        ext.KnowledgeProviderProfile(
            schema_version="1.0",
            knowledge_provider_id="knowledge-research",
            project_id="project-demo",
            profile_version=1,
            transport=ext.KnowledgeTransport.STDIO,
            capabilities=("grounded-research", "citations"),
            requires_credentials=True,
            requires_authenticated_session=True,
            network_required=True,
            browser_automation=True,
            privacy_classification=ext.PrivacyClassification.EXTERNAL_PRIVATE,
            privacy_review_required=True,
            mutability=ext.KnowledgeMutability.MIXED,
            stability=ext.IntegrationStability.UNOFFICIAL,
            provenance_source="fixture MCP descriptor",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def test_procedure_is_deterministic_and_round_trips() -> None:
    profile = procedure()
    profile.validate(now=NOW)
    assert profile == ext.procedure_profile_from_dict(profile.to_dict())
    assert profile.profile_sha256 == procedure().profile_sha256


def test_procedure_rejects_duplicate_capabilities_and_secret_text() -> None:
    profile = procedure()
    duplicate = ext.seal(
        replace(
            profile,
            capabilities=("ci-repair", "ci-repair"),
            profile_sha256="",
        )
    )
    with pytest.raises(ext.CapabilityExtensionError, match="unique"):
        duplicate.validate()
    secret = ext.seal(
        replace(
            profile,
            provenance_source="api_key=abcdefghijklmnop",
            profile_sha256="",
        )
    )
    with pytest.raises(ext.CapabilityExtensionError, match="secret-shaped"):
        secret.validate()


def test_procedure_rejects_stale_and_parent_traversal() -> None:
    profile = procedure()
    with pytest.raises(ext.CapabilityExtensionError, match="stale"):
        profile.validate(now="2026-08-26T09:00:00Z")
    unsafe = ext.seal(
        replace(profile, allowed_paths=("../src/**",), profile_sha256="")
    )
    with pytest.raises(ext.CapabilityExtensionError, match="traverse"):
        unsafe.validate()


def test_selection_binds_exact_profile_and_capabilities() -> None:
    profile = procedure()
    chosen = selection(profile)
    chosen.validate(profile)
    assert chosen == ext.procedure_selection_from_dict(chosen.to_dict())
    wrong = ext.seal(
        replace(profile, project_id="project-other", profile_sha256="")
    )
    with pytest.raises(ext.CapabilityExtensionError, match="binding"):
        chosen.validate(wrong)


def test_selection_rejects_capability_not_declared_by_procedure() -> None:
    profile = procedure()
    chosen = ext.seal(
        replace(
            selection(profile),
            required_capabilities=("database-migration",),
            selection_sha256="",
        )
    )
    with pytest.raises(ext.CapabilityExtensionError, match="does not satisfy"):
        chosen.validate(profile)


def test_unverified_tool_candidate_is_never_usable() -> None:
    candidate = tool(ext.CandidateStatus.UNVERIFIED)
    candidate.validate()
    with pytest.raises(ext.CapabilityExtensionError, match="not verified"):
        candidate.require_verified()


def test_verified_tool_requires_all_mandatory_checks() -> None:
    candidate = tool()
    candidate.require_verified()
    assert candidate == ext.tool_candidate_from_dict(candidate.to_dict())
    incomplete = ext.seal(
        replace(candidate, checks=checks()[:-1], candidate_sha256="")
    )
    with pytest.raises(ext.CapabilityExtensionError, match="mandatory checks"):
        incomplete.validate()
    unknown_checks = tuple(
        replace(item, status=ext.CheckStatus.UNKNOWN)
        if item.name == "privacy"
        else item
        for item in checks()
    )
    unknown = ext.seal(
        replace(candidate, checks=unknown_checks, candidate_sha256="")
    )
    with pytest.raises(ext.CapabilityExtensionError, match="mandatory checks"):
        unknown.validate()


def test_knowledge_provider_round_trips_and_stales_fail_closed() -> None:
    profile = knowledge()
    profile.validate(now=NOW)
    parsed = ext.knowledge_provider_profile_from_dict(profile.to_dict())
    assert profile == parsed
    with pytest.raises(ext.CapabilityExtensionError, match="stale"):
        profile.validate(now="2026-08-26T09:00:00Z")


def test_knowledge_provider_requires_known_transport_and_mutability() -> None:
    profile = knowledge()
    bad_transport = ext.seal(
        replace(
            profile,
            transport=ext.KnowledgeTransport.UNKNOWN,
            profile_sha256="",
        )
    )
    with pytest.raises(ext.CapabilityExtensionError, match="transport must be known"):
        bad_transport.validate()
    bad_mutability = ext.seal(
        replace(
            profile,
            mutability=ext.KnowledgeMutability.UNKNOWN,
            profile_sha256="",
        )
    )
    with pytest.raises(ext.CapabilityExtensionError, match="mutability must be known"):
        bad_mutability.validate()


def test_unknown_fields_are_rejected() -> None:
    payload = procedure().to_dict()
    payload["authority"] = "granted"
    with pytest.raises(ext.CapabilityExtensionError, match="unknown fields"):
        ext.procedure_profile_from_dict(payload)
