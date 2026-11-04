"""Focused tests for the owner-controlled, free-first mission pilot."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import agf_chat


class FreeMissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "value.py").write_text("def answer():\n    return 0\n")
        (self.repo / "test_value.py").write_text(
            "import unittest\nfrom value import answer\n"
            "class TestValue(unittest.TestCase):\n"
            "    def test_answer(self): self.assertEqual(answer(), 42)\n"
        )
        self.state_dir = self.base / "state"
        self.state_dir.mkdir()
        self.id = "example1234567890"
        agf_chat.write_json(
            self.state_dir / f"{self.id}.json",
            {
                "mission_id": self.id,
                "repository": str(self.repo),
                "status": "PENDING",
                "index": 0,
                "max_attempts": 3,
                "routes": [
                    {"provider": "omniroute", "model": "ollama/qwen"},
                    {"provider": "ollama", "model": "qwen3.5:9b-q4_K_M"},
                ],
                "tasks": [
                    {
                        "file": "value.py",
                        "instruction": "return 42",
                        "tests": [sys.executable, "-m", "unittest", "-q", "test_value"],
                    }
                ],
                "usage": [],
                "events": [],
                "api_budget_eur": 0,
                "codex_calls": 0,
            },
        )

    def test_automatic_retry_after_failed_tests_and_gateway_fallback(self):
        calls = []

        def mock_model(prompt, *, provider, model, usage_observer=None):
            calls.append((provider, model))
            if provider == "omniroute":
                raise ValueError("offline gateway")
            source = (
                "def answer():\n    return 0\n"
                if len(calls) == 2
                else "def answer():\n    return 42\n"
            )
            return {"content": source}, {
                "provider": "ollama",
                "model": model,
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "api_eur": 0,
            }

        with patch.object(agf_chat, "ask_model", side_effect=mock_model):
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["index"], 1)
        self.assertEqual([event["passed"] for event in result["events"]], [False, True])
        self.assertEqual(len(result["usage"]), 2)
        self.assertEqual(result["codex_calls"], 0)
        self.assertEqual(sum(x["api_eur"] for x in result["usage"]), 0)
        self.assertIn("return 42", (self.repo / "value.py").read_text())

    def test_natural_language_planner_uses_approved_file_only(self):
        reply = {
            "message": {
                "content": json.dumps(
                    {
                        "tasks": [
                            {"file": "value.py", "instruction": "Implement answer returning 42"}
                        ]
                    }
                )
            },
            "model": "qwen3.5:9b-q4_K_M",
            "prompt_eval_count": 40,
            "eval_count": 18,
        }
        with patch.object(agf_chat, "request", return_value=reply):
            tasks, usage = agf_chat.plan_objective(
                self.repo,
                "Make the existing answer function return 42",
                [sys.executable, "-m", "unittest", "-q", "test_value"],
            )
        self.assertEqual(tasks[0]["file"], "value.py")
        self.assertEqual(usage["api_eur"], 0)

    def test_natural_language_planner_refuses_new_paths(self):
        reply = {
            "message": {
                "content": json.dumps(
                    {"tasks": [{"file": "../../outside.py", "instruction": "Create unsafe path"}]}
                )
            }
        }
        with patch.object(agf_chat, "request", return_value=reply):
            with self.assertRaises(ValueError):
                agf_chat.plan_objective(self.repo, "Write a file", [sys.executable])

    def test_omniroute_exact_ollama_model_alias(self):
        reply = {
            "model": "qwen3.5:9b-q4_K_M",
            "choices": [{"message": {"content": json.dumps({"content": "print(42)"})}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }
        with patch.object(agf_chat, "request", return_value=reply):
            content, usage = agf_chat.ask_model(
                "write code", provider="omniroute", model="ollama/qwen3.5:9b-q4_K_M"
            )
        self.assertEqual(content["content"], "print(42)")
        self.assertEqual(usage["provider"], "omniroute/ollama")
        self.assertEqual(usage["effective_model"], "qwen3.5:9b-q4_K_M")

    def test_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            agf_chat.safe_target(self.repo, "../../outside.py")

    def test_normalizes_root_and_rejects_target_symlink(self):
        alias = self.base / "repo-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(
            agf_chat.safe_target(alias, "value.py"), (self.repo / "value.py").resolve()
        )
        (self.repo / "link.py").symlink_to(self.repo / "value.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            agf_chat.safe_target(alias, "link.py")

    def test_refuses_remote_gateway(self):
        with self.assertRaises(ValueError):
            agf_chat.request("https://example.org/llm")

    def test_status_record_survives_restart(self):
        with patch.object(
            agf_chat,
            "ask_model",
            return_value=(
                {"content": "def answer():\n    return 42\n"},
                {
                    "provider": "ollama",
                    "model": "qwen",
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "api_eur": 0,
                },
            ),
        ):
            agf_chat.run_mission(self.state_dir, self.id)
        saved = json.loads((self.state_dir / f"{self.id}.json").read_text())
        self.assertEqual(saved["status"], "COMPLETED")

    def test_objective_is_persisted_before_planning_and_resumes_after_interruption(self):
        argv = [
            "agf_chat",
            "--state-dir",
            str(self.state_dir),
            "start",
            "--repository",
            str(self.repo),
            "--objective",
            "Implement answer",
        ]
        output = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            redirect_stdout(output),
            patch.object(agf_chat, "plan_objective") as planner,
        ):
            agf_chat.main()
        planner.assert_not_called()
        mission_id = json.loads(output.getvalue())["mission_id"]
        path = self.state_dir / f"{mission_id}.json"
        self.assertEqual(json.loads(path.read_text())["status"], "PLANNING")
        with patch.object(agf_chat, "plan_objective", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                agf_chat.run_mission(self.state_dir, mission_id)
        self.assertEqual(json.loads(path.read_text())["planning_attempts"], 1)
        task = {
            "file": "value.py",
            "instruction": "return 42",
            "tests": [sys.executable, "-m", "unittest", "-q", "test_value"],
        }
        with (
            patch.object(agf_chat, "plan_objective", return_value=([task], None)),
            patch.object(
                agf_chat,
                "ask_model",
                return_value=({"content": "def answer():\n    return 42\n"}, {"model": "local"}),
            ),
        ):
            result = agf_chat.run_mission(self.state_dir, mission_id)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["planning_attempts"], 2)

    def test_interrupted_planning_cannot_exceed_budget(self):
        path = self.state_dir / f"{self.id}.json"
        record = json.loads(path.read_text())
        record.update(
            status="PLANNING", tasks=[], objective="test", planning_tests=[], planning_attempts=2
        )
        agf_chat.write_json(path, record)
        with patch.object(agf_chat, "plan_objective") as planner:
            result = agf_chat.run_mission(self.state_dir, self.id)
        planner.assert_not_called()
        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_planning_response_keeps_token_usage(self):
        path = self.state_dir / f"{self.id}.json"
        record = json.loads(path.read_text())
        record.update(status="PLANNING", tasks=[], objective="fix answer",
                      planning_tests=[sys.executable], planning_attempts=0)
        agf_chat.write_json(path, record)
        reply = {"message": {"content": "invalid"}, "model": "local",
                 "prompt_eval_count": 11, "eval_count": 7}
        with patch.object(agf_chat, "request", return_value=reply):
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["usage"][0]["completion_tokens"], 7)

    def test_invalid_execution_response_keeps_token_usage(self):
        path = self.state_dir / f"{self.id}.json"
        record = json.loads(path.read_text())
        record.update(max_attempts=1, routes=[{"provider": "ollama", "model": "local"}])
        agf_chat.write_json(path, record)
        reply = {"message": {"content": "invalid"}, "model": "local",
                 "prompt_eval_count": 4, "eval_count": 8}
        with patch.object(agf_chat, "request", return_value=reply):
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["usage"][0]["completion_tokens"], 8)

    def test_interrupted_legacy_execution_is_not_dispatched_again(self):
        path = self.state_dir / f"{self.id}.json"
        record = json.loads(path.read_text())
        record["status"] = "RUNNING"
        agf_chat.write_json(path, record)
        with patch.object(agf_chat, "ask_model") as model:
            result = agf_chat.run_mission(self.state_dir, self.id)
        model.assert_not_called()
        self.assertEqual(result["status"], "HUMAN_REQUIRED")

    def test_exhausted_legacy_attempt_budget_survives_resume(self):
        path = self.state_dir / f"{self.id}.json"
        record = json.loads(path.read_text())
        record.update(status="PENDING", attempt=3)
        agf_chat.write_json(path, record)
        with patch.object(agf_chat, "ask_model") as model:
            result = agf_chat.run_mission(self.state_dir, self.id)
        model.assert_not_called()
        self.assertEqual(result["status"], "BLOCKED")

    def test_pause_during_legacy_dispatch_preserves_source_and_status(self):
        path = self.state_dir / f"{self.id}.json"

        def pause_during_request(*args, **kwargs):
            record = json.loads(path.read_text())
            record["status"] = "PAUSED"
            agf_chat.write_json(path, record)
            return {"content": "def answer():\n    return 42\n"}, {"model": "local"}

        with patch.object(agf_chat, "ask_model", side_effect=pause_during_request):
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "PAUSED")
        self.assertIn("return 0", (self.repo / "value.py").read_text())
        stale = dict(result, status="RUNNING")
        agf_chat.write_json(path, stale)
        self.assertEqual(json.loads(path.read_text())["status"], "PAUSED")

    def test_failed_atomic_source_replace_preserves_existing_file(self):
        target = self.repo / "value.py"
        original_replace = os.replace

        def fail_target_replace(source, destination):
            if Path(destination).resolve() == target.resolve():
                raise OSError("controlled disk failure")
            return original_replace(source, destination)

        with patch.object(agf_chat, "ask_model", return_value=(
            {"content": "def answer():\n    return 42\n"}, {"model": "local"}
        )), patch.object(agf_chat.os, "replace", side_effect=fail_target_replace):
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("return 0", target.read_text())

    def test_stop_after_source_write_prevents_test_dispatch(self):
        path = self.state_dir / f"{self.id}.json"
        original_lock = agf_chat.control_lock
        stopped = False

        @contextmanager
        def stop_after_write(record_path):
            nonlocal stopped
            with original_lock(record_path):
                yield
            if not stopped and "return 42" in (self.repo / "value.py").read_text():
                stopped = True
                with original_lock(path):
                    record = json.loads(path.read_text())
                    record["status"] = "STOPPED"
                    agf_chat._write_json_unlocked(path, record)

        with patch.object(agf_chat, "control_lock", side_effect=stop_after_write), \
                patch.object(agf_chat, "ask_model", return_value=(
                    {"content": "def answer():\n    return 42\n"}, {"model": "local"}
                )), patch.object(agf_chat, "execute_tests") as tests:
            result = agf_chat.run_mission(self.state_dir, self.id)
        self.assertEqual(result["status"], "STOPPED")
        tests.assert_not_called()


if __name__ == "__main__":
    unittest.main()
