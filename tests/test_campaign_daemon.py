import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agf_orchestrator.campaign_daemon import (
    CampaignDaemon,
    CampaignDaemonError,
    CampaignDriverSpec,
    CanonicalBindingError,
    render_launchd_plist,
)
from agf_orchestrator.campaign_runner import (
    CampaignStatus,
    CampaignStore,
    StepResult,
    WaitRequest,
    make_initial_state,
    timestamp,
)

TARGET = "a" * 40


def test_daemon_survives_driver_exit_and_wakes_same_campaign(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    probe = scripts / "probe.py"
    probe.write_text("import json; json.load(__import__('sys').stdin); print('{\"ready\":true}')\n")
    work = scripts / "work.py"
    work.write_text(
        "import json,sys; state=json.load(sys.stdin); "
        "print('{\"outcome\":\"WAIT\",\"wait\":{\"status\":\"WAITING_CI\","
        "\"reason\":\"ci\",\"resource\":\"run\","
        "\"expected_condition\":\"pass\",\"next_check_at\":\""
        f"{timestamp(datetime.now(UTC) - timedelta(seconds=1))}"
        "\"}}' if state['wake_generation']==0 else '{\"outcome\":\"COMPLETE\"}')\n"
    )
    state_dir = tmp_path / "state"
    store = CampaignStore(state_dir, "project-ai-fund", "campaign-ai-fund")
    store.create(make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET, lineage_binding="main", retry_budget=3,
    ))
    daemon = CampaignDaemon(state_dir)
    monkeypatch.setattr(daemon, "_validate_canonical_binding", lambda _state, _state_dir: None)
    daemon.register(CampaignDriverSpec(
        "project-ai-fund", "campaign-ai-fund", str(state_dir),
        (sys.executable, str(probe)), (sys.executable, str(work)), poll_seconds=1,
    ))
    observed = []

    def sleep(seconds):
        observed.append(daemon.status())
        time.sleep(min(seconds, 1.1))

    daemon.sleep = sleep
    daemon.run_forever(max_loops=3)
    assert any(item.runner_active and item.campaigns_waiting == 1 for item in observed)
    final = store.load()
    assert final.status.value == "COMPLETE"
    assert [event.event_type for event in final.events].count("WAKE") == 1


def test_daemon_status_and_single_instance_lock(tmp_path):
    daemon = CampaignDaemon(tmp_path)
    daemon._acquire_lock()
    try:
        assert daemon.status().runner_active is False
        other = CampaignDaemon(tmp_path)
        try:
            other._acquire_lock()
        except Exception as exc:
            assert "another campaign daemon" in str(exc)
        else:
            raise AssertionError("second daemon acquired the lock")
    finally:
        daemon._release_lock()


def test_daemon_retires_wait_with_wrong_canonical_target(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    store = CampaignStore(state_dir, "project-ai-fund", "campaign-ai-fund")
    store.create(make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET, lineage_binding="main:" + TARGET,
        retry_budget=3,
    ))
    from agf_orchestrator.campaign_runner import PersistentCampaignRunner

    clock = datetime.now(UTC)
    PersistentCampaignRunner(store).tick(
        lambda _state: True,
        lambda _state: StepResult(
            "WAIT",
            WaitRequest(
                CampaignStatus.WAITING_CI,
                "ci",
                "run",
                "pass",
                timestamp(clock - timedelta(seconds=1)),
            ),
        ),
    )
    daemon = CampaignDaemon(state_dir)
    monkeypatch.setattr(
        daemon,
        "_validate_canonical_binding",
        lambda _state, _state_dir: (_ for _ in ()).throw(
            CanonicalBindingError("wrong target SHA")
        ),
    )
    daemon.register(CampaignDriverSpec(
        "project-ai-fund", "campaign-ai-fund", str(state_dir),
        (sys.executable, "probe.py"), (sys.executable, "work.py"), 1,
    ))
    daemon.run_forever(max_loops=1)
    assert store.load().status.value == "BLOCKED_NON_RETRYABLE"


def test_daemon_rejects_lineage_binding_for_another_target(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    store = CampaignStore(state_dir, "project-ai-fund", "campaign-ai-fund")
    state = make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET,
        lineage_binding="ai-fund:main:" + "b" * 40,
        retry_budget=3,
    )
    store.create(state)
    daemon = CampaignDaemon(state_dir)
    (state_dir / "projects.json").write_text("{}")
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.ProjectRegistry.verify_read_only",
        lambda _registry, _project: SimpleNamespace(
            status=SimpleNamespace(value="ACTIVE"),
            name="ai-fund",
            repository_root=str(tmp_path),
            default_branch="main",
            current_head_sha=TARGET,
        ),
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon._git",
        lambda _root, *args: "main" if args[0] == "branch" else TARGET,
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.SessionStore.load",
        lambda _store, _session: SimpleNamespace(
            project_id="project-ai-fund",
            base_sha=TARGET,
            status=SimpleNamespace(value="READY"),
            artifact_hashes={"canonical_target": TARGET},
        ),
    )
    with pytest.raises(CanonicalBindingError, match="lineage binding"):
        daemon._validate_canonical_binding(state, state_dir)


