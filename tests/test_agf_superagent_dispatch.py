"""Durable Superagent-style routing never blocks on model execution."""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

from tools import agf_chat


def _mission(state_dir: Path, mission_id: str, status: str, **extra):
    record = {"mission_id": mission_id, "engine": "full", "status": status, **extra}
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{mission_id}.json").write_text(json.dumps(record))


def test_dispatcher_launches_one_worker_without_waiting(tmp_path):
    _mission(tmp_path, "aaaaaaaaaaaaaaaa", "PENDING")
    _mission(tmp_path, "bbbbbbbbbbbbbbbb", "PENDING")
    with patch.object(agf_chat.subprocess, "Popen", return_value=Mock(pid=123456)) as popen:
        result = agf_chat.dispatch_queued_missions(tmp_path, max_workers=1)
    assert result["status"] == "WORKER_DISPATCHED"
    assert result["dispatched"] == ["aaaaaaaaaaaaaaaa"]
    assert popen.call_count == 1
    assert popen.call_args.args[0][-2:] == ["run", "aaaaaaaaaaaaaaaa"]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdin"] is agf_chat.subprocess.DEVNULL
    assert json.loads((tmp_path / "aaaaaaaaaaaaaaaa.json").read_text())["dispatch_pid"] == 123456


def test_active_executor_consumes_the_only_slot(tmp_path):
    _mission(tmp_path, "aaaaaaaaaaaaaaaa", "RUNNING", pid=os.getpid())
    _mission(tmp_path, "bbbbbbbbbbbbbbbb", "PENDING")
    with patch.object(agf_chat.subprocess, "Popen") as popen:
        result = agf_chat.dispatch_queued_missions(tmp_path, max_workers=1)
    assert result["dispatched"] == []
    assert result["active"] == 1
    popen.assert_not_called()


def test_blocked_paused_and_untrusted_records_never_spawn(tmp_path):
    _mission(tmp_path, "aaaaaaaaaaaaaaaa", "HUMAN_REQUIRED")
    _mission(tmp_path, "bbbbbbbbbbbbbbbb", "PAUSED")
    _mission(tmp_path, "cccccccccccccccc", "COMPLETED")
    (tmp_path / "not-a-valid-mission.json").write_text(
        json.dumps({"mission_id": "not-a-valid-mission", "status": "PENDING"})
    )
    with patch.object(agf_chat.subprocess, "Popen") as popen:
        result = agf_chat.dispatch_queued_missions(tmp_path, max_workers=2)
    assert result["dispatched"] == []
    popen.assert_not_called()


def test_stale_execution_can_be_relaunched_without_changing_task_scope(tmp_path):
    _mission(tmp_path, "aaaaaaaaaaaaaaaa", "RUNNING", pid=9999999)
    with patch.object(agf_chat.subprocess, "Popen", return_value=Mock(pid=1234)) as popen:
        result = agf_chat.dispatch_queued_missions(tmp_path, max_workers=1)
    assert result["dispatched"] == ["aaaaaaaaaaaaaaaa"]
    popen.assert_called_once()
    status = json.loads((tmp_path / "aaaaaaaaaaaaaaaa.json").read_text())
    assert status["status"] == "RUNNING"
    assert status["dispatch_pid"] == 1234


def test_dispatch_does_not_wait_for_executor_control_lock(tmp_path):
    """A worker holding its validation lock cannot stall the coordinator."""
    import fcntl

    _mission(tmp_path, "aaaaaaaaaaaaaaaa", "PENDING")
    ctl_path = tmp_path / "aaaaaaaaaaaaaaaa.control.lock"
    with ctl_path.open("a+") as ctl:
        fcntl.flock(ctl.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(agf_chat.subprocess, "Popen", return_value=Mock(pid=1234)) as p:
            outcome = agf_chat.dispatch_queued_missions(tmp_path, max_workers=1)
        assert outcome["dispatched"] == ["aaaaaaaaaaaaaaaa"]
        p.assert_called_once()
    saved = json.loads((tmp_path / "aaaaaaaaaaaaaaaa.json").read_text())
    assert "dispatch_pid" not in saved
