"""Disposable end-to-end capability intelligence pilot with no external effects."""

from __future__ import annotations

from dataclasses import dataclass

from .capability_invalidation import (
    CapabilityEvidenceRecord,
    CapabilityInvalidationError,
    CapabilityInvalidator,
    InvalidationReason,
)
from .capability_selection import (
    CapabilityCandidate,
    CapabilitySelectionError,
    CapabilitySelector,
    SelectionGates,
)


class CapabilityPilotError(ValueError):
    """Raised when the bounded disposable pilot cannot prove its invariants."""


@dataclass(frozen=True)
class PilotAuditEvent:
    name: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class CapabilityPilotReport:
    project_id: str
    selected_provider: str
    fallback_used: bool
    restart_verified: bool
    invalidation_digest: str
    audit_events: tuple[PilotAuditEvent, ...]


class CapabilityPilot:
    """Run a finite profile-selection/failure/restart/audit composition in memory."""

    def run(
        self,
        candidates: tuple[CapabilityCandidate, ...],
        evidence: tuple[CapabilityEvidenceRecord, ...],
        *,
        project_id: str,
        required_capability: str,
        now: str,
        gates: SelectionGates,
    ) -> CapabilityPilotReport:
        if len(candidates) < 2 or len(candidates) != len(evidence):
            raise CapabilityPilotError("pilot requires at least two paired candidates")
        store = CapabilityInvalidator()
        events: list[PilotAuditEvent] = []
        for candidate, item in zip(candidates, evidence):
            if candidate.profile.profile_sha256 != item.profile.profile_sha256:
                raise CapabilityPilotError("candidate/evidence profile binding mismatch")
            store.record(item)
        events.append(PilotAuditEvent("record", "PASS", f"profiles={len(evidence)}"))

        primary = evidence[0]
        store.invalidate(primary, InvalidationReason.HEALTH_CHANGE, now)
        events.append(PilotAuditEvent("failure", "PASS", "primary health change invalidated"))
        restored = CapabilityInvalidator.from_state(store.export_state())
        restart_verified = restored.invalidation_digest() == store.invalidation_digest()
        if not restart_verified:
            raise CapabilityPilotError("restart/readback changed invalidation state")
        events.append(PilotAuditEvent("restart", "PASS", "tombstone digest preserved"))

        eligible: list[CapabilityCandidate] = []
        for candidate, item in zip(candidates, evidence):
            try:
                restored.eligible(
                    item, project_id=project_id,
                    provider_state_sha256=item.provider_state_sha256,
                    health_generation=item.health_generation, now=now,
                )
            except CapabilityInvalidationError as exc:
                events.append(
                    PilotAuditEvent(
                        "eligibility", "BLOCKED",
                        f"profile={item.profile.profile_id};hash={item.profile.profile_sha256};{exc}",
                    )
                )
                continue
            eligible.append(candidate)
        try:
            result = CapabilitySelector().select(
                eligible, project_id=project_id,
                required_capabilities=[required_capability], now=now, gates=gates,
            )
        except CapabilitySelectionError as exc:
            events.append(PilotAuditEvent("selection", "BLOCKED", str(exc)))
            raise CapabilityPilotError("pilot has no safe fallback") from exc
        selected = next(item for item in eligible if item.profile.profile_id == result.profile_id)
        events.append(
            PilotAuditEvent(
                "selection", "PASS",
                f"provider={result.provider_id};profile={result.profile_id};"
                f"hash={selected.profile.profile_sha256}",
            )
        )
        return CapabilityPilotReport(
            project_id, result.provider_id,
            True,
            restart_verified,
            restored.invalidation_digest(), tuple(events),
        )
