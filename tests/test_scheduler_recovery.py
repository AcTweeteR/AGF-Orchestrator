import json
from dataclasses import replace
from pathlib import Path

import pytest

from agf_orchestrator.scheduler_models import SchedulerStatus, scheduler_from_dict
from agf_orchestrator.scheduler_recovery import (
    RecoveryStatus,
    SchedulerRecoveryError,
    recover_expired_lease,
    recover_interruption,
)

FIXTURE = Path(__file__).parent / "fixtures" / "scheduler" / "valid_state.json"


def running_state():
    return scheduler_from_dict(json.loads(FIXTURE.read_text())).transition(SchedulerStatus.RUNNING)


def leased_state():
    state = running_state()
    return replace(
        state, current_item_id="item-backlog", lease_owner="worker",
        lease_expires_at="2026-08-05T01:00:00Z",
    )


def test_expired_lease_is_released_and_paused():
    decision = recover_expired_lease(leased_state(), "2026-08-05T01:00:00Z")

    assert decision.status is RecoveryStatus.RECOVERED
    assert decision.state.status is SchedulerStatus.PAUSED
    assert decision.state.current_item_id is None
    assert decision.state.lease_owner is None


def test_active_lease_and_no_lease_are_not_recovered():
    active = recover_expired_lease(leased_state(), "2026-08-05T00:30:00Z")
    none = recover_expired_lease(running_state(), "2026-08-05T00:30:00Z")

    assert active.status is RecoveryStatus.ACTIVE
    assert none.status is RecoveryStatus.NO_LEASE


def test_interruption_pauses_but_never_auto_resumes():
    decision = recover_interruption(running_state())
    assert decision.status is RecoveryStatus.RECOVERED
    assert decision.state.status is SchedulerStatus.PAUSED
    human = running_state().transition(SchedulerStatus.HUMAN_REQUIRED)
    assert recover_interruption(human).status is RecoveryStatus.HUMAN_REQUIRED


def test_recovery_timestamp_is_strictly_validated():
    with pytest.raises(SchedulerRecoveryError, match="timestamp"):
        recover_expired_lease(leased_state(), "tomorrow")
