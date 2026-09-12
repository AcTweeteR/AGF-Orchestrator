import json
from dataclasses import replace

import pytest
from test_campaign_runner import Clock
from test_delivery_reconciliation import git
from test_objective_completion import ready
from test_session_continuation import ForbiddenPipeline

from agf_orchestrator.campaign_daemon import CampaignDaemon, CampaignDaemonError
from agf_orchestrator.campaign_runner import CampaignStatus, CampaignStore, StepResult
from agf_orchestrator.governed_campaign import (
    GovernedCampaignError,
    GovernedCampaignRunner,
    GovernedSessionDriver,
    GovernedSessionDriverSpec,
    register_governed_campaign,
)


def registered(tmp_path, monkeypatch, *, integrated=True):
    root, state, manager, session, _ = ready(tmp_path, monkeypatch, integrated=integrated)
    spec = GovernedSessionDriverSpec(session.project_id, "campaign-calculator", str(state),
                                    session.session_id)
    initial = register_governed_campaign(spec, 2)
    store = CampaignStore(state, spec.project_id, spec.campaign_id)
    driver = GovernedSessionDriver(spec, manager_factory=lambda: manager,
                                  pipeline_factory=lambda _: ForbiddenPipeline())
    return root, manager, spec, store, driver, initial


@pytest.mark.parametrize("crash_after_reconcile", [False, True])
def test_wait_merge_reconcile_and_restart_preserve_canonical_lineage(
    tmp_path, monkeypatch, crash_after_reconcile,
):
    root, manager, spec, store, driver, initial = registered(
        tmp_path, monkeypatch, integrated=False,
    )
    clock = Clock()
    driver.now = clock
    runner = GovernedCampaignRunner(store, driver, now=clock)
    waiting = runner.tick(driver.probe, driver.work)
    assert waiting.status is CampaignStatus.WAITING_GITHUB
    artifacts = {str(path): path.read_bytes() for path in manager.store.artifacts_dir.rglob("*")
                 if path.is_file()}
    assert driver.probe(waiting) is False
    assert artifacts == {
        str(path): path.read_bytes() for path in manager.store.artifacts_dir.rglob("*")
        if path.is_file()
    }
    git(root, "merge", "--ff-only", "agf/task-001")
    git(root, "push", "origin", "main")
    if crash_after_reconcile:
        manager.resume(spec.session_id)
    clock.advance(31)
    restarted = GovernedCampaignRunner(store, driver, now=clock)
    reconciled = restarted.tick(driver.probe, driver.work)
    assert reconciled.status in {CampaignStatus.RUNNING, CampaignStatus.COMPLETE}
    assert reconciled.target_sha != initial.target_sha
    assert reconciled.lineage_binding != initial.lineage_binding
    assert reconciled.retry_budget == initial.retry_budget
    completed = restarted.tick(driver.probe, driver.work)
    assert completed.status is CampaignStatus.COMPLETE


def test_merge_between_wait_and_campaign_save_is_reconciled_next_tick(tmp_path, monkeypatch):
    root, _, _, store, driver, initial = registered(tmp_path, monkeypatch, integrated=False)
    clock = Clock()
    driver.now = clock
    runner = GovernedCampaignRunner(store, driver, now=clock)

    def concurrent_integration(state):
        result = driver.work(state)
        assert result.outcome == "WAIT"
        git(root, "merge", "--ff-only", "agf/task-001")
        git(root, "push", "origin", "main")
        return result

    waiting = runner.tick(driver.probe, concurrent_integration)
    assert waiting.status is CampaignStatus.WAITING_GITHUB
    assert waiting.target_sha == initial.target_sha
    clock.advance(31)
    result = runner.tick(driver.probe, driver.work)
    assert result.status is CampaignStatus.RUNNING
    assert result.target_sha == git(root, "rev-parse", "HEAD")
    assert runner.tick(driver.probe, driver.work).status is CampaignStatus.COMPLETE


def test_registered_driver_closes_using_canonical_acceptance(tmp_path, monkeypatch):
    _, manager, spec, store, driver, _ = registered(tmp_path, monkeypatch)
    result = GovernedCampaignRunner(store, driver).tick(driver.probe, driver.work)
    assert result.status is CampaignStatus.COMPLETE
    assert manager.get(spec.session_id).status.value == "COMPLETED"
    assert CampaignStore(store.state_dir, spec.project_id, spec.campaign_id).load() == result


