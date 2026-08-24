from decimal import Decimal

import pytest

from agf_orchestrator.capability_extensions import (
    CandidateStatus,
    CheckStatus,
    ProcedureProfile,
    ToolCandidate,
    VerificationCheck,
    seal,
)
from agf_orchestrator.catalog_adapters import CatalogAdapterError, public_apis_candidates
from agf_orchestrator.extension_pilot import run_disposable_extension_pilot
from agf_orchestrator.fleet_observability import (
    FleetObservabilityError,
    observe_budget,
    rank_eligible_by_cost,
)
from agf_orchestrator.mcp_profiles import (
    external_upload_eligibility,
    knowledge_provider_eligibility,
    notebooklm_mcp_profile,
)
from agf_orchestrator.procedure_registry import ProcedureRegistry, ProcedureRequirements
from agf_orchestrator.readiness import REQUIRED_READINESS_CHECKS
from agf_orchestrator.risk_models import RiskLevel

NOW = "2026-08-24T09:00:00Z"
LATER = "2026-08-25T09:00:00Z"


def procedure() -> ProcedureProfile:
    from agf_orchestrator.capability_extensions import InvocationPolicy

    return seal(
        ProcedureProfile(
            schema_version="1.0",
            procedure_id="procedure-research",
            project_id="project-demo",
            profile_version=1,
            capabilities=("research", "repository-understanding"),
            max_risk=RiskLevel.MEDIUM,
            allowed_paths=("docs/**",),
            provider_requirements=("structured-output",),
            required_evidence=("tests", "review"),
            invocation_policy=InvocationPolicy.AGF_SELECTABLE,
            provenance_source="test procedure",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def requirements() -> ProcedureRequirements:
    return ProcedureRequirements(
        capabilities=("research",),
        risk=RiskLevel.LOW,
        requested_paths=("docs/report.md",),
        provider_capabilities=("structured-output",),
    )


def verified_tool() -> ToolCandidate:
    names = (
        "official_documentation",
        "authentication",
        "limits",
        "license",
        "privacy",
        "stability",
        "policy",
    )
    checks = tuple(
        VerificationCheck(name, CheckStatus.PASS, f"evidence:{name}")
        for name in names
    )
    return seal(
        ToolCandidate(
            schema_version="1.0",
            candidate_id="candidate-verified-api",
            project_id="project-demo",
            capability="research",
            endpoint_label="Verified API",
            catalog_source="independent verification fixture",
            status=CandidateStatus.VERIFIED,
            checks=checks,
            observed_at=NOW,
            candidate_sha256="",
        )
    )


def ready_evidence() -> dict[str, bool | None]:
    return {name: True for name in REQUIRED_READINESS_CHECKS}


def test_public_api_catalog_entries_remain_unverified() -> None:
    entries = [
        {
            "API": "Example Weather",
            "Description": "Weather data",
            "Category": "Weather",
            "Auth": "apiKey",
            "HTTPS": True,
            "Cors": "yes",
            "Link": "https://example.invalid",
        }
    ]
    candidates = public_apis_candidates("project-demo", entries, observed_at=NOW)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status is CandidateStatus.UNVERIFIED
    assert candidate.checks == ()
    with pytest.raises(ValueError, match="not verified"):
        candidate.require_verified()


def test_catalog_adapter_is_bounded_and_requires_core_fields() -> None:
    with pytest.raises(CatalogAdapterError, match="Category"):
        public_apis_candidates(
            "project-demo",
            [{"API": "Missing category"}],
            observed_at=NOW,
        )


def test_notebooklm_profile_is_optional_unofficial_and_privacy_sensitive() -> None:
    profile = notebooklm_mcp_profile(
        "project-demo",
        observed_at=NOW,
        expires_at=LATER,
    )
    assert profile.network_required is True
    assert profile.browser_automation is True
    assert profile.privacy_review_required is True
    assert profile.stability.value == "UNOFFICIAL"
    assert profile.mutability.value == "MIXED"

    denied = knowledge_provider_eligibility(
        profile,
        now=NOW,
        available=True,
        authenticated=True,
        policy_authorized=True,
        privacy_eligible=None,
    )
    assert denied.eligible is False
    assert "privacy" in denied.reason


def test_notebooklm_upload_requires_separate_explicit_authorization() -> None:
    profile = notebooklm_mcp_profile(
        "project-demo",
        observed_at=NOW,
        expires_at=LATER,
    )
    denied = external_upload_eligibility(
        profile,
        now=NOW,
        available=True,
        authenticated=True,
        policy_authorized=True,
        privacy_eligible=True,
        upload_authorized=False,
    )
    assert denied.eligible is False
    assert denied.authority_effect == "NONE"
    allowed = external_upload_eligibility(
        profile,
        now=NOW,
        available=True,
        authenticated=True,
        policy_authorized=True,
        privacy_eligible=True,
        upload_authorized=True,
    )
    assert allowed.eligible is True
    assert allowed.authority_effect == "NONE"


def test_budget_observation_exposes_limits_without_clearing_kill_switch() -> None:
    observation = observe_budget(
        provider_id="provider-codex",
        procedure_id="procedure-research",
        task_id="task-demo",
        budget="10.00",
        used="4.25",
        kill_switch_active=True,
    )
    assert observation.remaining == Decimal("5.75")
    assert observation.within_budget is True
    assert observation.execution_available is False
    assert observation.to_dict()["authority_effect"] == "NONE"
    with pytest.raises(FleetObservabilityError, match="non-negative"):
        observe_budget(
            provider_id="provider-codex",
            procedure_id="procedure-research",
            task_id="task-demo",
            budget="-1",
            used="0",
            kill_switch_active=False,
        )


def test_cost_ranking_only_considers_upstream_eligible_candidates() -> None:
    ranked = rank_eligible_by_cost(
        (
            ("provider-expensive", Decimal("4"), True),
            ("provider-cheap-ineligible", Decimal("1"), False),
            ("provider-cheap", Decimal("2"), True),
        )
    )
    assert ranked == ("provider-cheap", "provider-expensive")


def test_disposable_pilot_happy_path_performs_no_external_mutation(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    registry.put(procedure())
    budget = observe_budget(
        provider_id="provider-codex",
        procedure_id="procedure-research",
        task_id="task-demo",
        budget="10",
        used="1",
        kill_switch_active=False,
    )
    knowledge = notebooklm_mcp_profile(
        "project-demo",
        observed_at=NOW,
        expires_at=LATER,
    )
    result = run_disposable_extension_pilot(
        project_id="project-demo",
        registry=registry,
        procedure_requirements=requirements(),
        readiness_evidence=ready_evidence(),
        now=NOW,
        budget=budget,
        tool_candidate=verified_tool(),
        tool_required=True,
        knowledge_profile=knowledge,
        knowledge_required=True,
        knowledge_available=True,
        knowledge_authenticated=True,
        knowledge_policy_authorized=True,
        knowledge_privacy_eligible=True,
    )
    assert result.status == "READY_FOR_EXECUTION"
    assert result.selected_procedure_id == "procedure-research"
    assert result.external_mutation_performed is False


def test_disposable_pilot_canaries_fail_closed(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    registry.put(procedure())
    budget = observe_budget(
        provider_id="provider-codex",
        procedure_id="procedure-research",
        task_id="task-demo",
        budget="10",
        used="11",
        kill_switch_active=False,
    )
    result = run_disposable_extension_pilot(
        project_id="project-demo",
        registry=registry,
        procedure_requirements=requirements(),
        readiness_evidence=ready_evidence(),
        now=NOW,
        budget=budget,
    )
    assert result.blocker == "budget-exhausted"

    stopped = observe_budget(
        provider_id="provider-codex",
        procedure_id="procedure-research",
        task_id="task-demo",
        budget="10",
        used="1",
        kill_switch_active=True,
    )
    result = run_disposable_extension_pilot(
        project_id="project-demo",
        registry=registry,
        procedure_requirements=requirements(),
        readiness_evidence=ready_evidence(),
        now=NOW,
        budget=stopped,
    )
    assert result.blocker == "kill-switch"

    unverified = public_apis_candidates(
        "project-demo",
        [{"API": "Example", "Category": "Research"}],
        observed_at=NOW,
    )[0]
    normal = observe_budget(
        provider_id="provider-codex",
        procedure_id="procedure-research",
        task_id="task-demo",
        budget="10",
        used="1",
        kill_switch_active=False,
    )
    result = run_disposable_extension_pilot(
        project_id="project-demo",
        registry=registry,
        procedure_requirements=requirements(),
        readiness_evidence=ready_evidence(),
        now=NOW,
        budget=normal,
        tool_candidate=unverified,
        tool_required=True,
    )
    assert result.status == "BLOCKED"
    assert result.blocker is not None
    assert result.blocker.startswith("tool:")

    knowledge = notebooklm_mcp_profile(
        "project-demo",
        observed_at=NOW,
        expires_at=LATER,
    )
    result = run_disposable_extension_pilot(
        project_id="project-demo",
        registry=registry,
        procedure_requirements=requirements(),
        readiness_evidence=ready_evidence(),
        now=NOW,
        budget=normal,
        knowledge_profile=knowledge,
        knowledge_required=True,
        knowledge_available=True,
        knowledge_authenticated=True,
        knowledge_policy_authorized=True,
        knowledge_privacy_eligible=False,
    )
    assert result.status == "BLOCKED"
    assert result.blocker is not None
    assert "privacy" in result.blocker
