"""Deterministic eligible-task selection with bounded lease and budget gates."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from .roadmap_models import Roadmap
from .scheduler_models import SchedulerState, SchedulerStatus


class SelectionValidationError(ValueError):
    """Raised when a selection request is malformed."""


class SelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_ELIGIBLE = "NO_ELIGIBLE"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MAX_TEXT = 4000


@dataclass(frozen=True)
class SelectionDecision:
    status: SelectionStatus
    item_id: str | None
    reason: str
    state: SchedulerState


def select_next(
    roadmap: Roadmap,
    state: SchedulerState,
    *,
    lease_owner: str,
    lease_expires_at: str,
    estimated_cost: int,
) -> SelectionDecision:
    """Select exactly one eligible item without executing or mutating inputs."""
    roadmap.validate()
    state.validate()
    _validate_request(lease_owner, lease_expires_at, estimated_cost)
    if state.status is SchedulerStatus.HUMAN_REQUIRED:
        return SelectionDecision(
            SelectionStatus.HUMAN_REQUIRED, None, "human intervention is required", state
        )
    if state.status is not SchedulerStatus.RUNNING:
        return SelectionDecision(SelectionStatus.BLOCKED, None, "scheduler is not RUNNING", state)
    if state.current_item_id is not None or state.lease_owner is not None:
        return SelectionDecision(
            SelectionStatus.BLOCKED, None, "an active lease already exists", state
        )
    if state.budget_used + estimated_cost > state.budget_limit:
        return SelectionDecision(SelectionStatus.BLOCKED, None, "budget is insufficient", state)
    eligible = roadmap.eligible_items()
    if not eligible:
        return SelectionDecision(
            SelectionStatus.NO_ELIGIBLE, None, "no eligible roadmap item", state
        )
    item = eligible[0]
    claimed = replace(
        state,
        current_item_id=item.item_id,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        budget_used=state.budget_used + estimated_cost,
        event_sequence=state.event_sequence + 1,
    )
    claimed.validate()
    return SelectionDecision(
        SelectionStatus.SELECTED, item.item_id, "eligible item leased", claimed
    )


def _validate_request(lease_owner: str, lease_expires_at: str, estimated_cost: int) -> None:
    if not isinstance(lease_owner, str) or not lease_owner.strip() or len(lease_owner) > _MAX_TEXT:
        raise SelectionValidationError("lease_owner is invalid")
    if not _TIMESTAMP.fullmatch(lease_expires_at):
        raise SelectionValidationError("lease_expires_at is invalid")
    if (
        not isinstance(estimated_cost, int)
        or isinstance(estimated_cost, bool)
        or estimated_cost <= 0
    ):
        raise SelectionValidationError("estimated_cost is invalid")
