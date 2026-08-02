import subprocess

from agf_orchestrator.adapters import openhands as openhands_module
from agf_orchestrator.adapters.openhands import OpenHandsAdapter


def test_instruction_contains_exact_safety_context(tmp_path):
    instruction = OpenHandsAdapter().build_instruction(
        repository=str(tmp_path),
        task_id="task-001",
        title="Update file",
        objective="Replace before with after",
        allowed_paths=["message.txt"],
        acceptance_criteria=["exact content is after"],
        validation_commands=['python -B -c "assert True"'],
        stop_conditions=["scope expansion"],
    )
    assert "AGF role: Implementer" in instruction
    assert "message.txt" in instruction
    assert "Do not commit, push, create a PR, merge, or release" in instruction
    assert "HUMAN_REQUIRED" in instruction


def test_fake_openhands_cli_success_and_exact_workspace(tmp_path):
    fake = tmp_path / "fake-openhands"
    fake.write_text("#!/bin/sh\nprintf 'cwd=%s\\n' \"$PWD\"\nprintf 'fake output\\n'\n")
    fake.chmod(0o755)
    result = OpenHandsAdapter(executable=str(fake), timeout=2).execute("instruction", str(tmp_path))
    assert result.exit_code == 0
    assert f"cwd={tmp_path}" in result.stdout_summary
    assert "fake output" in result.stdout_summary


def test_fake_openhands_cli_failure_and_missing_binary_are_safe(tmp_path):
    fake = tmp_path / "fake-openhands"
    fake.write_text("#!/bin/sh\nprintf 'TOKEN=ghp_12345678901234567890\\n'\nexit 7\n")
    fake.chmod(0o755)
    failed = OpenHandsAdapter(executable=str(fake), timeout=2).execute("instruction", str(tmp_path))
    assert failed.exit_code == 7
    assert "ghp_12345678901234567890" not in failed.stdout_summary
    missing = OpenHandsAdapter(executable=str(tmp_path / "missing")).execute(
        "instruction", str(tmp_path)
    )
    assert missing.human_required is True
    assert missing.transport_error == "OPENHANDS_PROCESS_FAILED"


def test_timeout_and_secret_environment_filter(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        raise subprocess.TimeoutExpired(command, 1, output=b"secret", stderr=b"timeout")

    monkeypatch.setenv("OPENHANDS_API_KEY", "do-not-forward")
    monkeypatch.setenv("TOKEN_SHOULD_NOT_PASS", "secret")
    monkeypatch.setattr(openhands_module.subprocess, "run", fake_run)
    result = OpenHandsAdapter(executable="openhands", timeout=1).execute(
        "instruction", str(tmp_path)
    )
    assert result.timed_out is True
    assert "OPENHANDS_API_KEY" not in captured["env"]
    assert "TOKEN_SHOULD_NOT_PASS" not in captured["env"]
    assert captured["command"][:4] == [
        "openhands",
        "--headless",
        "--json",
        "--exit-without-confirmation",
    ]
