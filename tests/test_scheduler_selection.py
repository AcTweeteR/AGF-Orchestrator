import json
from pathlib import Path

import pytest

from agf_orchestrator.roadmap_models import roadmap_from_dict
from agf_orchestrator.scheduler_models import SchedulerStatus, scheduler_from_dict
from agf_orchestrator.scheduler_selection import (
    SelectionStatus,
    SelectionValidationError,
    select_next,
)

ROOT = Path(__file__).parent


def load_state():
    payload = json.loads((ROOT / "fixtures/scheduler/valid_state.json").read_text())
    return scheduler_from_dict(payload).transition(SchedulerStatus.RUNNING)


def load_roadmap():
    payload = json.loads((ROOT / "fixtures/roadmaps/valid_roadmap.json").read_text())
    return roadmap_from_dict(payload)


def select(state=None, cost=10):
    return select_next(
        load_roadmap(), state or load_state(), lease_owner="scheduler-worker",
        lease_expires_at="2026-08-05T01:00:00Z", estimated_cost=cost,
    )


def test_selection_uses_roadmap_priority_and_returns_leased_state_without_mutation():
    state = load_state()
    decision = select(state)

    assert decision.status is SelectionStatus.SELECTED
    assert decision.item_id == "item-backlog"
    assert decision.state.current_item_id == "item-backlog"
    assert decision.state.lease_owner == "scheduler-worker"
    assert decision.state.budget_used == 10
    assert state.current_item_id is None


def test_budget_and_active_lease_gates_block_selection():
    state = load_state()
    blocked = select(state, cost=1001)
    assert blocked.status is SelectionStatus.BLOCKED
    leased = select(state).state
    assert select(leased).status is SelectionStatus.BLOCKED


def test_non_running_and_human_required_states_do_not_select():
    paused = load_state().transition(SchedulerStatus.PAUSED)
    assert select(paused).status is SelectionStatus.BLOCKED
    human = load_state().transition(SchedulerStatus.HUMAN_REQUIRED)
    assert select(human).status is SelectionStatus.HUMAN_REQUIRED


def test_selection_request_is_bounded():
    with pytest.raises(SelectionValidationError, match="estimated_cost"):
        select_next(
            load_roadmap(), load_state(), lease_owner="worker",
            lease_expires_at="2026-08-05T01:00:00Z", estimated_cost=0,
        )
