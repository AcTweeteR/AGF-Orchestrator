"""Bounded cooperative scheduler loop and structured status events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .scheduler_models import SchedulerState, SchedulerStatus


class SchedulerLoopError(RuntimeError):
    """Raised when a scheduler step violates the state contract."""


TERMINAL_STATES = {
    SchedulerStatus.COMPLETED,
    SchedulerStatus.FAILED,
    SchedulerStatus.CANCELLED,
}
STOP_STATES = TERMINAL_STATES | {
    SchedulerStatus.BLOCKED,
    SchedulerStatus.HUMAN_REQUIRED,
}


@dataclass(frozen=True)
class SchedulerEvent:
    event_id: str
    sequence: int
    event_type: str
    from_status: str
    to_status: str
    summary: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class LoopResult:
    state: SchedulerState
    events: tuple[SchedulerEvent, ...]
    steps: int
    limit_reached: bool


def run_bounded(
    state: SchedulerState,
    step: Callable[[SchedulerState], SchedulerState],
    *,
    max_steps: int,
) -> LoopResult:
    """Run a finite number of validated cooperative scheduler steps."""
    state.validate()
    if not callable(step):
        raise SchedulerLoopError("scheduler step is not callable")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 100:
        raise SchedulerLoopError("max_steps is invalid")
    current = state
    events: list[SchedulerEvent] = []
    for _ in range(max_steps):
        if current.status in STOP_STATES:
            break
        previous = current
        try:
            current = step(previous)
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise SchedulerLoopError("scheduler step failed") from exc
        if not isinstance(current, SchedulerState):
            raise SchedulerLoopError("scheduler step returned invalid state")
        current.validate()
        if current.project_id != state.project_id or current.scheduler_id != state.scheduler_id:
            raise SchedulerLoopError("scheduler step changed state identity")
        if current.event_sequence != previous.event_sequence + 1:
            raise SchedulerLoopError("scheduler step did not advance event sequence")
        sequence = len(events) + 1
        events.append(
            SchedulerEvent(
                f"event-{sequence:06d}", sequence, "STATE_TRANSITION",
                previous.status.value, current.status.value, "scheduler step completed",
            )
        )
    return LoopResult(current, tuple(events), len(events), len(events) == max_steps)
