import json
import multiprocessing
import time
from dataclasses import replace
from pathlib import Path

import pytest
from test_campaign_runner import build

from agf_orchestrator.campaign_runner import (
    CampaignRunnerError,
    CampaignStatus,
    PersistentCampaignRunner,
    StepResult,
    parse_timestamp,
)
from agf_orchestrator.locking import LockError, project_lock


def defer_with_failed_save(tmp_path, monkeypatch, *, budget=3):
    store, clock = build(tmp_path, budget=budget)
    runner = PersistentCampaignRunner(store, now=clock)

    def unavailable(_):
        raise LockError("project transaction is occupied")

    with monkeypatch.context() as patch:
        patch.setattr(store, "save", lambda _: (_ for _ in ()).throw(LockError("lock is held")))
        after = runner.tick(lambda _: True, unavailable)
    return store, clock, runner, after


def test_real_process_lock_longer_than_save_timeout_records_retry_for_restart(tmp_path):
    store, clock = build(tmp_path)
    context = multiprocessing.get_context("fork")
    acquired, release = context.Event(), context.Event()

    def hold_project():
        with project_lock(store.state_dir, store.project_id, "concurrent-owner"):
            acquired.set()
            assert release.wait(20)

    holder = context.Process(target=hold_project)

    def work(_):
        holder.start()
        assert acquired.wait(5)
        raise LockError("work cannot obtain the project transaction")

    runner = PersistentCampaignRunner(store, now=clock)
    try:
        started = time.monotonic()
        deferred = runner.tick(lambda _: True, work)
        assert time.monotonic() - started >= 5.0
        assert holder.is_alive()
        assert runner._deferred_path().exists()
        assert deferred.status is CampaignStatus.RETRY_BACKOFF
        assert deferred.retry_count == 1
    finally:
        release.set()
        if holder.pid is not None:
            holder.join(5)
            if holder.is_alive():
                holder.kill()
                holder.join(5)
    assert holder.exitcode == 0
    claimed = store.load()
    assert claimed.status is CampaignStatus.RUNNING
    assert parse_timestamp(claimed.lease_expires_at) > clock()
    restarted = PersistentCampaignRunner(store, now=clock)
    recovered = restarted.tick(lambda _: pytest.fail("backoff not due"),
                               lambda _: pytest.fail("active lease must be reconciled first"))
    assert recovered == deferred
    assert recovered.lease_owner is None
    assert not runner._deferred_path().exists()
    clock.advance(31)
    completed = restarted.tick(lambda _: True, lambda _: StepResult("COMPLETE"))
    assert completed.status is CampaignStatus.COMPLETE
    assert completed.retry_count == 1
    assert completed.retry_budget == claimed.retry_budget
    assert completed.operation_id == claimed.operation_id


@pytest.mark.parametrize("damage", ["digest", "identity", "transition"])
def test_corrupt_deferred_retry_is_retained_and_never_dispatches(tmp_path, monkeypatch, damage):
    store, clock, runner, _ = defer_with_failed_save(tmp_path, monkeypatch)
    path = runner._deferred_path()
    payload = json.loads(path.read_text())
    if damage == "digest":
        payload["sha256"] = "0" * 64
    elif damage == "identity":
        payload["after"]["project_id"] = "project-other"
    else:
        payload["after"]["retry_budget"] += 1
    if damage != "digest":
        unsigned = {key: value for key, value in payload.items() if key != "sha256"}
        payload["sha256"] = runner._retry_digest(unsigned)
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    with pytest.raises(CampaignRunnerError):
        PersistentCampaignRunner(store, now=clock).tick(
            lambda _: pytest.fail("corrupt retry must not probe"),
            lambda _: pytest.fail("corrupt retry must not dispatch"),
        )
    assert path.read_bytes() == before
    assert store.load().status is CampaignStatus.RUNNING


def test_concurrent_campaign_change_is_not_overwritten_by_retry(tmp_path, monkeypatch):
    store, clock, runner, _ = defer_with_failed_save(tmp_path, monkeypatch)
    changed = runner.invalidate_binding("different canonical outcome")
    path = runner._deferred_path()
    evidence = path.read_bytes()
    with pytest.raises(CampaignRunnerError, match="conflicts with current campaign"):
        PersistentCampaignRunner(store, now=clock).tick(lambda _: True,
                                                       lambda _: pytest.fail("dispatch"))
    assert store.load() == changed
    assert path.read_bytes() == evidence


def test_crash_after_retry_save_is_recovered_without_charging_twice(tmp_path, monkeypatch):
    store, clock, runner, after = defer_with_failed_save(tmp_path, monkeypatch)
    path = runner._deferred_path()
    unlink = Path.unlink

    def crash(candidate, *args, **kwargs):
        if candidate == path:
            raise OSError("interrupted after retry was saved")
        return unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", crash)
        with pytest.raises(OSError, match="interrupted"):
            PersistentCampaignRunner(store, now=clock).tick(lambda _: True,
                                                           lambda _: pytest.fail("dispatch"))
    assert store.load() == after
    assert path.exists()
    recovered = PersistentCampaignRunner(store, now=clock).tick(
        lambda _: pytest.fail("backoff not due"), lambda _: pytest.fail("dispatch"),
    )
    assert recovered == after
    assert recovered.retry_count == 1
    assert not path.exists()


def test_deferred_exhaustion_stays_terminal_without_extra_work(tmp_path, monkeypatch):
    store, clock, runner, after = defer_with_failed_save(tmp_path, monkeypatch, budget=0)
    assert after.status is CampaignStatus.BLOCKED_NON_RETRYABLE
    assert after.retry_count == 0
    recovered = PersistentCampaignRunner(store, now=clock).tick(
        lambda _: pytest.fail("terminal probe"), lambda _: pytest.fail("terminal dispatch"),
    )
    assert recovered == after
    assert not runner._deferred_path().exists()


def test_deferred_retry_path_cannot_collide_with_another_campaign(tmp_path, monkeypatch):
    store, clock, runner, _ = defer_with_failed_save(tmp_path, monkeypatch)
    other = type(store)(
        tmp_path,
        store.project_id,
        f"{store.campaign_id}.deferred-retry",
        now=clock,
    )
    other.create(
        replace(store.load(), campaign_id=f"{store.campaign_id}.deferred-retry")
    )
    assert runner._deferred_path() != other.path
    assert runner._deferred_path().exists()
    assert other.load().campaign_id.endswith(".deferred-retry")


def test_retry_event_and_state_use_one_recorded_timestamp(tmp_path, monkeypatch):
    store, clock = build(tmp_path)

    class AdvancingClock:
        def __call__(self):
            value = clock()
            clock.advance(1)
            return value

    runner = PersistentCampaignRunner(store, now=AdvancingClock())
    monkeypatch.setattr(
        store, "save", lambda _: (_ for _ in ()).throw(LockError("lock is held"))
    )
    after = runner.tick(lambda _: True, lambda _: (_ for _ in ()).throw(RuntimeError()))
    assert after.updated_at == after.events[-1].timestamp
    assert runner._read_deferred(runner._deferred_path())[1] == after
