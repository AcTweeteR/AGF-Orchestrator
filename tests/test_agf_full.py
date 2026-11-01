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

    def fake_changes(worktree, task, failure):
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

    def edits(root, task, previous):
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
