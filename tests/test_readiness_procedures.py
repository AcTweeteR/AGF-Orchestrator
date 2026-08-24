from dataclasses import replace

import pytest

from agf_orchestrator.capability_extensions import InvocationPolicy, ProcedureProfile, seal
from agf_orchestrator.loop_patterns import PATTERNS, get_pattern
from agf_orchestrator.procedure_registry import (
    ProcedureRegistry,
    ProcedureRegistryError,
    ProcedureRequirements,
)
from agf_orchestrator.readiness import REQUIRED_READINESS_CHECKS, doctor, evaluate_readiness
from agf_orchestrator.risk_models import RiskLevel

NOW = "2026-08-24T09:00:00Z"
LATER = "2026-08-25T09:00:00Z"


def profile(
    procedure_id: str = "procedure-ci-repair",
    *,
    version: int = 1,
    invocation_policy: InvocationPolicy = InvocationPolicy.AGF_SELECTABLE,
) -> ProcedureProfile:
    return seal(
        ProcedureProfile(
            schema_version="1.0",
            procedure_id=procedure_id,
            project_id="project-demo",
            profile_version=version,
            capabilities=("ci-repair", "repository-understanding"),
            max_risk=RiskLevel.MEDIUM,
            allowed_paths=("src/**", "tests/**"),
            provider_requirements=("structured-output",),
            required_evidence=("tests", "review"),
            invocation_policy=invocation_policy,
            provenance_source="test procedure",
            observed_at=NOW,
            expires_at=LATER,
            profile_sha256="",
        )
    )


def requirements() -> ProcedureRequirements:
    return ProcedureRequirements(
        capabilities=("ci-repair",),
        risk=RiskLevel.MEDIUM,
        requested_paths=("src/example.py",),
        provider_capabilities=("structured-output",),
    )


def test_registry_is_project_isolated_and_selects_eligible_procedure(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    stored = registry.put(profile())
    assert stored.parent.name == "project-demo"
    selected = registry.select("project-demo", requirements(), now=NOW)
    assert selected.procedure_id == "procedure-ci-repair"
    assert registry.list("project-other", now=NOW) == ()


def test_registry_rejects_explicit_only_risk_and_paths(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    registry.put(profile(invocation_policy=InvocationPolicy.EXPLICIT_ONLY))
    with pytest.raises(ProcedureRegistryError, match="no eligible"):
        registry.select("project-demo", requirements(), now=NOW)

    eligible = profile(invocation_policy=InvocationPolicy.AGF_SELECTABLE)
    registry.put(eligible)
    too_risky = replace(requirements(), risk=RiskLevel.HIGH)
    with pytest.raises(ProcedureRegistryError, match="no eligible"):
        registry.select("project-demo", too_risky, now=NOW)
    outside = replace(requirements(), requested_paths=("docs/escape.md",))
    with pytest.raises(ProcedureRegistryError, match="no eligible"):
        registry.select("project-demo", outside, now=NOW)


def test_registry_fails_closed_on_stale_or_ambiguous_evidence(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    registry.put(profile())
    with pytest.raises(ProcedureRegistryError, match="invalid"):
        registry.list("project-demo", now="2026-08-26T09:00:00Z")

    registry = ProcedureRegistry(tmp_path / "ambiguous")
    registry.put(profile("procedure-ci-repair-a"))
    registry.put(profile("procedure-ci-repair-b"))
    with pytest.raises(ProcedureRegistryError, match="ambiguous"):
        registry.select("project-demo", requirements(), now=NOW)


def test_newer_profile_version_wins_when_other_scope_is_equal(tmp_path) -> None:
    registry = ProcedureRegistry(tmp_path)
    registry.put(profile("procedure-ci-repair-old", version=1))
    registry.put(profile("procedure-ci-repair-new", version=2))
    selected = registry.select("project-demo", requirements(), now=NOW)
    assert selected.procedure_id == "procedure-ci-repair-new"


def test_readiness_requires_every_blocking_evidence_item() -> None:
    evidence = {name: True for name in REQUIRED_READINESS_CHECKS}
    report = evaluate_readiness(evidence)
    assert report.ready is True
    assert report.informational_score == 100
    assert report.blockers == ()

    evidence["budget_available"] = False
    report = evaluate_readiness(evidence)
    assert report.ready is False
    assert [item.name for item in report.blockers] == ["budget_available"]


def test_readiness_unknown_fails_closed_and_score_never_overrides() -> None:
    evidence = {name: True for name in REQUIRED_READINESS_CHECKS}
    evidence["kill_switch_clear"] = None
    report = evaluate_readiness(evidence)
    assert report.ready is False
    assert report.informational_score == 90
    assert report.blockers[0].status.value == "UNKNOWN"


def test_doctor_is_observational_only() -> None:
    evidence = {name: True for name in REQUIRED_READINESS_CHECKS}
    evidence["provider_eligible"] = False
    result = doctor(evidence)
    assert result["authority_effect"] == "NONE"
    assert result["ready"] is False
    assert result["remediation"] == ["resolve:provider_eligible"]
    assert evidence["provider_eligible"] is False


def test_loop_patterns_never_grant_merge_or_ignore_kill_switch() -> None:
    assert set(PATTERNS) == {
        "ci-repair",
        "dependency-update",
        "issue-triage",
        "pr-babysitter",
        "release-prep",
    }
    for pattern in PATTERNS.values():
        assert pattern.external_mutation is False
        assert pattern.auto_merge is False
        assert pattern.respects_kill_switch is True
        assert pattern.finite_progress_required is True
    assert get_pattern("ci-repair").required_capabilities[0] == "repository-understanding"
    with pytest.raises(ValueError, match="unknown"):
        get_pattern("auto-merge-everything")
