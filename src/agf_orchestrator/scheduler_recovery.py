"""Fail-safe lease expiry and interruption recovery gates."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from .scheduler_models import SchedulerState, SchedulerStatus


class SchedulerRecoveryError(ValueError):
    """Raised when recovery input is invalid."""


class RecoveryStatus(StrEnum):
    RECOVERED = "RECOVERED"
    ACTIVE = "ACTIVE"
    NO_LEASE = "NO_LEASE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class RecoveryDecision:
    status: RecoveryStatus
    state: SchedulerState
    reason: str


def recover_expired_lease(state: SchedulerState, now: str) -> RecoveryDecision:
    """Release only an actually expired lease and pause before reassignment."""
    state.validate()
    if not _TIMESTAMP.fullmatch(now):
        raise SchedulerRecoveryError("recovery timestamp is invalid")
    if state.status is SchedulerStatus.HUMAN_REQUIRED:
        return RecoveryDecision(
            RecoveryStatus.HUMAN_REQUIRED, state, "human intervention is required"
        )
    if state.lease_expires_at is None:
        return RecoveryDecision(RecoveryStatus.NO_LEASE, state, "no active lease exists")
    if now < state.lease_expires_at:
        return RecoveryDecision(RecoveryStatus.ACTIVE, state, "lease is still active")
    recovered = replace(
        state,
        status=SchedulerStatus.PAUSED,
        current_item_id=None,
        lease_owner=None,
        lease_expires_at=None,
        event_sequence=state.event_sequence + 1,
    )
    recovered.validate()
    return RecoveryDecision(
        RecoveryStatus.RECOVERED, recovered, "expired lease released; scheduler paused"
    )


def recover_interruption(state: SchedulerState) -> RecoveryDecision:
    """Pause an interrupted RUNNING scheduler without automatic continuation."""
    state.validate()
    if state.status is SchedulerStatus.HUMAN_REQUIRED:
        return RecoveryDecision(
            RecoveryStatus.HUMAN_REQUIRED, state, "human intervention is required"
        )
    if state.status is not SchedulerStatus.RUNNING:
        return RecoveryDecision(RecoveryStatus.NO_LEASE, state, "scheduler is not RUNNING")
    paused = state.transition(SchedulerStatus.PAUSED)
    return RecoveryDecision(
        RecoveryStatus.RECOVERED, paused, "interruption recovered; scheduler paused"
    )
