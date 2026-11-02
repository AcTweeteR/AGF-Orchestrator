"""Full-project runner checks: path isolation, scoped multi-file patches and retry."""

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
    reply = {"model": "qwen3.5:9b-q4_K_M", "choices": [{"message": {"content": ""}}],
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
    with pytest.raises(ValueError, match="outside owner-selected scope"):
        agf_full.chat.main()
    assert not list((tmp_path / "state").glob("*.json"))
    tasks.write_text(json.dumps([{
        "files": ["logic.py"], "instruction": "fix answer", "tests": [sys.executable],
    }]))
    agf_full.chat.main()
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]
    state = json.loads((tmp_path / "state" / (mission_id + ".json")).read_text())
    assert state["scope"] == "logic.py"
