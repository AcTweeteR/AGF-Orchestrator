
import os
import subprocess
import time

from agf_orchestrator.adapters import codex as codex_module
from agf_orchestrator.adapters.codex import (
    SAFE_ENV_KEYS,
    CodexAdapter,
    CodexInvocationProfile,
    _read_verified_final_message,
    discover_invocation_profile,
    redact_secrets,
)


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
    fake.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then "
        "printf 'final\\n' > \"$2\"; shift 2; else shift; fi\n"
        "done\n"
        "printf 'fake stdout\\n'\nprintf 'fake stderr\\n' >&2\n"
    )
    fake.chmod(0o755)
    result = CodexAdapter(
        executable=str(fake), timeout=2, profile=CodexInvocationProfile()
    ).execute("instruction", str(tmp_path))
    assert result.exit_code == 0
    assert "fake stdout" in result.stdout_summary
    assert "fake stderr" in result.stderr_summary
    assert result.invocation_verified is True
    assert result.output_last_message_fresh is True


def test_missing_output_last_message_is_precise(tmp_path):
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    result = CodexAdapter(str(fake), profile=CodexInvocationProfile()).execute(
        "instruction", str(tmp_path)
    )
    assert result.transport_error == "CODEX_FINAL_MESSAGE_MISSING"
    assert "output-last-message created: no" in result.transport_evidence


def test_nonzero_exit_with_fresh_final_message_is_process_failure(tmp_path):
    fake = tmp_path / "fake-codex"
    fake.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then "
        "printf '{}\\n' > \"$2\"; shift 2; else shift; fi\n"
        "done\nexit 7\n"
    )
    fake.chmod(0o755)
    result = CodexAdapter(str(fake), profile=CodexInvocationProfile()).execute(
        "instruction", str(tmp_path)
    )
    assert result.transport_error == "CODEX_PROCESS_FAILED"
    assert result.final_message == "{}\n"


def test_unresolved_executable_has_precise_error(tmp_path):
    result = CodexAdapter(str(tmp_path / "missing"), profile=CodexInvocationProfile()).execute(
        "instruction", str(tmp_path)
    )
    assert result.transport_error == "CODEX_EXECUTABLE_NOT_FOUND"
    assert result.process_started is False


def test_fresh_output_last_message_verifies_and_is_bounded(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    path = approved / "final.txt"
    started = time.time_ns()
    path.write_text('{"status":"APPROVE"}')
    message, error, evidence = _read_verified_final_message(path, approved, started)
    assert error is None
    assert message == '{"status":"APPROVE"}'
    assert "invocation verified: yes" in evidence
    assert "output-last-message bytes: 20" in evidence


def test_stale_preexisting_output_last_message_is_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    path = approved / "final.txt"
    path.write_text("stale")
    old = time.time_ns() - 10_000_000_000
    os.utime(path, ns=(old, old))
    message, error, _ = _read_verified_final_message(path, approved, time.time_ns())
    assert message is None
    assert error == "CODEX_FINAL_MESSAGE_STALE"


def test_empty_and_missing_output_last_message_are_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    missing, missing_error, _ = _read_verified_final_message(
        approved / "missing.txt", approved, time.time_ns()
    )
    empty_path = approved / "empty.txt"
    empty_path.touch()
    empty, empty_error, _ = _read_verified_final_message(
        empty_path, approved, time.time_ns() - 1_000_000_000
    )
    assert missing is None and missing_error == "CODEX_FINAL_MESSAGE_MISSING"
    assert empty is None and empty_error == "CODEX_FINAL_MESSAGE_EMPTY"


def test_output_outside_approved_directory_is_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("final")
    message, error, _ = _read_verified_final_message(outside, approved, time.time_ns())
    assert message is None
    assert error == "CODEX_FINAL_MESSAGE_UNREADABLE"


def test_output_mtime_before_invocation_is_rejected(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    path = approved / "final.txt"
    path.write_text("final")
    old = time.time_ns() - 1_000_000
    os.utime(path, ns=(old, old))
    message, error, _ = _read_verified_final_message(path, approved, time.time_ns())
    assert message is None
    assert error == "CODEX_FINAL_MESSAGE_STALE"


def test_safe_environment_allowlist_excludes_secret_variables(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("TOKEN_SHOULD_NOT_PASS", "secret")
    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    CodexAdapter(executable="codex", profile=CodexInvocationProfile()).execute(
        "instruction", str(tmp_path)
    )
    assert set(captured["env"]) <= SAFE_ENV_KEYS
    assert "TOKEN_SHOULD_NOT_PASS" not in captured["env"]
    assert "PATH" in captured["env"]


def test_discovery_places_global_flags_before_exec(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-2:] == ["exec", "--help"]:
            return subprocess.CompletedProcess(
                command, 0, "Run Codex non-interactively\n--sandbox\n--config", ""
            )
        return subprocess.CompletedProcess(command, 0, "exec\n--sandbox\n--config", "")

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    profile = discover_invocation_profile("codex")
    assert profile is not None
    command = profile.build_command("codex", "instruction")
    assert command.index("-c") < command.index("exec")
    assert calls == [["codex", "--help"], ["codex", "exec", "--help"]]


def test_command_never_emits_unsupported_or_dangerous_flags():
    command = CodexInvocationProfile().build_command("codex", "instruction")
    assert command == [
        "codex", "-c", 'approval_policy="never"', "-s", "workspace-write",
        "exec", "instruction",
    ]
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--ask-for-approval" not in command
    assert command.index("-c") < command.index("exec")


def test_discovery_failure_returns_human_required(monkeypatch, tmp_path):
    def fail_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, "", "unsupported")

    monkeypatch.setattr(codex_module.subprocess, "run", fail_run)
    result = CodexAdapter(executable="codex").execute("instruction", str(tmp_path))
    assert result.human_required is True
    assert result.exit_code is None