def test_registration_cannot_replace_budget_or_driver(tmp_path, monkeypatch):
    _, _, spec, _, _, initial = registered(tmp_path, monkeypatch)
    assert register_governed_campaign(spec, 2) == initial
    for changed, budget in ((spec, 3), (replace(spec, timeout=90), 2),
                            (replace(spec, campaign_id="campaign-second"), 2)):
        with pytest.raises(GovernedCampaignError):
            register_governed_campaign(changed, budget)


def test_owner_advanced_session_can_bind_a_successor_campaign(tmp_path, monkeypatch):
    _, manager, spec, _, _, _ = registered(tmp_path, monkeypatch)
    session = manager.get(spec.session_id)
    old_hash = session.artifact_hashes.pop("campaign_binding")
    session.artifact_hashes["historical:campaign_binding"] = old_hash
    session.artifact_hashes["external_advancement"] = "e" * 64
    manager.store.save(session)
    successor = replace(spec, campaign_id="campaign-successor")
    binding_path = (
        manager.store.artifacts_dir / spec.session_id / "campaign-binding.json"
    )
    old_binding = binding_path.read_bytes()
    snapshot = GovernedSessionDriver.snapshot
    monkeypatch.setattr(
        GovernedSessionDriver,
        "snapshot",
        lambda *_: (_ for _ in ()).throw(GovernedCampaignError("invalid successor")),
    )
    with pytest.raises(GovernedCampaignError, match="invalid successor"):
        register_governed_campaign(successor, 2)
    assert binding_path.read_bytes() == old_binding
    monkeypatch.setattr(GovernedSessionDriver, "snapshot", snapshot)

    state = register_governed_campaign(successor, 2)
    updated = manager.get(spec.session_id)

    assert state.campaign_id == "campaign-successor"
    assert updated.artifact_hashes["campaign_binding"] != old_hash
    assert updated.artifact_hashes["historical:campaign_binding"] == old_hash
    assert (
        manager.store.artifacts_dir
        / spec.session_id
        / "campaign-binding-before-campaign-successor.json"
    ).is_file()


@pytest.mark.parametrize("mode", ["missing-hash", "corrupt", "changed-driver"])
def test_budget_binding_is_checked_before_work_or_probe(tmp_path, monkeypatch, mode):
    _, manager, spec, _, driver, initial = registered(tmp_path, monkeypatch)
    session = manager.get(spec.session_id)
    if mode == "missing-hash":
        del session.artifact_hashes["campaign_binding"]
        manager.store.save(session)
    elif mode == "corrupt":
        path = manager.store.artifacts_dir / session.session_id / "campaign-binding.json"
        path.write_text("{}")
    else:
        driver = GovernedSessionDriver(replace(spec, timeout=90))
    with pytest.raises(GovernedCampaignError, match="binding"):
        driver.probe(initial)
    with pytest.raises(GovernedCampaignError, match="binding"):
        driver.work(initial)


def test_transient_work_failure_retries_after_restart_with_same_target(tmp_path, monkeypatch):
    _, _, spec, store, driver, _ = registered(tmp_path, monkeypatch)
    clock = Clock()
    runner = GovernedCampaignRunner(store, driver, now=clock, base_backoff_seconds=1)

    def unavailable(_):
        raise OSError("temporary service failure")

    failed = runner.tick(driver.probe, unavailable)
    assert failed.status is CampaignStatus.RETRY_BACKOFF
    clock.advance(2)
    restarted = GovernedCampaignRunner(
        CampaignStore(store.state_dir, spec.project_id, spec.campaign_id), driver, now=clock,
    )
    result = restarted.tick(driver.probe, driver.work)
    assert result.status is CampaignStatus.COMPLETE
    assert result.retry_count == 1


