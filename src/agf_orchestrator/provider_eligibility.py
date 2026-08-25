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
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .capability_extensions import CapabilityExtensionError, KnowledgeProviderProfile
from .capability_profiles import CapabilityProfileError
from .capability_selection import CapabilityCandidate, CapabilitySelector, SelectionGates
from .provider_intelligence import (
    ProviderIntelligenceError,
    ProviderIntelligenceState,
    ProviderIntelligenceStore,
)


class ProviderEligibilityError(ValueError):
    """Raised when canonical provider eligibility is unavailable or invalid."""


def _canonical_state_root() -> Path:
    """Return the process' owner-configured AGF state root.

    The root is deployment configuration, not an input to an eligibility
    operation.  In particular, a valid owner signature on a copied state
    does not make the copy the active AGF control plane.
    """
    return Path(
        os.environ.get("AGF_STATE_DIR") or "~/.agf-orchestrator"
    ).expanduser().resolve()


@dataclass(frozen=True)
class _OwnerStoreHandle:
    """Immutable capture of the canonical owner-store load primitives."""

    root: Path
    for_project_fn: Any
    load_fn: Any

    def load(self, project_id: str, decision_domain: str) -> ProviderIntelligenceState:
        fresh_store = ProviderIntelligenceStore(self.root)
        scoped_store = self.for_project_fn(
            fresh_store, project_id, decision_domain=decision_domain
        )
        return self.load_fn(scoped_store)


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


