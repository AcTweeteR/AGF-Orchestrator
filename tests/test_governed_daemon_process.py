"""Real forked daemon lifecycle with ephemeral authority; no live provider E2E."""

import multiprocessing
import time

import test_governed_campaign as fixtures
from test_delivery_reconciliation import git

from agf_orchestrator.adapters.codex import CodexAdapter
from agf_orchestrator.adapters.openhands import OpenHandsSDKAdapter
from agf_orchestrator.campaign_daemon import CampaignDaemon
from agf_orchestrator.campaign_runner import CampaignStatus, parse_timestamp
from agf_orchestrator.governed_campaign import GovernedSessionDriverSpec


def registered(tmp_path, monkeypatch):
    # Use the existing signed fixture with a short real scheduling interval.
    monkeypatch.setattr(fixtures, "GovernedSessionDriverSpec", lambda *args, **kwargs:
                        GovernedSessionDriverSpec(*args, **kwargs, poll_seconds=1))
    fixture = fixtures.registered(tmp_path, monkeypatch, integrated=False)
    _, _, spec, store, _, _ = fixture
    CampaignDaemon(store.state_dir).register(spec)

    def forbidden(*args, **kwargs):
        raise AssertionError("waiting/reconciliation/closure must not invoke a provider")

    monkeypatch.setattr(CodexAdapter, "execute", forbidden)
    monkeypatch.setattr(OpenHandsSDKAdapter, "execute", forbidden)
    return fixture


def run_process(context, state_dir, *, sleep=None, loops=1):
    receiver, sender = context.Pipe(duplex=False)

    def child():
        try:
            daemon = CampaignDaemon(state_dir, **({"sleep": sleep} if sleep else {}))
            daemon.run_forever(max_loops=loops)
            sender.send(("OK", None))
        except BaseException as exc:
            sender.send((type(exc).__name__, str(exc)))
        finally:
            sender.close()

    process = context.Process(target=child)
    process.start()
    sender.close()
    return process, receiver


def finish(process, receiver):
    try:
        process.join(20)
        assert not process.is_alive(), "daemon failed to finish its bounded loop"
        assert process.exitcode == 0
        assert receiver.poll(1), "child did not report an outcome"
        return receiver.recv()
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        receiver.close()


def test_forked_daemon_waits_exits_and_resumes_real_integration(tmp_path, monkeypatch):
    root, manager, spec, store, _, initial = registered(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    assert finish(*run_process(context, store.state_dir)) == ("OK", None)
    waiting = store.load()
    assert waiting.status is CampaignStatus.WAITING_GITHUB
    assert waiting.target_sha == initial.target_sha
    assert waiting.retry_count == 0
    assert not CampaignDaemon(store.state_dir).status().runner_active
    assert not list((manager.store.artifacts_dir / spec.session_id).glob(
        "execution-started-*.json",
    ))

    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    delay = max(0, parse_timestamp(waiting.next_check_at).timestamp() - time.time())
    time.sleep(delay + 0.05)
    assert finish(*run_process(context, store.state_dir)) == ("OK", None)
    reconciled = store.load()
    assert reconciled.status is CampaignStatus.RUNNING
    assert reconciled.target_sha == git(root, "rev-parse", "HEAD")
    assert reconciled.target_sha != initial.target_sha
    assert reconciled.lineage_binding == manager.get(spec.session_id).artifact_hashes["plan"]
    assert any(event.event_type == "WAKE" for event in reconciled.events)
    assert reconciled.retry_budget == initial.retry_budget
    assert reconciled.retry_count == waiting.retry_count
    assert reconciled.operation_id == initial.operation_id

    assert finish(*run_process(context, store.state_dir)) == ("OK", None)
    completed = store.load()
    assert completed.status is CampaignStatus.COMPLETE
    assert manager.get(spec.session_id).status.value == "COMPLETED"
    assert completed.event_sequence > reconciled.event_sequence
    assert completed.policy_binding == initial.policy_binding
    assert completed.authority_generation == initial.authority_generation
    assert completed.retry_budget == initial.retry_budget
    assert not list((manager.store.artifacts_dir / spec.session_id).glob(
        "assessment-invocation-*-started.json",
    ))
    assert not list((manager.store.artifacts_dir / spec.session_id).glob(
        "execution-started-*.json",
    ))


def test_second_daemon_process_cannot_enter_active_instance(tmp_path, monkeypatch):
    _, _, _, store, _, _ = registered(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    sleeping, release = context.Event(), context.Event()

    def pause(_):
        sleeping.set()
        if not release.wait(15):
            raise AssertionError("test did not release first daemon")

    first, first_result = run_process(context, store.state_dir, sleep=pause, loops=2)
    try:
        assert sleeping.wait(10)
        before = store.path.read_bytes()
        outcome, reason = finish(*run_process(context, store.state_dir))
        assert outcome == "CampaignDaemonError"
        assert "another campaign daemon is active" in reason
        assert store.path.read_bytes() == before
    finally:
        release.set()
        assert finish(first, first_result) == ("OK", None)
