"""Focused tests for the owner-controlled, free-first mission pilot."""

import json
import subprocess
import sys
import tempfile
import unittest
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

        def mock_model(prompt, *, provider, model):
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

    def test_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            agf_chat.safe_target(self.repo, "../../outside.py")

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


if __name__ == "__main__":
    unittest.main()
