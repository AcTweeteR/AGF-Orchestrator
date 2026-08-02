import subprocess

from test_delivery import plan_for, setup_repo

from agf_orchestrator.adapters import openhands as openhands_module
from agf_orchestrator.adapters.codex import CodexProcessResult
from agf_orchestrator.adapters.openhands import OpenHandsAdapter, parse_openhands_output
from agf_orchestrator.executor import Executor


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


def test_json_terminal_success_and_failure_are_interpreted():
    success = parse_openhands_output('{"state":"finished","message":"done"}\n')
    assert success.status_code is None
    assert success.human_required is False
    failed = parse_openhands_output('{"status":"failed","error":"task failed"}\n')
    assert failed.status_code == "OPENHANDS_TASK_FAILED"
    assert failed.human_required is False


def test_json_missing_terminal_or_malformed_output_requires_human():
    no_terminal = parse_openhands_output('{"event":"message","message":"working"}\n')
    malformed = parse_openhands_output("not json\n")
    assert no_terminal.status_code == "OPENHANDS_NO_TERMINAL_STATE"
    assert malformed.status_code == "OPENHANDS_JSON_INVALID"
    assert no_terminal.human_required is True
    assert malformed.human_required is True


def test_json_interaction_and_contradictory_states_require_human():
    interaction = parse_openhands_output('{"state":"awaiting_confirmation"}\n')
    contradictory = parse_openhands_output('{"state":"finished"}\n{"state":"failed"}\n')
    assert interaction.status_code == "OPENHANDS_INTERACTION_REQUIRED"
    assert contradictory.status_code == "OPENHANDS_CONTRADICTORY_TERMINAL_STATE"
    assert interaction.human_required is True
    assert contradictory.human_required is True


def test_configuration_and_stderr_diagnostics_do_not_create_success():
    configuration = parse_openhands_output(
        "Headless mode requires existing settings. Please run openhands to configure your settings."
    )
    stderr_only = parse_openhands_output('{"state":"running"}\n', "provider unavailable")
    assert configuration.status_code == "OPENHANDS_CONFIGURATION_REQUIRED"
    assert configuration.human_required is True
    assert stderr_only.status_code == "OPENHANDS_NO_TERMINAL_STATE"


def test_executor_rejects_openhands_exit_zero_without_changes(tmp_path):
    class NoopOpenHands:
        name = "openhands"

        def build_instruction(self, **kwargs):
            return "instruction"

        def execute(self, instruction, repository, *, sandbox="workspace-write"):
            return CodexProcessResult("fake", 0, '{"state":"finished"}', "")

    root = setup_repo(tmp_path)
    result = Executor(adapter=NoopOpenHands()).execute(
        plan_for(root), "task-001", str(root), dry_run=False
    )
    assert result.status.value == "FAILED"
    assert "OPENHANDS_NO_CHANGES" in result.blocking_issues
