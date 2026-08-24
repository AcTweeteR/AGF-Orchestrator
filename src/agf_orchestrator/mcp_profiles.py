"""Provider-neutral MCP knowledge profiles and fail-closed eligibility checks."""

from __future__ import annotations

from dataclasses import dataclass

from .capability_extensions import (
    CapabilityExtensionError,
    IntegrationStability,
    KnowledgeMutability,
    KnowledgeProviderProfile,
    KnowledgeTransport,
    PrivacyClassification,
    seal,
)


@dataclass(frozen=True)
class KnowledgeEligibility:
    eligible: bool
    reason: str
    authority_effect: str = "NONE"


def notebooklm_mcp_profile(
    project_id: str,
    *,
    observed_at: str,
    expires_at: str,
    transport: KnowledgeTransport = KnowledgeTransport.STDIO,
) -> KnowledgeProviderProfile:
    """Build the optional, explicitly untrusted NotebookLM MCP profile."""
    profile = seal(
        KnowledgeProviderProfile(
            schema_version="1.0",
            knowledge_provider_id="knowledge-notebooklm-mcp",
            project_id=project_id,
            profile_version=1,
            transport=transport,
            capabilities=("grounded-research", "citations", "source-management"),
            requires_credentials=True,
            requires_authenticated_session=True,
            network_required=True,
            browser_automation=True,
            privacy_classification=PrivacyClassification.EXTERNAL_PRIVATE,
            privacy_review_required=True,
            mutability=KnowledgeMutability.MIXED,
            stability=IntegrationStability.UNOFFICIAL,
            provenance_source="optional NotebookLM MCP integration profile",
            observed_at=observed_at,
            expires_at=expires_at,
            profile_sha256="",
        )
    )
    profile.validate(now=observed_at)
    return profile


def knowledge_provider_eligibility(
    profile: KnowledgeProviderProfile,
    *,
    now: str,
    available: bool | None,
    authenticated: bool | None,
    policy_authorized: bool | None,
    privacy_eligible: bool | None,
) -> KnowledgeEligibility:
    """Evaluate provider use without granting any downstream action authority."""
    try:
        profile.validate(now=now)
    except CapabilityExtensionError as exc:
        return KnowledgeEligibility(False, f"invalid-or-stale-profile:{exc}")
    checks = (
        (available, "provider-unavailable-or-unknown"),
        (authenticated, "authentication-unavailable-or-unknown"),
        (policy_authorized, "policy-not-authorized-or-unknown"),
    )
    for value, reason in checks:
        if value is not True:
            return KnowledgeEligibility(False, reason)
    if profile.privacy_review_required and privacy_eligible is not True:
        return KnowledgeEligibility(False, "privacy-not-eligible-or-unknown")
    return KnowledgeEligibility(True, "eligible-as-optional-knowledge-provider")


def external_upload_eligibility(
    profile: KnowledgeProviderProfile,
    *,
    now: str,
    available: bool | None,
    authenticated: bool | None,
    policy_authorized: bool | None,
    privacy_eligible: bool | None,
    upload_authorized: bool | None,
) -> KnowledgeEligibility:
    """Require explicit upload authorization in addition to provider eligibility."""
    base = knowledge_provider_eligibility(
        profile,
        now=now,
        available=available,
        authenticated=authenticated,
        policy_authorized=policy_authorized,
        privacy_eligible=privacy_eligible,
    )
    if not base.eligible:
        return base
    if upload_authorized is not True:
        return KnowledgeEligibility(False, "external-upload-not-explicitly-authorized")
    return KnowledgeEligibility(True, "eligible-for-explicitly-authorized-upload")
