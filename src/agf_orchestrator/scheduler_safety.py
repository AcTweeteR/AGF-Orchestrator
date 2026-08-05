"""Conservative no-progress and deadlock stop gates for the scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .roadmap_models import Roadmap, RoadmapItemStatus
from .scheduler_models import SchedulerStatus


class SafetyGateError(ValueError):
    """Raised when safety-gate input is invalid."""


class SafetyStatus(StrEnum):
    PROCEED = "PROCEED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class ProgressObservation:
    scheduler_id: str
    status: SchedulerStatus
    current_item_id: str | None
    event_sequence: int


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyStatus
    reason: str
    stalled_observations: int = 0


def assess_progress(
    observations: tuple[ProgressObservation, ...], *, max_stalls: int
) -> SafetyDecision:
    """Stop after repeated identical scheduler observations."""
    if not observations:
        raise SafetyGateError("progress observations are required")
    invalid_limit = (
        not isinstance(max_stalls, int)
        or isinstance(max_stalls, bool)
        or not 1 <= max_stalls <= 100
    )
    if invalid_limit:
        raise SafetyGateError("max_stalls is invalid")
    for item in observations:
        if not item.scheduler_id or item.event_sequence < 0:
            raise SafetyGateError("progress observation is invalid")
    current = observations[-1]
    if current.status is SchedulerStatus.HUMAN_REQUIRED:
        return SafetyDecision(SafetyStatus.HUMAN_REQUIRED, "human intervention is required")
    if current.status in {
        SchedulerStatus.COMPLETED, SchedulerStatus.CANCELLED, SchedulerStatus.FAILED,
    }:
        return SafetyDecision(SafetyStatus.COMPLETED, "scheduler reached a terminal state")
    fingerprint = (
        current.scheduler_id, current.status, current.current_item_id, current.event_sequence
    )
    stalled = 0
    for item in reversed(observations):
        if (
            item.scheduler_id, item.status, item.current_item_id, item.event_sequence
        ) != fingerprint:
            break
        stalled += 1
    if stalled >= max_stalls:
        return SafetyDecision(
            SafetyStatus.BLOCKED, "scheduler made no measurable progress", stalled
        )
    return SafetyDecision(SafetyStatus.PROCEED, "scheduler progress remains measurable", stalled)


def assess_roadmap(roadmap: Roadmap) -> SafetyDecision:
    """Stop when unfinished READY work is dependency-blocked with no eligible item."""
    roadmap.validate()
    eligible = roadmap.eligible_items()
    if eligible:
        return SafetyDecision(SafetyStatus.PROCEED, "eligible roadmap work exists")
    unfinished = [
        item for item in roadmap.items
        if item.status not in {RoadmapItemStatus.COMPLETED, RoadmapItemStatus.SUPERSEDED}
    ]
    if not unfinished:
        return SafetyDecision(SafetyStatus.COMPLETED, "all roadmap work is complete")
    if any(item.status is RoadmapItemStatus.READY for item in unfinished):
        return SafetyDecision(SafetyStatus.BLOCKED, "unfinished roadmap work is dependency-blocked")
    return SafetyDecision(SafetyStatus.PROCEED, "roadmap is awaiting explicit lifecycle progress")
