"""Full-project runner checks: path isolation, scoped multi-file patches and retry."""

import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import agf_full


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "logic.py").write_text("def answer():\n    return 0\n")
    (root / "addon.xml").write_text('<addon id="a" name="Before"/>\n')
    (root / "test_logic.py").write_text(
        "import unittest\nfrom logic import answer\n"
        "import xml.etree.ElementTree as ET\n"
        "class TestCode(unittest.TestCase):\n"
        "    def test_both(self):\n"
        "        self.assertEqual(answer(), 42)\n"
        "        self.assertEqual(ET.parse('addon.xml').getroot().attrib['name'],'After')\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=AGF Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return root


def test_rejects_unplanned_file_and_path_traversal(repo):
    task = {
        "files": ["../../no.py"],
        "instruction": "unsafe",
        "tests": [sys.executable, "-m", "unittest"],
    }
    with pytest.raises(ValueError):
        agf_full.check_task(repo, task)


def test_project_mission_rejects_paid_model_before_dispatch(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    with pytest.raises(ValueError, match="local Ollama"):
        agf_full.start(tmp_path / "state", repo, objective="Fix the bug", tasks_path=None,
                       tests=[sys.executable], model="openai/paid")
    assert not (tmp_path / "worktrees").exists()
    with pytest.raises(ValueError, match="local Ollama"):
        agf_full.route_json("test", purpose="planning", model="openai/paid")


def test_multi_file_patch_provenance(repo):
    task = {
        "files": ["logic.py", "addon.xml"],
        "instruction": "implement both",
        "tests": [sys.executable, "-m", "unittest", "test_logic"],
    }
    result = {
        "changes": [
            {"file": "logic.py", "find": "return 0", "replace": "return 42"},
            {"file": "addon.xml", "find": 'name="Before"', "replace": 'name="After"'},
        ]
    }
    with patch.object(agf_full, "route_json", return_value=(result, {"provider": "ollama"})):
        edits, evidence = agf_full.propose_edits(repo, task, "")
    assert edits["logic.py"].endswith("return 42\n")
    assert 'name="After"' in edits["addon.xml"]
    assert evidence["provider"] == "ollama"
    for name, source in edits.items():
        (repo / name).write_text(source)
    assert agf_full.check_edits(repo, task["files"])[0]
    assert agf_full.chat.execute_tests(repo, task["tests"])[0]


def test_rejects_duplicate_patch_anchor(repo):
    task = {
        "files": ["logic.py"],
        "instruction": "implement",
        "tests": [sys.executable, "-m", "unittest"],
    }
    bad = {"changes": [{"file": "logic.py", "find": "  ", "replace": "x"}]}
    with patch.object(agf_full, "route_json", return_value=(bad, {})):
        with pytest.raises(ValueError, match="nonunique"):
            agf_full.propose_edits(repo, task, "")


def test_durable_mission_isolated_from_caller_and_committed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    task = {
        "files": ["logic.py", "addon.xml"],
        "instruction": "fix both",
        "tests": [sys.executable, "-m", "unittest", "-q", "test_logic"],
    }
    tasksfile = tmp_path / "tasks.json"
    tasksfile.write_text(json.dumps([task]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir,
        repo,
        objective=None,
        tasks_path=tasksfile,
        tests=task["tests"],
        model="ollama/local",
    )
    assert (repo / "logic.py").read_text().endswith("return 0\n")
    worktree = Path(queued["worktree"])
    assert worktree.is_dir()
    attempts = [0]

    def fake_changes(worktree, task, failure, **kwargs):
        assert kwargs["model"] == "ollama/local"
        attempts[0] += 1
        if attempts[0] == 1:
            return {"logic.py": "def answer():\n    return 0\n"}, {
                "provider": "ollama",
                "model": "local",
                "prompt_tokens": 1,
            }
        return {
            "logic.py": "def answer():\n    return 42\n",
            "addon.xml": '<addon id="a" name="After"/>\n',
        }, {
            "provider": "ollama",
            "model": "local",
            "prompt_tokens": 1,
        }

    with patch.object(agf_full, "propose_edits", side_effect=fake_changes):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "COMPLETED"
    assert result["index"] == 1
    assert any(e.get("passed") is False for e in result["events"])
    assert any(e.get("passed") is True for e in result["events"])
    assert (repo / "logic.py").read_text().endswith("return 0\n")
    assert (worktree / "logic.py").read_text().endswith("return 42\n")
    untracked = subprocess.check_output(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        text=True,
    ).splitlines()
    assert not [line for line in untracked if "__pycache__" not in line]

    assert len(result["usage"]) == 2


def test_dependent_tasks_only_require_final_integrated_tests(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tests = [sys.executable, "-m", "unittest", "-q", "test_logic"]
    work = [
        {"files": ["logic.py"], "instruction": "Implement answer", "tests": tests},
        {"files": ["addon.xml"], "instruction": "Rename addon", "tests": tests},
    ]
    tasksfile = tmp_path / "tasks.json"
    tasksfile.write_text(json.dumps(work))
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir, repo, objective=None, tasks_path=tasksfile, tests=tests, model="ollama/local"
    )

    def edits(root, task, previous, **kwargs):
        if "logic.py" in task["files"]:
            return {"logic.py": "def answer():\n    return 42\n"}, {
                "provider": "ollama",
                "model": "local",
                "prompt_tokens": 3,
            }
        return {"addon.xml": '<addon id="a" name="After"/>\n'}, {
            "provider": "ollama",
            "model": "local",
            "prompt_tokens": 3,
        }

    with patch.object(agf_full, "propose_edits", side_effect=edits):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "COMPLETED"
    assert result["index"] == 2
    assert len([e for e in result["events"] if e.get("committed")]) == 2
    assert (repo / "logic.py").read_text().endswith("return 0\n")


def test_approved_unittest_command_cannot_pass_with_zero_tests(tmp_path):
    ok, evidence = agf_full.chat.execute_tests(
        tmp_path, [sys.executable, "-m", "unittest", "discover"]
    )
    assert not ok
    assert "Ran 0 tests" in evidence


def test_planning_is_persisted_before_dispatch_and_interruption_is_recoverable(
    repo, tmp_path, monkeypatch,
):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir, repo, objective="Fix both files", tasks_path=None,
        tests=[sys.executable, "-m", "unittest", "-q", "test_logic"], model="ollama/local",
    )
    path = state_dir / (queued["mission_id"] + ".json")
    assert json.loads(path.read_text())["status"] == "PENDING"
    with patch.object(agf_full, "plan", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            agf_full.run_mission(state_dir, queued["mission_id"])
    assert json.loads(path.read_text())["status"] == "PLANNING"
    task = {"files": ["logic.py", "addon.xml"], "instruction": "fix both",
            "tests": [sys.executable, "-m", "unittest", "-q", "test_logic"]}
    changes = {"logic.py": "def answer():\n    return 42\n",
               "addon.xml": '<addon id="a" name="After"/>\n'}
    with patch.object(agf_full, "plan", return_value=([task], {"provider": "ollama"})), \
         patch.object(agf_full, "propose_edits", return_value=(changes, {
             "provider": "ollama", "model": "local",
         })):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "COMPLETED"
    assert result["mission_id"] == queued["mission_id"]
    assert result["planning_attempts"] == 2


def test_interrupted_planning_has_bounded_recovery(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir, repo, objective="Fix both files", tasks_path=None,
        tests=[sys.executable, "-m", "unittest"], model="ollama/local",
    )
    with patch.object(agf_full, "plan", side_effect=KeyboardInterrupt):
        for _ in range(2):
            with pytest.raises(KeyboardInterrupt):
                agf_full.run_mission(state_dir, queued["mission_id"])
    with patch.object(agf_full, "plan", side_effect=AssertionError("unexpected redispatch")):
        blocked = agf_full.run_mission(state_dir, queued["mission_id"])
    assert blocked["status"] == "BLOCKED"
    assert blocked["planning_attempts"] == 2


def test_killed_planner_can_be_recovered_after_process_restart(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective="Fix both files", tasks_path=None,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    code = (
        "import sys,time; from pathlib import Path; from tools import agf_full; "
        "agf_full.plan=lambda *a,**k: time.sleep(60); "
        "agf_full.run_mission(Path(sys.argv[1]),sys.argv[2])"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(state_dir), queued["mission_id"]])
    try:
        deadline = time.monotonic() + 10
        while json.loads(path.read_text())["status"] != "PLANNING":
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        process.kill()
        process.wait(timeout=5)
    assert json.loads(path.read_text())["planning_attempts"] == 1
    with patch.object(agf_full, "plan", side_effect=ValueError("recovered planner invoked")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "BLOCKED"
    assert result["planning_attempts"] == 2
    assert "recovered planner invoked" in result["reason"]


def test_planning_failure_keeps_mission_and_reason(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir, repo, objective="Fix both files", tasks_path=None,
        tests=[sys.executable, "-m", "unittest"], model="ollama/local",
    )
    with patch.object(agf_full, "plan", side_effect=ValueError("provider unavailable")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    saved = json.loads((state_dir / (queued["mission_id"] + ".json")).read_text())
    assert saved["status"] == result["status"] == "BLOCKED"
    assert "provider unavailable" in saved["reason"]
    assert Path(saved["worktree"]).is_dir()


def test_stop_during_planning_failure_is_preserved(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(
        state_dir, repo, objective="Fix both files", tasks_path=None,
        tests=[sys.executable, "-m", "unittest"], model="ollama/local",
    )
    path = state_dir / (queued["mission_id"] + ".json")

    def stop_then_fail(*args, **kwargs):
        current = json.loads(path.read_text())
        current["status"] = "STOPPED"
        agf_full.chat.write_json(path, current)
        raise ValueError("provider unavailable")

    with patch.object(agf_full, "plan", side_effect=stop_then_fail):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "STOPPED"
    assert json.loads(path.read_text())["status"] == "STOPPED"


def test_stop_during_successful_planning_does_not_install_plan(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective="Fix both files", tasks_path=None,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    task = {"files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable]}

    def stop_then_return(*args, **kwargs):
        current = json.loads(path.read_text())
        current.update(status="STOPPED", owner_note="retain")
        agf_full.chat.write_json(path, current)
        return [task], {"provider": "ollama"}

    with patch.object(agf_full, "plan", side_effect=stop_then_return):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "STOPPED" and result["tasks"] is None
    assert result["owner_note"] == "retain" and "pid" not in result
    assert result["usage"] == [{"provider": "ollama"}]


def test_stop_during_execution_clears_pid(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")

    def stop_then_edit(*args, **kwargs):
        current = json.loads(path.read_text())
        current.update(status="STOPPED", owner_note="retain")
        agf_full.chat.write_json(path, current)
        return {"logic.py": "def answer():\n    return 42\n"}, {
            "provider": "ollama", "model": "local",
        }

    with patch.object(agf_full, "propose_edits", side_effect=stop_then_edit), \
         patch.object(agf_full.chat, "execute_tests", return_value=(True, "passed")) as tests:
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "STOPPED" and "pid" not in result
    assert result["owner_note"] == "retain"
    tests.assert_not_called()
    assert "return 0" in (Path(result["worktree"]) / "logic.py").read_text()


@pytest.mark.parametrize("test_passes", [True, False])
def test_pause_waits_for_inflight_task_without_discarding_edits(
    repo, tmp_path, monkeypatch, capsys, test_passes,
):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")

    def request_pause(*args, **kwargs):
        monkeypatch.setattr(sys, "argv", ["agf-chat", "--state-dir", str(state_dir),
                                         "pause", queued["mission_id"]])
        agf_full.chat.main()
        assert json.loads(capsys.readouterr().out)["status"] == "PAUSING"
        return {"logic.py": "def answer():\n    return 42\n"}, {
            "provider": "ollama", "model": "local",
        }

    with patch.object(agf_full, "propose_edits", side_effect=request_pause), \
         patch.object(agf_full.chat, "execute_tests", return_value=(test_passes, "test result")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == ("PAUSED" if test_passes else "HUMAN_REQUIRED")
    assert (Path(queued["worktree"]) / "logic.py").read_text().endswith("return 42\n")
    assert result["index"] == (1 if test_passes else 0)
    assert any(event.get("committed") for event in result["events"]) == test_passes


def test_stop_after_validation_prevents_local_commit(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    original_write = agf_full.chat.write_json

    def stop_after_validation(target, record):
        original_write(target, record)
        if any(event.get("passed") is True for event in record.get("events", [])):
            with agf_full.chat.control_lock(path):
                current = json.loads(path.read_text())
                current.update(status="STOPPED", owner_note="retain")
                agf_full.chat._write_json_unlocked(path, current)

    changes = {"logic.py": "def answer():\n    return 42\n"}
    with patch.object(agf_full.chat, "write_json", side_effect=stop_after_validation), \
         patch.object(agf_full, "propose_edits", return_value=(changes, {
             "provider": "ollama", "model": "local",
         })), patch.object(agf_full.chat, "execute_tests", return_value=(True, "passed")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "STOPPED" and result["index"] == 0
    assert result["owner_note"] == "retain" and "pid" not in result
    assert not any(event.get("committed") for event in result["events"])


def test_stale_worker_write_cannot_erase_stop(tmp_path):
    path = tmp_path / "mission.json"
    stopped = {"engine": "full", "status": "STOPPED", "owner_note": "retain",
               "usage": [], "events": []}
    agf_full.chat.write_json(path, stopped)
    stale = {"engine": "full", "status": "RUNNING", "usage": [], "events": []}
    agf_full.chat.write_json(path, stale)
    assert stale["status"] == "STOPPED"
    assert json.loads(path.read_text())["owner_note"] == "retain"


def test_resume_rejects_dirty_worktree_and_pause_cannot_clear_gate(
    repo, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    state = json.loads(path.read_text())
    state["status"] = "PAUSED"
    agf_full.chat.write_json(path, state)
    (Path(queued["worktree"]) / "logic.py").write_text("unreconciled change")
    monkeypatch.setattr(sys, "argv", ["agf-chat", "--state-dir", str(state_dir),
                                     "resume", queued["mission_id"]])
    agf_full.chat.main()
    assert json.loads(capsys.readouterr().out)["status"] == "HUMAN_REQUIRED"
    monkeypatch.setattr(sys, "argv", ["agf-chat", "--state-dir", str(state_dir),
                                     "pause", queued["mission_id"]])
    with pytest.raises(SystemExit, match="does not permit"):
        agf_full.chat.main()
    assert json.loads(path.read_text())["status"] == "HUMAN_REQUIRED"


def test_resume_does_not_cancel_pause_while_worker_active(tmp_path, monkeypatch, capsys):
    path = tmp_path / "active.json"
    agf_full.chat.write_json(path, {"mission_id": "active", "status": "PAUSED"})
    monkeypatch.setattr(sys, "argv", ["agf-chat", "--state-dir", str(tmp_path),
                                     "resume", "active"])
    with (tmp_path / "active.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agf_full.chat.main()
    assert json.loads(capsys.readouterr().out)["status"] == "ALREADY_RUNNING"
    assert json.loads(path.read_text())["status"] == "PAUSED"


def test_pause_keeps_legacy_runner_status_compatible(tmp_path, monkeypatch, capsys):
    path = tmp_path / "legacy.json"
    agf_full.chat.write_json(path, {"mission_id": "legacy", "status": "RUNNING",
                                   "index": 0, "usage": []})
    monkeypatch.setattr(sys, "argv", ["agf-chat", "--state-dir", str(tmp_path),
                                     "pause", "legacy"])
    agf_full.chat.main()
    assert json.loads(capsys.readouterr().out)["status"] == "PAUSED"
    assert json.loads(path.read_text())["status"] == "PAUSED"


def test_stop_during_execution_error_preserves_owner_fields(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")

    def stop_then_fail(*args, **kwargs):
        current = json.loads(path.read_text())
        current.update(status="STOPPED", owner_note="retain")
        agf_full.chat.write_json(path, current)
        raise ValueError("provider failed")

    with patch.object(agf_full, "propose_edits", side_effect=stop_then_fail):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "STOPPED" and result["owner_note"] == "retain"
    assert "pid" not in result and result["events"][-1]["error"] == "provider failed"


def test_interrupted_execution_does_not_redispatch(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    state = json.loads(path.read_text())
    state["status"] = "RUNNING"
    agf_full.chat.write_json(path, state)
    with patch.object(agf_full, "propose_edits", side_effect=AssertionError("redispatch")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
        assert agf_full.run_mission(state_dir, queued["mission_id"])["status"] == "HUMAN_REQUIRED"
    assert result["status"] == "HUMAN_REQUIRED"
    assert result["index"] == 0


def test_resume_does_not_reset_exhausted_task_attempts(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local", max_attempts=1)
    with patch.object(agf_full, "propose_edits", side_effect=ValueError("failure")) as dispatch:
        blocked = agf_full.run_mission(state_dir, queued["mission_id"])
    assert blocked["status"] == "BLOCKED" and blocked["attempt"] == 1
    assert dispatch.call_count == 1
    blocked["status"] = "PENDING"  # The CLI's existing resume transition.
    agf_full.chat.write_json(state_dir / (queued["mission_id"] + ".json"), blocked)
    with patch.object(agf_full, "propose_edits", side_effect=AssertionError("redispatch")):
        exhausted = agf_full.run_mission(state_dir, queued["mission_id"])
    assert exhausted["status"] == "BLOCKED" and exhausted["attempt"] == 1


def test_legacy_failure_events_count_against_attempt_budget(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local", max_attempts=1)
    path = state_dir / (queued["mission_id"] + ".json")
    legacy = json.loads(path.read_text())
    legacy["events"].append({"task": 0, "attempt": 1, "error": "old failure"})
    agf_full.chat.write_json(path, legacy)
    with patch.object(agf_full, "propose_edits", side_effect=AssertionError("redispatch")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "BLOCKED" and result["attempt"] == 1


def test_invalid_model_patch_preserves_usage(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local", max_attempts=1)
    usage = {"provider": "ollama", "model": "local", "completion_tokens": 12}
    response = {"changes": [{"file": "logic.py", "find": "missing", "replace": "x"}]}
    with patch.object(agf_full, "route_json", return_value=(response, usage)):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "BLOCKED"
    saved = json.loads((state_dir / (queued["mission_id"] + ".json")).read_text())
    assert saved["usage"] == [usage]
    assert "missing edit anchor" in saved["events"][0]["error"]


def test_empty_model_response_records_usage_before_rejection(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local", max_attempts=1)
    reply = {"model": "local", "choices": [{"message": {"content": ""}}],
             "usage": {"prompt_tokens": 7, "completion_tokens": 13}}
    with patch.object(agf_full.chat, "request", return_value=reply) as request:
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "BLOCKED"
    assert result["usage"][0]["completion_tokens"] == 13
    assert request.call_count == 1


def test_empty_planning_response_records_usage(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective="Fix both files", tasks_path=None,
                            tests=[sys.executable], model="ollama/local")
    reply = {"model": "local", "choices": [{"message": {"content": ""}}],
             "usage": {"prompt_tokens": 9, "completion_tokens": 17}}
    with patch.object(agf_full.chat, "request", return_value=reply):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "BLOCKED"
    assert result["usage"][0]["completion_tokens"] == 17


def test_cli_scope_restricts_owner_supplied_tasks(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["addon.xml"], "instruction": "rename addon", "tests": [sys.executable],
    }]))
    monkeypatch.setattr(sys, "argv", [
        "agf-chat", "--state-dir", str(tmp_path / "state"), "start", "--project-wide",
        "--repository", str(repo), "--tasks", str(tasks), "--scope", "logic.py",
    ])
    agf_full.chat.main()
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "BLOCKED"
    assert json.loads((tmp_path / "state" / (rejected["mission_id"] + ".json")).read_text())[
        "status"
    ] == "BLOCKED"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    agf_full.chat.main()
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]
    state = json.loads((tmp_path / "state" / (mission_id + ".json")).read_text())
    assert state["scope"] == "logic.py"

def test_planning_failure_keeps_queryable_mission(repo, tmp_path, monkeypatch):
    """A failed planner must not silently erase the accepted mission."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    with patch.object(agf_full, "plan", side_effect=TimeoutError("planner timed out")):
        result = agf_full.start(
            state_dir,
            repo,
            objective="Improve answer calculation",
            tasks_path=None,
            tests=[sys.executable, "-m", "unittest", "test_logic"],
            model="ollama/qwen3.5:9b-q4_K_M",
            background=False,
        )
        result = agf_full.run_mission(state_dir, result["mission_id"])
    assert result["status"] == "BLOCKED"
    persisted = json.loads((state_dir / (result["mission_id"] + ".json")).read_text())
    assert persisted["status"] == "BLOCKED"
    assert "planner timed out" in persisted["reason"]
    assert persisted["mission_id"] == result["mission_id"]
    assert (tmp_path / "worktrees" / result["mission_id"]).exists()


def test_planning_record_is_saved_before_model_call(repo, tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")

    def check_record(worktree, objective, tests, **kwargs):
        records = list(state_dir.glob("*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text())
        assert record["status"] == "PLANNING"
        assert record["pid"] > 0
        assert record["worktree"] == str(worktree)
        raise ValueError("controlled planning failure")

    with patch.object(agf_full, "plan", side_effect=check_record):
        result = agf_full.start(
            state_dir,
            repo,
            objective="Improve answer calculation",
            tasks_path=None,
            tests=[sys.executable, "-m", "unittest", "test_logic"],
            model="ollama/qwen3.5:9b-q4_K_M",
        )
        result = agf_full.run_mission(state_dir, result["mission_id"])
    assert result["status"] == "BLOCKED"
    recorded = json.loads((state_dir / (result["mission_id"] + ".json")).read_text())
    assert "planning_pid" not in recorded


def test_inventory_excludes_fixtures_unless_explicit(repo):
    fixture = repo / "tests" / "fixtures" / "valid_state.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"state": "ready"}\n')
    test_file = repo / "tests" / "test_report.py"
    test_file.write_text("def test_report(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    ordinary = agf_full.inventory(repo, query="Improve mission reporting")
    assert "tests/fixtures/valid_state.json" not in ordinary
    assert "tests/test_report.py" not in ordinary
    testing = agf_full.inventory(repo, query="Improve reporting tests")
    assert "tests/test_report.py" in testing
    assert "tests/fixtures/valid_state.json" not in testing
    fixtures = agf_full.inventory(repo, query="Improve reporting test fixtures")
    assert "tests/fixtures/valid_state.json" in fixtures


def test_check_task_respects_objective_approved_tests_and_rejects_unapproved(repo):
    allowed = agf_full.inventory(repo, query="Improve tests")
    task = {
        "files": ["test_logic.py"],
        "instruction": "Improve assertions",
        "tests": [sys.executable, "-m", "unittest", "test_logic"],
    }
    agf_full.check_task(repo, task, approved_files=allowed)
    with pytest.raises(ValueError, match="unavailable, or unsafe"):
        agf_full.check_task(repo, task)
    with pytest.raises(ValueError, match="unavailable, or unsafe"):
        agf_full.check_task(repo, {**task, "files": ["../outside.py"]}, approved_files=allowed)


def test_clean_branch_drift_requires_reconciliation(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective=None, tasks_path=tasks,
                            tests=[sys.executable], model="ollama/local")
    path = state_dir / (queued["mission_id"] + ".json")
    worktree = json.loads(path.read_text())["worktree"]
    subprocess.run(["git", "-C", worktree, "checkout", "-qb", "unapproved"], check=True)
    with patch.object(agf_full, "propose_edits") as dispatch:
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    dispatch.assert_not_called()
    assert result["status"] == "HUMAN_REQUIRED"


def test_validated_plan_survives_interruption_before_execution(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    state_dir = tmp_path / "state"
    queued = agf_full.start(state_dir, repo, objective="fix answer", tasks_path=None,
                            tests=[sys.executable], model="ollama/local")
    task = {"files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable]}
    original_write = agf_full.chat.write_json

    def interrupt_after_plan(path, record):
        original_write(path, record)
        if record.get("status") == "PLANNING" and record.get("tasks"):
            raise KeyboardInterrupt

    with patch.object(agf_full, "plan", return_value=([task], {})), \
            patch.object(agf_full.chat, "write_json", side_effect=interrupt_after_plan):
        with pytest.raises(KeyboardInterrupt):
            agf_full.run_mission(state_dir, queued["mission_id"])
    path = state_dir / (queued["mission_id"] + ".json")
    assert json.loads(path.read_text())["tasks"] == [task]
    with patch.object(agf_full, "plan") as planner, \
            patch.object(agf_full, "propose_edits", side_effect=ValueError("controlled")):
        agf_full.run_mission(state_dir, queued["mission_id"])
    planner.assert_not_called()