def canonical_knowledge_security_posture(profile: KnowledgeProviderProfile) -> str:
    """Canonical owner-bindable security metadata for a knowledge profile."""
    return json.dumps(
        {
            "capabilities": sorted(profile.capabilities),
            "transport": profile.transport.value,
            "requires_credentials": profile.requires_credentials,
            "requires_authenticated_session": profile.requires_authenticated_session,
            "network_required": profile.network_required,
            "browser_automation": profile.browser_automation,
            "privacy_classification": profile.privacy_classification.value,
            "privacy_review_required": profile.privacy_review_required,
            "mutability": profile.mutability.value,
            "stability": profile.stability.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


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
    source_decision_domain: str
    candidate_profile_sha256: str
    candidate_priority: int
    target_sha: str
    revision_scope: str
    security_posture_sha256: str
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
            "source_decision_domain": self.source_decision_domain,
            "candidate_profile_sha256": self.candidate_profile_sha256,
            "candidate_priority": self.candidate_priority,
            "target_sha": self.target_sha,
            "revision_scope": self.revision_scope,
            "security_posture_sha256": self.security_posture_sha256,
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
        if self.source_decision_domain not in _DOMAIN_KINDS:
            raise ProviderEligibilityError("provider decision source domain is invalid")
        if self.provider_kind not in _DOMAIN_KINDS[self.source_decision_domain]:
            raise ProviderEligibilityError("provider decision source domain mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", self.candidate_profile_sha256):
            raise ProviderEligibilityError("provider decision candidate profile is invalid")
        if not isinstance(self.candidate_priority, int) or self.candidate_priority < 0:
            raise ProviderEligibilityError("provider decision candidate priority is invalid")
        if not isinstance(self.target_sha, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.target_sha
        ):
            raise ProviderEligibilityError("provider decision target revision is invalid")
        if self.revision_scope not in _REVISION_SCOPES:
            raise ProviderEligibilityError("provider decision revision scope is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.security_posture_sha256):
            raise ProviderEligibilityError("provider decision security posture is invalid")
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
    posture_payload = dict(state.provider_security_posture).get(provider_id)
    if provider_kind in {"knowledge", "documentation"} and posture_payload is None:
        raise ProviderEligibilityError("owner provider security posture is unavailable")
    posture: dict[str, Any] = {}
    if posture_payload is None:
        posture_hash = _hash({})
    else:
        try:
            posture = json.loads(posture_payload)
            if not isinstance(posture, dict):
                raise ProviderEligibilityError("owner provider security posture is invalid")
            posture_hash = _hash(posture)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderEligibilityError("owner provider security posture is invalid") from exc
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
    scoped_gate_records = {
        (provider, profile_sha): dict(facts)
        for provider, profile_sha, facts in state.provider_gate_evidence_by_candidate
    }
    scoped_facts = scoped_gate_records.get(
        (candidate.profile.provider_id, candidate.profile.profile_sha256)
    )
    if scoped_facts is not None:
        required_gate_names = {
            "policy_eligible", "privacy_eligible", "health_eligible", "budget_eligible",
            "empirical_evidence_eligible", "independence_eligible",
        }
        if not required_gate_names.issubset(scoped_facts):
            raise ProviderEligibilityError("provider-scoped gate evidence is incomplete")
        policy = scoped_facts["policy_eligible"]
        privacy = scoped_facts["privacy_eligible"]
        health = scoped_facts["health_eligible"]
        budget = scoped_facts["budget_eligible"]
        empirical = scoped_facts["empirical_evidence_eligible"]
        independence = scoped_facts["independence_eligible"]
        network = scoped_facts.get("network_eligible")
        authentication = scoped_facts.get("authentication_eligible")
    else:
        gates = state.gates
        policy = gates.policy_eligible is True
        privacy = gates.privacy_eligible is True
        health = gates.health_eligible is True
        budget = gates.budget_eligible is True
        empirical = gates.empirical_evidence_eligible is True
        independence = gates.independence_eligible is True
        extra = dict(state.provider_gate_evidence)
        network = extra.get("network_eligible")
        authentication = extra.get("authentication_eligible")
    evidence = dict(state.gate_evidence)
    if network is not None and not isinstance(network, bool):
        raise ProviderEligibilityError("owner network eligibility is invalid")
    if authentication is not None and not isinstance(authentication, bool):
        raise ProviderEligibilityError("owner authentication eligibility is invalid")
    if provider_kind in {"knowledge", "documentation"}:
        required_network = posture.get("network_required")
        requires_credentials = posture.get("requires_credentials")
        requires_session = posture.get("requires_authenticated_session")
        privacy_review_required = posture.get("privacy_review_required")
        if not all(
            isinstance(value, bool)
            for value in (
                required_network,
                requires_credentials,
                requires_session,
                privacy_review_required,
            )
        ):
            raise ProviderEligibilityError("owner provider security posture is incomplete")
        if required_network and network is not True:
            raise ProviderEligibilityError("network eligibility is unavailable")
        if (requires_credentials or requires_session) and authentication is not True:
            raise ProviderEligibilityError("authentication eligibility is unavailable")
        if privacy_review_required and privacy is not True:
            raise ProviderEligibilityError("privacy eligibility is unavailable")
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
        source_decision_domain=state.decision_domain,
        candidate_profile_sha256=candidate.profile.profile_sha256,
        candidate_priority=candidate.priority,
        target_sha=state.target_sha,
        revision_scope=revision_scope,
        security_posture_sha256=posture_hash,
        authorized_requirements=requested,
        authority_context_hash=context_hash,
        source_state_sha256=state.state_sha256,
        policy_generation=state.policy_generation,
        policy_eligible=policy,
        privacy_eligible=privacy,
        network_eligible=network,
        authentication_eligible=authentication,
        health_eligible=health,
        budget_eligible=budget,
        empirical_evidence_eligible=empirical,
        independence_eligible=independence,
        fallback_eligible=state.gates.allow_fallback is True,
        decision_at=decision_at,
        expires_at=expiry,
        decision_sha256="0" * 64,
    )
    decision = ProviderEligibilityDecision(
        **{**decision.__dict__, "decision_sha256": _hash(decision._unsigned())}
    )
    decision.validate(now=now)
    return decision


def _require_owner_eligible(decision: ProviderEligibilityDecision) -> None:
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


class ProviderEligibilityAuthority:
    """Resolve and re-verify decisions from the existing owner state store."""

    __slots__ = ("_store_handle", "_sealed")

    def __init__(self, store: Any | None = None):
        if store is None:
            store = ProviderIntelligenceStore()
        if type(store) is not ProviderIntelligenceStore or not store.owner_verifying:
            raise ProviderEligibilityError(
                "provider eligibility requires the canonical owner-verifying store"
            )
        if Path(store.root).resolve() != _canonical_state_root():
            raise ProviderEligibilityError(
                "provider eligibility store is not the configured canonical state root"
            )
        object.__setattr__(
            self,
            "_store_handle",
            _OwnerStoreHandle(
                Path(store.root).resolve(),
                ProviderIntelligenceStore.for_project,
                ProviderIntelligenceStore.load,
            ),
        )
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("provider eligibility authority is immutable")
        object.__setattr__(self, name, value)

    @property
    def store(self) -> _OwnerStoreHandle:
        """Expose only the immutable canonical handle, never the mutable store."""
        return self._store_handle

    def _load_state(self, project_id: str, decision_domain: str) -> ProviderIntelligenceState:
        try:
            return self._store_handle.load(project_id, decision_domain)
        except (ProviderIntelligenceError, OSError, TypeError, ValueError) as exc:
            raise ProviderEligibilityError(
                "owner provider intelligence is unavailable"
            ) from exc

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
        state = self._load_state(project_id, domain)
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
        _require_owner_eligible(decision)
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
            decision_domain=(
                "documentation" if required_capability == "documentation" else "knowledge"
            ),
            now=now,
            required_capabilities=(required_capability,),
        )
        caller_posture_hash = _hash(
            json.loads(canonical_knowledge_security_posture(profile))
        )
        if caller_posture_hash != decision.security_posture_sha256:
            raise ProviderEligibilityError("knowledge provider security posture differs from owner")
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
            decision_domain=decision.source_decision_domain,
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
        required = tuple(required_capabilities)
        if not required:
            raise ProviderEligibilityError("required capabilities are missing")
        if len(set(required)) != len(required) or any(
            not isinstance(item, str) or not item.strip() for item in required
        ):
            raise ProviderEligibilityError("required capabilities are invalid")
        required = tuple(sorted(required))

        # Candidate identity, priority, and ordering are part of the
        # owner-authenticated ProviderIntelligenceState. The caller's
        # candidates are observations only: accepting them here would let a
        # caller add, omit, or reorder a provider and thereby change primary
        # versus fallback semantics.
        if revision_scope == "resolve-library":
            if provider_kind != "knowledge":
                raise ProviderEligibilityError(
                    "revisionless selection is limited to knowledge documentation"
                )
            domain = "documentation"
        else:
            domain = {
                "code-intelligence": "code-intelligence",
                "knowledge": "knowledge",
                "documentation": "documentation",
                "capability": "architect",
            }.get(provider_kind)
            if domain is None:
                raise ProviderEligibilityError("provider decision domain is invalid")
            if provider_kind == "knowledge" and "documentation" in required:
                domain = "documentation"
        owner_state = self._load_state(project_id, domain)
        try:
            if revision_scope == "revision-bound":
                if target_sha is None:
                    raise ProviderEligibilityError("target revision is required")
                owner_state.validate(now=now, target_sha=target_sha)
            elif revision_scope == "resolve-library":
                owner_state.validate(now=now)
            else:
                raise ProviderEligibilityError("revision scope is invalid")
        except ProviderIntelligenceError as exc:
            raise ProviderEligibilityError(
                "owner provider intelligence is unavailable"
            ) from exc
        owner_candidates = tuple(owner_state.candidates)
        if not owner_candidates:
            raise ProviderEligibilityError("owner candidate set is empty")

        eligible: list[CapabilityCandidate] = []
        fallback_allowed = True
        for candidate in owner_candidates:
            try:
                decision = _decision_from_state(
                    owner_state,
                    provider_id=candidate.profile.provider_id,
                    provider_kind=provider_kind,
                    capability_domain=(
                        "documentation" if revision_scope == "resolve-library" else required[0]
                    ),
                    now=now,
                    required_capabilities=required,
                    target_sha=target_sha,
                    revision_scope=revision_scope,
                )
                _require_owner_eligible(decision)
                eligible.append(candidate)
                fallback_allowed = fallback_allowed and decision.fallback_eligible
            except (ProviderEligibilityError, ProviderIntelligenceError):
                continue
        selected = CapabilitySelector().select(
            eligible,
            project_id=project_id,
            required_capabilities=required,
            now=now,
            gates=SelectionGates(True, True, True, True, True, True),
        )
        canonical_primary = CapabilitySelector.order_candidates(owner_candidates)
        if canonical_primary and selected.provider_id != canonical_primary[0].profile.provider_id:
            if not fallback_allowed:
                raise ProviderEligibilityError("owner policy forbids provider fallback")
            from dataclasses import replace

            selected = replace(selected, fallback_used=True)
        return selected