def test_no_justified_work_is_distinct_persisted_terminal_status(tmp_path, monkeypatch):
    _, manager, spec, store, driver, _ = registered(tmp_path, monkeypatch)
    runner = GovernedCampaignRunner(store, driver)
    result = runner.tick(driver.probe, lambda _: StepResult("NO_JUSTIFIED_WORK"))
    assert result.status is CampaignStatus.NO_JUSTIFIED_WORK
    assert manager.get(spec.session_id).status.value != "COMPLETED"
    assert runner.tick(lambda _: pytest.fail("terminal probe"),
                       lambda _: pytest.fail("terminal dispatch")) == result


def test_daemon_registration_preserves_bound_configuration(tmp_path, monkeypatch):
    _, _, spec, store, _, _ = registered(tmp_path, monkeypatch)
    daemon = CampaignDaemon(store.state_dir)
    daemon.register(spec)
    daemon.register(spec)
    assert daemon._load_specs() == (spec,)
    with pytest.raises(CampaignDaemonError, match="immutable"):
        daemon.register(replace(spec, timeout=90))
    assert json.loads((daemon.spec_dir / f"{spec.campaign_id}.json").read_text()) == spec.to_dict()


def test_cli_registration_requires_confirmation_and_persists_session_driver(
    tmp_path, monkeypatch, capsys,
):
    from agf_orchestrator.cli import main

    _, state, _, session, _ = ready(tmp_path, monkeypatch)
    args = ["campaign-runner", "register-session", "--state-dir", str(state),
            "--session", session.session_id, "--campaign-id", "campaign-cli", "--json"]
    assert main(args) == 2
    assert not CampaignDaemon(state).spec_dir.exists()
    assert "confirmation" in capsys.readouterr().err
    assert main([*args, "--execute", "--confirm-execution", "--confirm-delivery"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["registered"] is True
    spec, = CampaignDaemon(state)._load_specs()
    assert isinstance(spec, GovernedSessionDriverSpec)
    assert spec.session_id == session.session_id


@pytest.mark.parametrize("payload", [[], {"driver_kind": "unknown"}])
def test_daemon_rejects_unknown_driver_schema(tmp_path, payload):
    daemon = CampaignDaemon(tmp_path)
    daemon.spec_dir.mkdir(parents=True)
    (daemon.spec_dir / "campaign-invalid.json").write_text(json.dumps(payload))
    with pytest.raises(CampaignDaemonError):
        daemon._load_specs()


@pytest.mark.parametrize("lock_kind", ["session", "project"])
def test_real_lock_contention_retries_without_terminalizing(tmp_path, monkeypatch, lock_kind):
    from agf_orchestrator.locking import project_lock, session_lock

    _, manager, spec, store, driver, _ = registered(tmp_path, monkeypatch)
    clock = Clock()
    runner = GovernedCampaignRunner(store, driver, now=clock, base_backoff_seconds=1)
    lock = (session_lock(store.state_dir, spec.session_id, "concurrent-cli")
            if lock_kind == "session"
            else project_lock(store.state_dir, spec.project_id, "concurrent-cli"))
    def concurrent_cli(state):
        # Contention starts after the runner has claimed the campaign, while
        # SessionManager enters its independent session/project transaction.
        with lock:
            return driver.work(state)

    result = runner.tick(driver.probe, concurrent_cli)
    assert result.status is CampaignStatus.RETRY_BACKOFF
    assert manager.get(spec.session_id).status.value == "READY"
    assert result.retry_count == 1
    clock.advance(2)
    assert runner.tick(driver.probe, driver.work).status is CampaignStatus.COMPLETE


def test_relative_architect_spec_is_rejected():
    spec = GovernedSessionDriverSpec("project-test", "campaign-test", "/tmp/state",
                                    "session-test", architect_config="config.json")
    with pytest.raises(GovernedCampaignError, match="absolute"):
        spec.validate()


def test_cli_persists_absolute_architect_path_across_cwd_changes(tmp_path, monkeypatch):
    from agf_orchestrator.cli import main

    _, state, _, session, _ = ready(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    args = ["campaign-runner", "register-session", "--state-dir", str(state),
            "--session", session.session_id, "--campaign-id", "campaign-path",
            "--architect-config", "state/config.json", "--execute", "--confirm-execution",
            "--confirm-delivery", "--json"]
    assert main(args) == 0
    monkeypatch.chdir(state)
    spec, = CampaignDaemon(state)._load_specs()
    assert spec.architect_config == str(state / "config.json")
