"""Controlled OpenHands CLI adapter.

The adapter deliberately supports only the locally discovered headless CLI
shape. It returns the existing CodexProcessResult contract so the executor
and delivery pipeline retain their existing safety gates.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .codex import CodexProcessResult, _as_text, redact_secrets


class OpenHandsAdapter:
    """Invoke OpenHands headlessly in the isolated executor worktree."""

    name = "openhands"

    def __init__(self, executable: str = "openhands", timeout: float = 300.0):
        self.executable = executable
        self.timeout = timeout

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
            "AGF role: Implementer.",
            "Execute exactly one approved AGF-Orchestrator task.",
            f"Workspace: {repository}",
            f"Task ID: {task_id}",
            f"Task title: {title}",
            f"Exact objective: {objective}",
            "Allowed paths (and no others):",
            *[f"- {path}" for path in allowed_paths],
            "Exact acceptance criteria:",
            *[f"- {criterion}" for criterion in acceptance_criteria],
            "Approved validation commands:",
            *[f"- {command}" for command in validation_commands],
            "Approved architecture constraints: preserve the approved plan architecture; "
            "do not redesign.",
            "Stop conditions:",
            *[f"- {condition}" for condition in stop_conditions],
            "Do not expand scope or modify outside the workspace or allowed paths.",
            "Do not commit, push, create a PR, merge, or release.",
            "Report changed files and validation results. Stop and return HUMAN_REQUIRED "
            "on ambiguity.",
        ]
        return "\n".join(lines)

    def execute(
        self,
        instruction: str,
        repository: str,
        *,
        sandbox: str = "workspace-write",
    ) -> CodexProcessResult:
        del sandbox  # OpenHands owns its headless sandbox configuration.
        command = [
            self.executable,
            "--headless",
            "--json",
            "--exit-without-confirmation",
            "--task",
            instruction,
        ]
        summary = (
            "openhands --headless --json --exit-without-confirmation --task <task-instruction>"
        )
        environment = {
            key: os.environ[key]
            for key in (
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "OPENHANDS_CONFIG_DIR",
            )
            if key in os.environ
        }
        try:
            completed = subprocess.run(
                command,
                cwd=Path(repository),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            return CodexProcessResult(
                summary,
                None,
                redact_secrets(_as_text(exc.stdout)),
                redact_secrets(_as_text(exc.stderr)),
                timed_out=True,
            )
        except OSError as exc:
            return CodexProcessResult(
                summary,
                None,
                "",
                redact_secrets(str(exc)),
                human_required=True,
                transport_error="OPENHANDS_PROCESS_FAILED",
            )
        return CodexProcessResult(
            summary,
            completed.returncode,
            redact_secrets(completed.stdout),
            redact_secrets(completed.stderr),
        )
