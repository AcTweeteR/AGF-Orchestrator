import multiprocessing
import os
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


def test_boolean_authority_generation_is_rejected():
    with pytest.raises(CampaignRunnerError, match="authority_generation"):
        make_initial_state(
            project_id="project-ai-fund", campaign_id="campaign-ai-fund",
            session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
            operation_id="operation-r7-audit", target_sha=TARGET,
            lineage_binding="lineage-main", retry_budget=3,
            authority_generation=True,
        )


def test_non_string_policy_binding_is_rejected():
    with pytest.raises(CampaignRunnerError, match="policy_binding"):
        make_initial_state(
            project_id="project-ai-fund", campaign_id="campaign-ai-fund",
            session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
            operation_id="operation-r7-audit", target_sha=TARGET,
            lineage_binding="lineage-main", retry_budget=3,
            policy_binding=1,
        )


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


def test_stale_binding_is_terminal_and_cannot_wake_after_restart(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    runner.tick(lambda _state: True, lambda _state: wait_for(clock, delay=0))
    stale = runner.invalidate_binding("canonical target advanced")
    assert stale.status is CampaignStatus.BLOCKED_NON_RETRYABLE
    assert stale.events[-1].event_type == "STALE_BINDING"
    calls = []
    restarted = PersistentCampaignRunner(
        CampaignStore(tmp_path, "project-ai-fund", "campaign-ai-fund"), now=clock
    )
    assert restarted.tick(
        lambda _state: calls.append("probe"), lambda _state: calls.append("work")
    ) == stale
    assert calls == []
    with pytest.raises(CampaignRunnerError):
        restarted.reset_retry("do not revive stale binding")


def test_wake_guard_rejects_target_change_before_wake_is_persisted(tmp_path):
    store, clock = build(tmp_path)
    runner = PersistentCampaignRunner(store, now=clock)
    runner.tick(lambda _state: True, lambda _state: wait_for(clock, delay=0))
    before = store.load()
    with pytest.raises(CampaignRunnerError, match="canonical target"):
        runner.tick(
            lambda _state: True,
            lambda _state: StepResult("COMPLETE"),
            wake_guard=lambda _state: (_ for _ in ()).throw(
                CampaignRunnerError("canonical target advanced")
            ),
        )
    after = store.load()
    assert after.event_sequence == before.event_sequence
    assert all(event.event_type != "WAKE" for event in after.events[before.event_sequence:])


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


def test_expired_lease_does_not_duplicate_still_running_work(tmp_path):
    store, clock = build(tmp_path)
    first = PersistentCampaignRunner(store, now=clock)
    second = PersistentCampaignRunner(store, now=clock)
    calls = []

    def work(state):
        calls.append("first")
        clock.advance(first.lease_seconds + 1)
        observed = second.tick(
            lambda _state: True,
            lambda _state: calls.append("duplicate") or StepResult("COMPLETE"),
        )
        assert observed.lease_owner == first.worker_id
        return StepResult("COMPLETE")

    final = first.tick(lambda _state: True, work)
    assert calls == ["first"]
    assert final.status is CampaignStatus.COMPLETE


def test_crashed_worker_releases_lock_and_restart_preserves_lineage(tmp_path):
    store, clock = build(tmp_path)
    before = store.load()

    def crash():
        runner = PersistentCampaignRunner(store, now=clock)
        runner.tick(lambda _state: True, lambda _state: os._exit(17))

    process = multiprocessing.get_context("fork").Process(target=crash)
    process.start()
    process.join(timeout=5)
    try:
        assert not process.is_alive()
        assert process.exitcode == 17
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    interrupted = store.load()
    assert interrupted.status is CampaignStatus.RUNNING
    assert interrupted.lease_owner is not None
    calls = []
    restarted = PersistentCampaignRunner(store, now=clock)
    restarted.tick(lambda _state: True, lambda state: calls.append(state))
    assert calls == []  # A crash does not bypass the outstanding lease.
    clock.advance(restarted.lease_seconds + 1)
    recovered = restarted.tick(
        lambda _state: True,
        lambda state: calls.append(state) or StepResult("COMPLETE"),
    )
    assert len(calls) == 1
    assert recovered.status is CampaignStatus.COMPLETE
    assert recovered.session_id == before.session_id
    assert recovered.lineage_binding == before.lineage_binding
    assert [event.event_type for event in recovered.events] == [
        "WORK_CLAIM", "WORK_CLAIM", "STEP",
    ]