@pytest.mark.parametrize("status", ["BLOCKED", "HUMAN_REQUIRED", "FAILED", "STALE"])
def test_daemon_rejects_non_executable_session_binding(tmp_path, monkeypatch, status):
    state_dir = tmp_path / "state"
    daemon = CampaignDaemon(state_dir)
    state_dir.mkdir()
    (state_dir / "projects.json").write_text("{}")
    state = make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET,
        lineage_binding="ai-fund:main:" + TARGET,
        retry_budget=3,
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.ProjectRegistry.verify_read_only",
        lambda _registry, _project: SimpleNamespace(
            status=SimpleNamespace(value="ACTIVE"),
            name="ai-fund",
            repository_root=str(tmp_path),
            default_branch="main",
            current_head_sha=TARGET,
        ),
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon._git",
        lambda _root, *args: "main" if args[0] == "branch" else TARGET,
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.SessionStore.load",
        lambda _store, _session: SimpleNamespace(
            project_id="project-ai-fund",
            base_sha=TARGET,
            status=SimpleNamespace(value=status),
            artifact_hashes={"canonical_target": TARGET},
        ),
    )
    with pytest.raises(CanonicalBindingError, match="session binding"):
        daemon._validate_canonical_binding(state, state_dir)


def test_stale_terminal_campaign_is_preserved_and_never_reactivated(tmp_path):
    state_dir = tmp_path / "state"
    store = CampaignStore(state_dir, "project-ai-fund", "campaign-ai-fund")
    store.create(make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET, lineage_binding="main", retry_budget=3,
    ))
    from agf_orchestrator.campaign_runner import PersistentCampaignRunner, StepResult
    PersistentCampaignRunner(store).tick(lambda _state: True, lambda _state: StepResult("COMPLETE"))
    daemon = CampaignDaemon(state_dir)
    daemon.register(CampaignDriverSpec(
        "project-ai-fund", "campaign-ai-fund", str(state_dir),
        (sys.executable, "probe.py"), (sys.executable, "work.py"), 1,
    ))
    daemon.run_forever(max_loops=1)
    final = store.load()
    assert final.status is CampaignStatus.COMPLETE
    assert final.event_sequence == 2


def test_launchd_plist_is_user_scoped_and_keepalive(tmp_path):
    plist = render_launchd_plist(
        label="com.example.runner", program="agf-orchestrator",
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
    )
    assert "KeepAlive" in plist
    assert "campaign-runner" in plist
    try:
        render_launchd_plist(label="bad&label", program="runner", state_dir="s", log_dir="l")
    except CampaignDaemonError:
        pass
    else:
        raise AssertionError("unsafe launchd value accepted")


def test_rebind_interpreters_updates_all_python_driver_commands(tmp_path):
    runtime = tmp_path / "python"
    runtime.write_text("#!/bin/sh\n")
    runtime.chmod(0o700)
    daemon = CampaignDaemon(tmp_path)
    daemon.register(CampaignDriverSpec(
        "project-ai-fund", "campaign-ai-fund", str(tmp_path),
        ("/protected/.venv/bin/python", "probe.py"),
        ("/protected/.venv/bin/python", "work.py"), 30,
    ))
    assert daemon.rebind_interpreters(str(runtime)) == 1
    spec = daemon._load_specs()[0]
    assert spec.probe_command[0] == str(runtime)
    assert spec.work_command[0] == str(runtime)


def test_driver_rejects_direct_gh_merge_adapter(tmp_path):
    with pytest.raises(CampaignDaemonError, match="ExternalActionExecutor"):
        CampaignDriverSpec(
            "project-ai-fund", "campaign-ai-fund", str(tmp_path),
            (sys.executable, "probe.py"),
            ("gh", "pr", "merge", "1"), 30,
        ).validate()


def test_driver_rejects_direct_git_push_adapter(tmp_path):
    with pytest.raises(CampaignDaemonError, match="ExternalActionExecutor"):
        CampaignDriverSpec(
            "project-ai-fund", "campaign-ai-fund", str(tmp_path),
            (sys.executable, "probe.py"),
            ("git", "push", "origin", "main"), 30,
        ).validate()
