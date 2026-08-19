from datetime import UTC, datetime, timedelta

import pytest

from agf_orchestrator.campaign_runner import (
    CampaignRunnerError,
    CampaignStatus,
    CampaignStore,
    PersistentCampaignRunner,
    StepResult,
    WaitRequest,
    campaign_from_dict,
    make_initial_state,
    timestamp,
)

TARGET = "a" * 40


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def build(tmp_path, *, budget=3):
    clock = Clock()
    store = CampaignStore(tmp_path, "project-ai-fund", "campaign-ai-fund", now=clock)
    state = make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7-audit", target_sha=TARGET,
        lineage_binding="lineage-main", retry_budget=budget, now=clock(),
    )
    store.create(state)
    return store, clock


def wait_for(clock, status=CampaignStatus.WAITING_CI, delay=10):
    return StepResult(
        "WAIT",
        WaitRequest(
            status, "CI is pending", "github:run:123", "conclusion is success",
            timestamp(clock() + timedelta(seconds=delay)),
        ),
    )


def test_ci_pending_persists_wait_and_resumes_after_external_change(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(
        store, now=clock, base_backoff_seconds=1, max_backoff_seconds=4
    )
    calls = []
    ready = {"value": False}

    def work(state):
        calls.append(state.status)
        if len(calls) == 1:
            return wait_for(clock)
        return StepResult("COMPLETE", reason="CI PASS handled")

    assert runner.tick(lambda _state: True, work).status is CampaignStatus.WAITING_CI
    assert calls == [CampaignStatus.RUNNING]
    assert runner.tick(lambda _state: ready["value"], work).status is CampaignStatus.WAITING_CI
    clock.advance(10)
    assert runner.tick(lambda _state: ready["value"], work).status is CampaignStatus.RETRY_BACKOFF
    ready["value"] = True
    clock.advance(2)
    final = runner.tick(lambda _state: ready["value"], work)
    assert final.status is CampaignStatus.COMPLETE
    assert final.session_id == "session-a610d1e887d0c9ac8d7e"
    assert final.wake_generation == 1
    assert [event.event_type for event in final.events] == [
        "WORK_CLAIM", "WAIT", "RETRY_BACKOFF", "WAKE", "WORK_CLAIM", "STEP"
    ]


def test_restart_during_wait_reuses_same_campaign_and_duplicate_wake_is_idempotent(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(
        store, now=clock, base_backoff_seconds=1, max_backoff_seconds=2
    )
    result = runner.tick(lambda _state: True, lambda _state: wait_for(clock))
    clock.advance(10)
    restarted = PersistentCampaignRunner(
        CampaignStore(tmp_path, "project-ai-fund", "campaign-ai-fund"),
        now=clock, base_backoff_seconds=1, max_backoff_seconds=2,
    )
    assert restarted.tick(lambda _state: True, lambda _state: StepResult("COMPLETE")).status \
        is CampaignStatus.COMPLETE
    again = restarted.tick(lambda _state: True, lambda _state: StepResult("COMPLETE"))
    assert again.event_sequence == 5
    assert result.campaign_id == again.campaign_id


def test_waiting_external_does_not_invoke_provider_before_wake(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    calls = []
    runner.tick(lambda _state: True, lambda _state: wait_for(clock, delay=60))
    state = runner.tick(
        lambda _state: calls.append("probe") or True,
        lambda _state: calls.append("work"),
    )
    assert state.status is CampaignStatus.WAITING_CI
    assert calls == []


@pytest.mark.parametrize("terminal", [
    CampaignStatus.COMPLETE,
    CampaignStatus.HUMAN_REQUIRED,
    CampaignStatus.BLOCKED_NON_RETRYABLE,
    CampaignStatus.CANCELLED,
])
def test_terminal_states_are_not_resumed(tmp_path, terminal):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    runner.tick(lambda _state: True, lambda _state: StepResult(terminal.value))
    calls = []
    assert runner.tick(lambda _state: calls.append("probe"), lambda _state: calls.append("work")) \
        .status is terminal
    assert calls == []


def test_retry_budget_exhaustion_is_non_retryable_and_never_loops(tmp_path):
    store, clock = build(tmp_path, budget=1)
    runner = PersistentCampaignRunner(
        store, now=clock, base_backoff_seconds=1, max_backoff_seconds=1
    )
    runner.tick(lambda _state: True, lambda _state: wait_for(clock, delay=0))
    clock.advance(1)
    state = runner.tick(lambda _state: False, lambda _state: StepResult("COMPLETE"))
    assert state.status is CampaignStatus.RETRY_BACKOFF
    clock.advance(1)
    state = runner.tick(lambda _state: False, lambda _state: StepResult("COMPLETE"))
    assert state.status is CampaignStatus.BLOCKED_NON_RETRYABLE
    assert state.retry_count == 1


def test_stale_target_or_extra_state_field_fails_closed(tmp_path):
    store, clock = build(tmp_path)
    payload = store.load().to_dict()
    payload["target_sha"] = "b" * 40
    payload["unexpected"] = True
    with pytest.raises(CampaignRunnerError):
        campaign_from_dict(payload)
    payload = store.load().to_dict()
    payload["target_sha"] = "g" * 40
    with pytest.raises(CampaignRunnerError):
        campaign_from_dict(payload)


def test_owner_boundaries_are_data_not_provider_authority(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    result = runner.tick(
        lambda _state: True,
        lambda _state: StepResult("HUMAN_REQUIRED", reason="real credential required"),
    )
    assert result.status is CampaignStatus.HUMAN_REQUIRED
    assert "real credential" in (result.reason or "")


def test_active_work_lease_blocks_duplicate_wake_claim(tmp_path):
    store, clock = build(tmp_path)
    first = store.claim("runner-first", timestamp(clock() + timedelta(seconds=60)))
    second = store.claim("runner-second", timestamp(clock() + timedelta(seconds=60)))
    assert first is not None
    assert second is None


def test_continue_keeps_campaign_running_for_next_tick(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    result = runner.tick(lambda _state: True, lambda _state: StepResult("CONTINUE"))
    assert result.status is CampaignStatus.RUNNING
    assert result.lease_owner is None


def test_retry_reset_is_bounded_auditable_and_requires_repair_state(tmp_path):
    store, clock = build(tmp_path, budget=1)
    runner = PersistentCampaignRunner(
        store, now=clock, base_backoff_seconds=1, max_backoff_seconds=1
    )
    runner.tick(lambda _state: True, lambda _state: wait_for(clock, delay=0))
    clock.advance(1)
    state = runner.tick(lambda _state: False, lambda _state: StepResult("COMPLETE"))
    assert state.status is CampaignStatus.RETRY_BACKOFF
    reset = runner.reset_retry("driver repaired")
    assert reset.status is CampaignStatus.WAITING_EXTERNAL
    assert reset.retry_count == 0
    assert reset.events[-1].event_type == "RETRY_RESET"
    with pytest.raises(CampaignRunnerError):
        runner.reset_retry("second reset is not idempotent")
