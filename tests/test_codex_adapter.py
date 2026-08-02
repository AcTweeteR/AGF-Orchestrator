
import subprocess

from agf_orchestrator.adapters import codex as codex_module
from agf_orchestrator.adapters.codex import SAFE_ENV_KEYS, CodexAdapter, redact_secrets


def test_instruction_is_self_contained():
    instruction = CodexAdapter().build_instruction(
        repository="/repo",
        task_id="task-001",
        title="Update file",
        objective="Update the approved file",
        allowed_paths=["allowed.txt"],
        acceptance_criteria=["the file is updated"],
        validation_commands=["git diff --check"],
        stop_conditions=["scope expansion"],
    )
    assert "/repo" in instruction
    assert "task-001" in instruction
    assert "allowed.txt" in instruction
    assert "git diff --check" in instruction
    assert "scope expansion" in instruction


def test_secret_redaction():
    value = "API_KEY=sk-test-secret-value token=ghp_12345678901234567890"
    redacted = redact_secrets(value)
    assert "sk-test-secret-value" not in redacted
    assert "ghp_12345678901234567890" not in redacted
    assert "[REDACTED]" in redacted


def test_fake_executable_captures_output(tmp_path):
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/bin/sh\nprintf 'fake stdout\\n'\nprintf 'fake stderr\\n' >&2\n")
    fake.chmod(0o755)
    result = CodexAdapter(executable=str(fake), timeout=2).execute("instruction", str(tmp_path))
    assert result.exit_code == 0
    assert "fake stdout" in result.stdout_summary
    assert "fake stderr" in result.stderr_summary


def test_safe_environment_allowlist_excludes_secret_variables(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("TOKEN_SHOULD_NOT_PASS", "secret")
    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    CodexAdapter(executable="codex").execute("instruction", str(tmp_path))
    assert set(captured["env"]) <= SAFE_ENV_KEYS
    assert "TOKEN_SHOULD_NOT_PASS" not in captured["env"]
    assert "PATH" in captured["env"]
