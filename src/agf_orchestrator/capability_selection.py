"""Deterministic capability eligibility and safe fallback selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .capability_profiles import CapabilityProfile, CapabilityProfileError


class CapabilitySelectionError(ValueError):
    """Raised when no candidate can satisfy the explicit selection gates."""


@dataclass(frozen=True)
class SelectionGates:
    """Independent gate outcomes supplied by governed components."""

    policy_eligible: bool | None = None
    privacy_eligible: bool | None = None
    independence_eligible: bool | None = None
    budget_eligible: bool | None = None
    health_eligible: bool | None = None
    empirical_evidence_eligible: bool | None = None
    allow_fallback: bool = True

    def failed(self) -> tuple[str, ...]:
        return tuple(
            name if passed is False else f"missing:{name}"
            for name, passed in (
                ("policy", self.policy_eligible),
                ("privacy", self.privacy_eligible),
                ("independence", self.independence_eligible),
                ("budget", self.budget_eligible),
                ("health", self.health_eligible),
                ("empirical_evidence", self.empirical_evidence_eligible),
            ) if not passed
        )


@dataclass(frozen=True)
class CapabilityCandidate:
    """A profile plus caller-supplied eligibility context; never self-selected."""

    profile: CapabilityProfile
    priority: int
    diagnostic_only: bool = False

    def validate(self) -> None:
        self.profile.validate()
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise CapabilitySelectionError("candidate priority is invalid")
        if self.priority < 0:
            raise CapabilitySelectionError("candidate priority must not be negative")


@dataclass(frozen=True)
class SelectionResult:
    provider_id: str
    profile_id: str
    fallback_used: bool
    considered_candidates: tuple[str, ...]
    rejected_reasons: tuple[str, ...]


class CapabilitySelector:
    """Select the first eligible candidate under explicit, deterministic gates."""

    diagnostic_only_provider_ids = frozenset({"provider-qwen", "qwen3.5:9b-q4_K_M"})

    @staticmethod
    def order_candidates(
        candidates: Iterable[CapabilityCandidate],
    ) -> tuple[CapabilityCandidate, ...]:
        return tuple(sorted(
            tuple(candidates),
            key=lambda item: (item.priority, item.profile.provider_id, item.profile.profile_id),
        ))

    def select(
        self,
        candidates: Iterable[CapabilityCandidate],
        *,
        project_id: str,
        required_capabilities: Iterable[str],
        now: str,
        gates: SelectionGates | None = None,
    ) -> SelectionResult:
        active_gates = gates or SelectionGates()
        required = tuple(sorted(set(required_capabilities)))
        if not required:
            raise CapabilitySelectionError("required_capabilities must not be empty")
        ordered = self.order_candidates(candidates)
        considered: list[str] = []
        rejected: list[str] = []
        for index, candidate in enumerate(ordered):
            provider = candidate.profile.provider_id
            considered.append(provider)
            reason = self._rejection_reason(candidate, project_id, required, now, active_gates)
            if reason is not None:
                rejected.append(f"{provider}: {reason}")
                continue
            if index > 0 and not active_gates.allow_fallback:
                rejected.append(f"{provider}: fallback is not permitted")
                continue
            return SelectionResult(
                provider_id=provider,
                profile_id=candidate.profile.profile_id,
                fallback_used=index > 0,
                considered_candidates=tuple(considered),
                rejected_reasons=tuple(rejected),
            )
        raise CapabilitySelectionError("no eligible capability candidate: " + "; ".join(rejected))

    @staticmethod
    def _rejection_reason(
        candidate: CapabilityCandidate,
        project_id: str,
        required: tuple[str, ...],
        now: str,
        gates: SelectionGates,
    ) -> str | None:
        try:
            candidate.validate()
            candidate.profile.validate_binding(project_id, candidate.profile.provider_id)
            candidate.profile.validate_at(now)
        except CapabilityProfileError as exc:
            return str(exc)
        except CapabilitySelectionError as exc:
            return str(exc)
        if (
            candidate.diagnostic_only
            or candidate.profile.provider_id in CapabilitySelector.diagnostic_only_provider_ids
        ):
            return "diagnostic-only candidate"
        failed = gates.failed()
        if failed:
            return "failed gates: " + ",".join(failed)
        for capability in required:
            try:
                candidate.profile.require_supported(capability)
            except CapabilityProfileError:
                return f"required capability is not supported: {capability}"
        return None
