"""Deterministic disposable pilot for E12 governed capability extensions."""

from __future__ import annotations

from dataclasses import dataclass

from .capability_extensions import CapabilityExtensionError, KnowledgeProviderProfile, ToolCandidate
from .fleet_observability import BudgetObservation
from .mcp_profiles import knowledge_provider_eligibility
from .procedure_registry import ProcedureRegistry, ProcedureRegistryError, ProcedureRequirements
from .readiness import evaluate_readiness


@dataclass(frozen=True)
class ExtensionPilotResult:
    status: str
    selected_procedure_id: str | None
    blocker: str | None
    external_mutation_performed: bool = False


def run_disposable_extension_pilot(
    *,
    project_id: str,
    registry: ProcedureRegistry,
    procedure_requirements: ProcedureRequirements,
    readiness_evidence: dict[str, bool | None],
    now: str,
    budget: BudgetObservation,
    tool_candidate: ToolCandidate | None = None,
    tool_required: bool = False,
    knowledge_profile: KnowledgeProviderProfile | None = None,
    knowledge_required: bool = False,
    knowledge_available: bool | None = None,
    knowledge_authenticated: bool | None = None,
    knowledge_policy_authorized: bool | None = None,
    knowledge_privacy_eligible: bool | None = None,
) -> ExtensionPilotResult:
    """Exercise extension gates without invoking a provider or mutating an external system."""
    readiness = evaluate_readiness(readiness_evidence)
    if not readiness.ready:
        return ExtensionPilotResult("BLOCKED", None, "mission-readiness")
    if budget.kill_switch_active:
        return ExtensionPilotResult("BLOCKED", None, "kill-switch")
    if not budget.within_budget:
        return ExtensionPilotResult("BLOCKED", None, "budget-exhausted")
    try:
        procedure = registry.select(project_id, procedure_requirements, now=now)
    except ProcedureRegistryError as exc:
        return ExtensionPilotResult("BLOCKED", None, f"procedure:{exc}")
    if tool_required:
        if tool_candidate is None:
            return ExtensionPilotResult("BLOCKED", procedure.procedure_id, "tool-unavailable")
        try:
            tool_candidate.require_verified()
        except CapabilityExtensionError as exc:
            return ExtensionPilotResult(
                "BLOCKED",
                procedure.procedure_id,
                f"tool:{exc}",
            )
    if knowledge_required:
        if knowledge_profile is None:
            return ExtensionPilotResult(
                "BLOCKED",
                procedure.procedure_id,
                "knowledge-provider-unavailable",
            )
        eligibility = knowledge_provider_eligibility(
            knowledge_profile,
            now=now,
            available=knowledge_available,
            authenticated=knowledge_authenticated,
            policy_authorized=knowledge_policy_authorized,
            privacy_eligible=knowledge_privacy_eligible,
        )
        if not eligibility.eligible:
            return ExtensionPilotResult(
                "BLOCKED",
                procedure.procedure_id,
                f"knowledge:{eligibility.reason}",
            )
    return ExtensionPilotResult("READY_FOR_EXECUTION", procedure.procedure_id, None)
