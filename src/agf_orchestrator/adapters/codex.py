"""Controlled, provider-specific Codex CLI adapter."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
)
SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
}


def redact_secrets(value: str, *, limit: int = 4000) -> str:
    """Redact common secret-shaped values and cap report size."""
    redacted = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(
                lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
            )
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:limit]


@dataclass(frozen=True)
class CodexInvocationProfile:
    """Parser-verified placement for the installed Codex CLI options."""

    global_options_before_exec: bool = True

    def build_command(
        self, executable: str, instruction: str, *, sandbox: str = "workspace-write",
        final_message_path: str | None = None,
    ) -> list[str]:
        if not self.global_options_before_exec:
            raise ValueError("unsupported Codex invocation profile")
        command = [
            executable,
            "-c",
            'approval_policy="never"',
            "-s",
            sandbox,
        ]
        command.extend(["exec"])
        if final_message_path is not None:
            command.extend(["--output-last-message", final_message_path])
        command.append(instruction)
        return command


@dataclass(frozen=True)
class CodexProcessResult:
    command_summary: str
    exit_code: int | None
    stdout_summary: str
    stderr_summary: str
    timed_out: bool = False
    human_required: bool = False
    final_message: str | None = None
    transport_error: str | None = None


def _safe_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}


def discover_invocation_profile(
    executable: str, *, timeout: float = 10.0
) -> CodexInvocationProfile | None:
    """Verify option placement from this executable's own parser help."""
    environment = _safe_environment()
    try:
        root_help = subprocess.run(
            [executable, "--help"], capture_output=True, text=True,
            timeout=timeout, shell=False, env=environment,
        )
        exec_help = subprocess.run(
            [executable, "exec", "--help"], capture_output=True, text=True,
            timeout=timeout, shell=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if root_help.returncode != 0 or exec_help.returncode != 0:
        return None
    root_output = f"{root_help.stdout}\n{root_help.stderr}"
    exec_output = f"{exec_help.stdout}\n{exec_help.stderr}"
    if (
        "--sandbox" not in root_output
        or "--config" not in root_output
        or "exec" not in root_output
        or "--config" not in exec_output
        or "--sandbox" not in exec_output
    ):
        return None
    return CodexInvocationProfile()


class CodexAdapter:
    """Invoke the locally discovered Codex CLI without shell interpretation."""

    name = "codex"

    def __init__(
        self,
        executable: str = "codex",
        timeout: float = 300.0,
        profile: CodexInvocationProfile | None = None,
    ) -> None:
        self.executable = executable
        self.timeout = timeout
        self.profile = profile

    def build_instruction(
        self,
        *,
        repository: str,
        task_id: str,
        title: str,
        objective: str,
        allowed_paths: list[str],
        acceptance_criteria: list[str],
        validation_commands: list[str],
        stop_conditions: list[str],
    ) -> str:
        lines = [
            "Execute exactly one approved AGF-Orchestrator task.",
            f"Repository root: {repository}",
            f"Task ID: {task_id}",
            f"Task title: {title}",
            f"Objective: {objective}",
            "Allowed paths:",
            *[f"- {path}" for path in allowed_paths],
            "Acceptance criteria:",
            *[f"- {criterion}" for criterion in acceptance_criteria],
            "Approved validation commands:",
            *[f"- {command}" for command in validation_commands],
            "Stop conditions:",
            *[f"- {condition}" for condition in stop_conditions],
            "Do not modify files outside the allowed paths. Do not commit or push.",
        ]
        return "\n".join(lines)

    def execute(
        self,
        instruction: str,
        repository: str,
        *,
        sandbox: str = "workspace-write",
    ) -> CodexProcessResult:
        profile = self.profile or discover_invocation_profile(self.executable)
        if profile is None:
            return CodexProcessResult(
                "codex invocation syntax could not be verified", None, "",
                "Codex invocation syntax could not be verified from parser help",
                human_required=True,
            )
        final_message_file = tempfile.NamedTemporaryFile(
            prefix="agf-codex-final-", suffix=".txt", delete=False
        )
        final_message_path = final_message_file.name
        final_message_file.close()
        command = profile.build_command(
            self.executable,
            instruction,
            sandbox=sandbox,
            final_message_path=final_message_path,
        )
        summary = (
            f'codex -c approval_policy="never" -s {sandbox} '
            "exec <task-instruction>"
        )
        try:
            completed = subprocess.run(
                command,
                cwd=Path(repository),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                env=_safe_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = redact_secrets(_as_text(exc.stdout))
            stderr = redact_secrets(_as_text(exc.stderr))
            return CodexProcessResult(
                summary, None, stdout, stderr, timed_out=True,
                final_message=self._read_final_message(final_message_path),
            )
        except OSError as exc:
            self._remove_final_message(final_message_path)
            return CodexProcessResult(
                summary, None, "", redact_secrets(str(exc)),
                transport_error="CODEX_PROCESS_FAILED",
            )
        final_message = self._read_final_message(final_message_path)
        return CodexProcessResult(
            summary,
            completed.returncode,
            redact_secrets(completed.stdout),
            redact_secrets(completed.stderr),
            final_message=final_message,
            transport_error=(
                None if final_message is not None else "FINAL_MESSAGE_MISSING"
            ),
        )

    @staticmethod
    def _remove_final_message(path: str) -> None:
        Path(path).unlink(missing_ok=True)

    @classmethod
    def _read_final_message(cls, path: str) -> str | None:
        try:
            value = Path(path).read_text(encoding="utf-8")
        except OSError:
            value = None
        finally:
            cls._remove_final_message(path)
        return redact_secrets(value) if value is not None else None


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
