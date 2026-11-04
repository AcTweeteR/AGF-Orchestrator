"""Full-project runner checks: path isolation, scoped multi-file patches and retry."""

import json
import subprocess
import sys
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


def test_planning_failure_keeps_queryable_mission(repo, tmp_path, monkeypatch):
    """A failed planner must not silently erase the accepted mission."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    with patch.object(agf_full, "plan", side_effect=TimeoutError("planner timed out")):
        queued = agf_full.start(
            state_dir,
            repo,
            objective="Improve answer calculation",
            tasks_path=None,
            tests=[sys.executable, "-m", "unittest", "test_logic"],
            model="ollama/qwen3.5:9b-q4_K_M",
            background=False,
        )
        result = agf_full.run_mission(state_dir, queued["mission_id"])
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
        queued = agf_full.start(
            state_dir,
            repo,
            objective="Improve answer calculation",
            tasks_path=None,
            tests=[sys.executable, "-m", "unittest", "test_logic"],
            model="ollama/qwen3.5:9b-q4_K_M",
        )
        result = agf_full.run_mission(state_dir, queued["mission_id"])
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


def _queued_recovery_fixture(repo, tmp_path, monkeypatch):
    """Create an owned worktree without making a network/model call."""
    monkeypatch.setattr(agf_full, "WORKTREE_ROOT", tmp_path / "worktrees")
    test_argv = [sys.executable, "-m", "unittest", "-q", "test_logic"]
    task = {
        "files": ["logic.py", "addon.xml"],
        "instruction": "Repair the integrated sources",
        "tests": test_argv,
    }
    tasksfile = tmp_path / "recovery-tasks.json"
    tasksfile.write_text(json.dumps([task]))
    state_dir = tmp_path / "recovery-state"
    queued = agf_full.start(
        state_dir, repo, objective=None, tasks_path=tasksfile,
        tests=test_argv, model="ollama/local",
    )
    return state_dir, queued, task


def _make_recovery_commit(state_dir, queued, task, *, safe_markers=True, extra_file=False):
    """Simulate process exit after Git commit but before JSON checkpoint."""
    mid = queued["mission_id"]
    root = Path(queued["worktree"])
    (root / "logic.py").write_text("def answer():\n    return 42\n")
    (root / "addon.xml").write_text('<addon id="a" name="After"/>\n')
    files = ["logic.py", "addon.xml"]
    if extra_file:
        (root / "test_logic.py").write_text("# unapproved change\n")
        files.append("test_logic.py")
    subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True)
    trailer = (
        f"AGF-Mission: {mid}\n"
        f"AGF-Task: 1\n"
        f"AGF-Task-Digest: {agf_full._task_digest(task)}"
    ) if safe_markers else "missing AGF provenance"
    subprocess.run([
        "git", "-C", str(root),
        "-c", "user.name=AGF Test",
        "-c", "user.email=test@example.invalid",
        "commit", "-qm", "AGF free-first task 1", "-m", trailer,
    ], check=True)
    path = state_dir / f"{mid}.json"
    state = json.loads(path.read_text())
    state["status"] = "RUNNING"
    state["attempt"] = 1
    agf_full.chat.write_json(path, state)
    return root


def test_deepseek_style_commit_recovery_without_reexecuting_model(repo, tmp_path, monkeypatch):
    state_dir, queued, task = _queued_recovery_fixture(repo, tmp_path, monkeypatch)
    root = _make_recovery_commit(state_dir, queued, task)
    with patch.object(agf_full, "propose_edits", side_effect=AssertionError("model replay")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "COMPLETED", result.get("reason")
    assert result["index"] == 1
    assert any(event.get("recovered") is True for event in result["events"])
    assert result["expected_head"] == subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert (repo / "logic.py").read_text().endswith("return 0\n")


@pytest.mark.parametrize("safe_markers,extra_file", [(False, False), (True, True)])
def test_interrupted_untrusted_commit_requires_human(
    repo, tmp_path, monkeypatch, safe_markers, extra_file
):
    state_dir, queued, task = _queued_recovery_fixture(repo, tmp_path, monkeypatch)
    _make_recovery_commit(
        state_dir, queued, task, safe_markers=safe_markers, extra_file=extra_file
    )
    with patch.object(agf_full, "propose_edits", side_effect=AssertionError("model replay")):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "HUMAN_REQUIRED"
    assert result["index"] == 0


def test_clean_interrupted_attempt_resumes_with_persisted_budget(repo, tmp_path, monkeypatch):
    state_dir, queued, task = _queued_recovery_fixture(repo, tmp_path, monkeypatch)
    path = state_dir / f"{queued['mission_id']}.json"
    state = json.loads(path.read_text())
    state["status"] = "RUNNING"
    state["attempt"] = 0
    agf_full.chat.write_json(path, state)

    def simulated_model(_root, _task, _failure, **kwargs):
        return {
            "logic.py": "def answer():\n    return 42\n",
            "addon.xml": '<addon id="a" name="After"/>\n',
        }, {"provider": "ollama", "model": "local", "prompt_tokens": 1}

    with patch.object(agf_full, "propose_edits", side_effect=simulated_model):
        result = agf_full.run_mission(state_dir, queued["mission_id"])
    assert result["status"] == "COMPLETED"
    assert any(
        event.get("recovered") == "clean_interrupted_attempt"
        for event in result["events"]
    )
