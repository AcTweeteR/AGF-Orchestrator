import json
from pathlib import Path

import pytest

from agf_orchestrator.scheduler_models import (
    SchedulerStatus,
    SchedulerValidationError,
    scheduler_from_dict,
)

FIXTURE = Path(__file__).parent / "fixtures" / "scheduler" / "valid_state.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_scheduler_state_round_trips_and_transition_increments_event_sequence():
    state = scheduler_from_dict(load_fixture())
    running = state.transition(SchedulerStatus.RUNNING)

    assert state.status is SchedulerStatus.CREATED
    assert running.status is SchedulerStatus.RUNNING
    assert running.event_sequence == 1
    assert scheduler_from_dict(running.to_dict()) == running


def test_scheduler_pause_resume_and_terminal_transitions_are_explicit():
    state = scheduler_from_dict(load_fixture()).transition(SchedulerStatus.RUNNING)
    paused = state.transition(SchedulerStatus.PAUSED)
    resumed = paused.transition(SchedulerStatus.RUNNING)
    completed = resumed.transition(SchedulerStatus.COMPLETED)

    assert paused.status is SchedulerStatus.PAUSED
    assert resumed.status is SchedulerStatus.RUNNING
    assert completed.status is SchedulerStatus.COMPLETED
    with pytest.raises(SchedulerValidationError, match="invalid scheduler transition"):
        completed.transition(SchedulerStatus.RUNNING)


def test_invalid_budget_lease_and_unknown_fields_are_rejected():
    payload = load_fixture()
    payload["budget_used"] = 1001
    with pytest.raises(SchedulerValidationError, match="budget_used"):
        scheduler_from_dict(payload)

    payload = load_fixture()
    payload["lease_owner"] = "worker"
    with pytest.raises(SchedulerValidationError, match="paired"):
        scheduler_from_dict(payload)

    payload = load_fixture()
    payload["unexpected"] = True
    with pytest.raises(SchedulerValidationError, match="missing or contains unknown"):
        scheduler_from_dict(payload)


def test_scheduler_state_never_accepts_negative_sequence_or_human_action_overflow():
    payload = load_fixture()
    payload["event_sequence"] = -1
    with pytest.raises(SchedulerValidationError, match="event_sequence"):
        scheduler_from_dict(payload)

    payload = load_fixture()
    payload["required_human_actions"] = ["action"] * 201
    with pytest.raises(SchedulerValidationError, match="required_human_actions"):
        scheduler_from_dict(payload)
