"""Canonical, owner-verified provider eligibility.

This module is a projection of the existing owner-authenticated
``provider_intelligence`` authority component.  It is deliberately not a
second registry, policy engine, or signing mechanism.  A decision is trusted
only after the source state has been loaded and verified again from the
``ProviderIntelligenceStore``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from .capability_extensions import CapabilityExtensionError, KnowledgeProviderProfile
from .capability_profiles import CapabilityProfileError
from .capability_selection import CapabilityCandidate, CapabilitySelector, SelectionGates
from .provider_intelligence import ProviderIntelligenceError, ProviderIntelligenceState


class ProviderEligibilityError(ValueError):
    """Raised when canonical provider eligibility is unavailable or invalid."""


_DECISION_TTL = timedelta(hours=1)
_REVISION_SCOPES = frozenset({"revision-bound", "resolve-library"})
_PROVIDER_KINDS = {"capability", "code-intelligence", "knowledge", "documentation"}
_DOMAIN_KINDS = {
    "architect": {"capability", "code-intelligence"},
    "code-intelligence": {"code-intelligence"},
    "knowledge": {"knowledge", "documentation"},
    "documentation": {"knowledge", "documentation"},
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _instant(label: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProviderEligibilityError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ProviderEligibilityError(f"{label} must include timezone")
    return parsed.astimezone(UTC)


def _now(value: str) -> datetime:
    parsed = _instant("assessment now", value)
    return parsed.replace(microsecond=0)


@dataclass(frozen=True)
class ProviderEligibilityDecision:
    """A verifiable projection of an owner-authenticated provider decision.

    ``decision_sha256`` protects representation integrity only.  It is never
    sufficient for trust: ``ProviderEligibilityAuthority.verify`` reloads the
    signed owner state and reconstructs this exact value.
    """

    project_id: str
    provider_id: str
    provider_kind: str
    capability_domain: str
    target_sha: str
    revision_scope: str
    authorized_requirements: tuple[str, ...]
    authority_context_hash: str
    source_state_sha256: str
    policy_generation: int
    policy_eligible: bool
    privacy_eligible: bool
    network_eligible: bool | None
    authentication_eligible: bool | None
    health_eligible: bool
    budget_eligible: bool
    empirical_evidence_eligible: bool
    independence_eligible: bool
    fallback_eligible: bool
    decision_at: str
    expires_at: str
    decision_sha256: str

    def _unsigned(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "capability_domain": self.capability_domain,
            "target_sha": self.target_sha,
            "revision_scope": self.revision_scope,
            "authorized_requirements": list(self.authorized_requirements),
            "authority_context_hash": self.authority_context_hash,
            "source_state_sha256": self.source_state_sha256,
            "policy_generation": self.policy_generation,
            "policy_eligible": self.policy_eligible,
            "privacy_eligible": self.privacy_eligible,
            "network_eligible": self.network_eligible,
            "authentication_eligible": self.authentication_eligible,
            "health_eligible": self.health_eligible,
            "budget_eligible": self.budget_eligible,
            "empirical_evidence_eligible": self.empirical_evidence_eligible,
            "independence_eligible": self.independence_eligible,
            "fallback_eligible": self.fallback_eligible,
            "decision_at": self.decision_at,
            "expires_at": self.expires_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "decision_sha256": self.decision_sha256}

    def validate(self, *, now: str | None = None) -> None:
        if not self.project_id.startswith("project-"):
            raise ProviderEligibilityError("provider decision project binding is invalid")
        if not self.provider_id or not self.provider_id.startswith(("provider-", "knowledge-")):
            raise ProviderEligibilityError("provider decision identity is invalid")
        if self.provider_kind not in _PROVIDER_KINDS:
            raise ProviderEligibilityError("provider decision kind is invalid")
        for label, value in (
            ("authority context hash", self.authority_context_hash),
            ("source state hash", self.source_state_sha256),
            ("decision hash", self.decision_sha256),
        ):
            if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ProviderEligibilityError(f"{label} is invalid")
        if not self.capability_domain or len(self.capability_domain) > 128:
            raise ProviderEligibilityError("provider decision capability domain is invalid")
        if not isinstance(self.target_sha, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.target_sha
        ):
            raise ProviderEligibilityError("provider decision target revision is invalid")
        if self.revision_scope not in _REVISION_SCOPES:
            raise ProviderEligibilityError("provider decision revision scope is invalid")
        if not self.authorized_requirements or any(
            not isinstance(item, str) or not item.strip()
            for item in self.authorized_requirements
        ) or (
            tuple(sorted(set(self.authorized_requirements))) != self.authorized_requirements
            or self.capability_domain not in self.authorized_requirements
        ):
            raise ProviderEligibilityError("provider decision requirement scope is invalid")
        if not isinstance(self.policy_generation, int) or self.policy_generation < 1:
            raise ProviderEligibilityError("provider decision policy generation is invalid")
        for label, value in (
            ("policy", self.policy_eligible),
            ("privacy", self.privacy_eligible),
            ("health", self.health_eligible),
            ("budget", self.budget_eligible),
            ("empirical evidence", self.empirical_evidence_eligible),
            ("independence", self.independence_eligible),
            ("fallback", self.fallback_eligible),
        ):
            if not isinstance(value, bool):
                raise ProviderEligibilityError(f"{label} eligibility is invalid")
        if self.network_eligible is not None and not isinstance(self.network_eligible, bool):
            raise ProviderEligibilityError("network eligibility is invalid")
        if self.authentication_eligible is not None and not isinstance(
            self.authentication_eligible, bool
        ):
            raise ProviderEligibilityError("authentication eligibility is invalid")
        decision_at = _instant("provider decision_at", self.decision_at)
        expires_at = _instant("provider decision expires_at", self.expires_at)
        if expires_at <= decision_at or expires_at > decision_at + _DECISION_TTL:
            raise ProviderEligibilityError("provider decision TTL is invalid")
        if now is not None:
            current = _now(now)
            if decision_at > current:
                raise ProviderEligibilityError("provider decision is future-dated")
            if expires_at <= current:
                raise ProviderEligibilityError("provider decision is stale")
        if self.decision_sha256 != _hash(self._unsigned()):
            raise ProviderEligibilityError("provider decision hash is invalid")


def _decision_from_state(
    state: ProviderIntelligenceState,
    *,
    provider_id: str,
    provider_kind: str,
    capability_domain: str,
    target_sha: str | None,
    revision_scope: str,
    now: str,
    required_capabilities: Iterable[str],
) -> ProviderEligibilityDecision:
    current = _now(now)
    try:
        if revision_scope not in _REVISION_SCOPES:
            raise ProviderEligibilityError("provider decision revision scope is invalid")
        if revision_scope == "revision-bound":
            if not isinstance(target_sha, str) or not target_sha:
                raise ProviderEligibilityError("provider decision target revision is required")
            state.validate(now=now, target_sha=target_sha)
        else:
            if provider_kind != "knowledge" or capability_domain != "documentation":
                raise ProviderEligibilityError("revisionless scope is not supported here")
            if target_sha is not None:
                raise ProviderEligibilityError("revisionless decision must not receive target_sha")
            state.validate(now=now)
    except ProviderIntelligenceError as exc:
        raise ProviderEligibilityError("owner provider intelligence is unavailable") from exc
    if _instant("provider intelligence observed_at", state.observed_at) > current:
        raise ProviderEligibilityError("provider intelligence is future-dated")
    if provider_kind not in _PROVIDER_KINDS:
        raise ProviderEligibilityError("provider decision kind is invalid")
    if provider_kind not in _DOMAIN_KINDS.get(state.decision_domain, set()):
        raise ProviderEligibilityError("provider decision domain is not owner-bound")
    candidate = next(
        (item for item in state.candidates if item.profile.provider_id == provider_id), None
    )
    if candidate is None or candidate.diagnostic_only:
        raise ProviderEligibilityError("provider is not owner-registered")
    requested = tuple(required_capabilities)
    if (
        not requested
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 128
            for item in requested
        )
        or len(set(requested)) != len(requested)
    ):
        raise ProviderEligibilityError("requested provider requirements are invalid")
    if tuple(sorted(requested)) != requested:
        requested = tuple(sorted(requested))
    if not set(requested).issubset(set(state.requirements)):
        raise ProviderEligibilityError("requested provider requirement is outside owner scope")
    if capability_domain not in requested or capability_domain not in state.requirements:
        raise ProviderEligibilityError("provider capability domain is outside owner scope")
    try:
        for capability in requested:
            candidate.profile.require_supported(capability)
    except (CapabilityProfileError, ValueError) as exc:
        raise ProviderEligibilityError("provider capability is not owner-eligible") from exc
    gates = state.gates
    evidence = dict(state.gate_evidence)
    extra = dict(state.provider_gate_evidence)
    network = extra.get("network_eligible")
    authentication = extra.get("authentication_eligible")
    if network is not None and not isinstance(network, bool):
        raise ProviderEligibilityError("owner network eligibility is invalid")
    if authentication is not None and not isinstance(authentication, bool):
        raise ProviderEligibilityError("owner authentication eligibility is invalid")
    context_hash = _hash(
        {
            "project_id": state.project_id,
            "constitution_record_hash": state.constitution_record_hash,
            "policy_generation": state.policy_generation,
            "policy_evidence": evidence.get("policy_eligible"),
            "source_state_sha256": state.state_sha256,
        }
    )
    decision_at = state.observed_at
    expiry = min(
        _instant("provider intelligence expiry", state.expires_at),
        _instant("provider decision_at", decision_at) + _DECISION_TTL,
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    decision = ProviderEligibilityDecision(
        project_id=state.project_id,
        provider_id=provider_id,
        provider_kind=provider_kind,
        capability_domain=capability_domain,
        target_sha=state.target_sha,
        revision_scope=revision_scope,
        authorized_requirements=requested,
        authority_context_hash=context_hash,
        source_state_sha256=state.state_sha256,
        policy_generation=state.policy_generation,
        policy_eligible=gates.policy_eligible is True,
        privacy_eligible=gates.privacy_eligible is True,
        network_eligible=network,
        authentication_eligible=authentication,
        health_eligible=gates.health_eligible is True,
        budget_eligible=gates.budget_eligible is True,
        empirical_evidence_eligible=gates.empirical_evidence_eligible is True,
        independence_eligible=gates.independence_eligible is True,
        fallback_eligible=gates.allow_fallback is True,
        decision_at=decision_at,
        expires_at=expiry,
        decision_sha256="0" * 64,
    )
    decision = ProviderEligibilityDecision(
        **{**decision.__dict__, "decision_sha256": _hash(decision._unsigned())}
    )
    decision.validate(now=now)
    return decision


class ProviderEligibilityAuthority:
    """Resolve and re-verify decisions from the existing owner state store."""

    def __init__(self, store: Any):
        self.store = store

    def resolve(
        self,
        *,
        project_id: str,
        provider_id: str,
        provider_kind: str,
        capability_domain: str,
        now: str,
        required_capabilities: Iterable[str],
        target_sha: str | None = None,
        revision_scope: str = "revision-bound",
        decision_domain: str | None = None,
    ) -> ProviderEligibilityDecision:
        domain = decision_domain or {
            "code-intelligence": "code-intelligence",
            "knowledge": "knowledge",
            "documentation": "documentation",
            "capability": "architect",
        }.get(provider_kind)
        if domain is None:
            raise ProviderEligibilityError("provider decision domain is invalid")
        try:
            state = self.store.for_project(project_id, decision_domain=domain).load()
        except (ProviderIntelligenceError, OSError, TypeError, ValueError) as exc:
            raise ProviderEligibilityError("owner provider intelligence is unavailable") from exc
        decision = _decision_from_state(
            state,
            provider_id=provider_id,
            provider_kind=provider_kind,
            capability_domain=capability_domain,
            target_sha=target_sha,
            revision_scope=revision_scope,
            now=now,
            required_capabilities=required_capabilities,
        )
        if not all(
            (
                decision.policy_eligible,
                decision.privacy_eligible,
                decision.health_eligible,
                decision.budget_eligible,
                decision.empirical_evidence_eligible,
                decision.independence_eligible,
            )
        ):
            raise ProviderEligibilityError("provider is not owner-eligible")
        return decision

    def resolve_knowledge_profile(
        self,
        profile: KnowledgeProviderProfile,
        *,
        now: str,
        required_capability: str,
        target_sha: str | None = None,
        revision_scope: str = "revision-bound",
    ) -> ProviderEligibilityDecision:
        try:
            profile.validate(now=now)
        except CapabilityExtensionError as exc:
            raise ProviderEligibilityError("knowledge provider profile is invalid") from exc
        if required_capability not in profile.capabilities:
            raise ProviderEligibilityError("knowledge provider capability is unsupported")
        if profile.network_required:
            # Network eligibility must be explicit in owner state; profile
            # metadata can require it but can never grant it.
            required_network = True
        else:
            required_network = False
        decision = self.resolve(
            project_id=profile.project_id,
            provider_id=profile.knowledge_provider_id,
            provider_kind="knowledge",
            capability_domain=required_capability,
            target_sha=target_sha,
            revision_scope=revision_scope,
            decision_domain="documentation",
            now=now,
            required_capabilities=(required_capability,),
        )
        if required_network and decision.network_eligible is not True:
            raise ProviderEligibilityError("network eligibility is unavailable")
        if (
            (profile.requires_credentials or profile.requires_authenticated_session)
            and decision.authentication_eligible is not True
        ):
            raise ProviderEligibilityError("authentication eligibility is unavailable")
        if profile.privacy_review_required and not decision.privacy_eligible:
            raise ProviderEligibilityError("privacy eligibility is denied")
        return decision

    def verify(
        self,
        decision: ProviderEligibilityDecision,
        *,
        now: str,
        required_capabilities: Iterable[str],
        target_sha: str | None = None,
    ) -> ProviderEligibilityDecision:
        decision.validate(now=now)
        expected = self.resolve(
            project_id=decision.project_id,
            provider_id=decision.provider_id,
            provider_kind=decision.provider_kind,
            capability_domain=decision.capability_domain,
            now=now,
            required_capabilities=required_capabilities,
            target_sha=(target_sha if decision.revision_scope == "revision-bound" else None),
            revision_scope=decision.revision_scope,
            decision_domain=(
                "documentation"
                if decision.capability_domain == "documentation"
                else {
                    "code-intelligence": "code-intelligence",
                    "knowledge": "knowledge",
                    "documentation": "documentation",
                    "capability": "architect",
                }.get(decision.provider_kind)
            ),
        )
        if expected != decision:
            raise ProviderEligibilityError("provider eligibility decision differs from authority")
        return decision

    def select(
        self,
        candidates: Iterable[CapabilityCandidate],
        *,
        project_id: str,
        required_capabilities: Iterable[str],
        provider_kind: str,
        now: str,
        target_sha: str | None = None,
        revision_scope: str = "revision-bound",
    ):
        ordered = tuple(candidates)
        required = tuple(required_capabilities)
        if not required:
            raise ProviderEligibilityError("required capabilities are missing")
        eligible: list[CapabilityCandidate] = []
        fallback_allowed = True
        for candidate in ordered:
            try:
                decision = self.resolve(
                    project_id=project_id,
                    provider_id=candidate.profile.provider_id,
                    provider_kind=provider_kind,
                    capability_domain=required[0],
                    now=now,
                    required_capabilities=required,
                    target_sha=target_sha,
                    revision_scope=revision_scope,
                )
                if all(
                    (
                        decision.policy_eligible,
                        decision.privacy_eligible,
                        decision.health_eligible,
                        decision.budget_eligible,
                        decision.empirical_evidence_eligible,
                        decision.independence_eligible,
                    )
                ):
                    eligible.append(candidate)
                    fallback_allowed = fallback_allowed and decision.fallback_eligible
            except ProviderEligibilityError:
                continue
        selected = CapabilitySelector().select(
            eligible,
            project_id=project_id,
            required_capabilities=required,
            now=now,
            gates=SelectionGates(True, True, True, True, True, True),
        )
        canonical_primary = CapabilitySelector.order_candidates(ordered)
        if canonical_primary and selected.provider_id != canonical_primary[0].profile.provider_id:
            if not fallback_allowed:
                raise ProviderEligibilityError("owner policy forbids provider fallback")
            from dataclasses import replace

            selected = replace(selected, fallback_used=True)
        return selected
