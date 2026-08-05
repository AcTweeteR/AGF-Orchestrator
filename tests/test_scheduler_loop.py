import json
from pathlib import Path

import pytest

from agf_orchestrator.scheduler_loop import SchedulerLoopError, run_bounded
from agf_orchestrator.scheduler_models import SchedulerStatus, scheduler_from_dict

FIXTURE = Path(__file__).parent / "fixtures" / "scheduler" / "valid_state.json"


def state():
    return scheduler_from_dict(json.loads(FIXTURE.read_text(encoding="utf-8"))).transition(
        SchedulerStatus.RUNNING
    )


def test_bounded_loop_emits_structured_events_and_stops_at_limit():
    def toggle(current):
        next_status = (
            SchedulerStatus.RUNNING
            if current.status is SchedulerStatus.PAUSED
            else SchedulerStatus.PAUSED
        )
        return current.transition(next_status)

    result = run_bounded(state(), toggle, max_steps=2)

    assert result.steps == 2
    assert result.limit_reached is True
    assert [event.event_id for event in result.events] == ["event-000001", "event-000002"]
    assert result.events[0].from_status == "RUNNING"
    assert result.events[0].to_status == "PAUSED"


def test_loop_stops_on_human_required_and_terminal_states():
    human = state().transition(SchedulerStatus.HUMAN_REQUIRED)
    result = run_bounded(human, lambda current: current, max_steps=2)
    assert result.steps == 0
    assert result.state.status is SchedulerStatus.HUMAN_REQUIRED

    completed = state().transition(SchedulerStatus.COMPLETED)
    result = run_bounded(completed, lambda current: current, max_steps=2)
    assert result.steps == 0
    assert result.state.status is SchedulerStatus.COMPLETED


def test_loop_rejects_non_progressing_or_identity_changing_steps():
    with pytest.raises(SchedulerLoopError, match="did not advance"):
        run_bounded(state(), lambda current: current, max_steps=1)

    def wrong_identity(current):
        return current.transition(SchedulerStatus.PAUSED).__class__(
            **{**current.transition(SchedulerStatus.PAUSED).__dict__, "project_id": "project-other"}
        )

    with pytest.raises(SchedulerLoopError, match="identity"):
        run_bounded(state(), wrong_identity, max_steps=1)


def test_loop_bound_is_finite_and_validated():
    with pytest.raises(SchedulerLoopError, match="max_steps"):
        run_bounded(
            state(), lambda current: current.transition(SchedulerStatus.PAUSED), max_steps=101
        )
