"""Controlled OpenHands CLI adapter.

The adapter deliberately supports only the locally discovered headless CLI
shape. It returns the existing CodexProcessResult contract so the executor
and delivery pipeline retain their existing safety gates.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .codex import CodexProcessResult, _as_text, redact_secrets

OPENHANDS_SUCCESS_STATES = {"completed", "complete", "finished", "success", "succeeded"}
OPENHANDS_FAILURE_STATES = {"failed", "failure", "error", "rejected", "cancelled", "canceled"}
OPENHANDS_INTERACTION_STATES = {
    "awaiting_confirmation",
    "awaiting_input",
    "confirmation_required",
    "interaction_required",
    "paused",
    "needs_input",
}
OPENHANDS_CONFIGURATION = re.compile(
    r"(?is)(headless mode requires existing settings|configure your settings|"
    r"(missing|required|invalid).{0,40}(model|provider|api key|configuration)|"
    r"(model|provider).{0,40}(missing|required|not configured))"
)


@dataclass(frozen=True)
class OpenHandsInterpretation:
    status_code: str | None
    human_required: bool
    final_message: str | None


def _normalized_state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def parse_openhands_output(stdout: str, stderr: str = "") -> OpenHandsInterpretation:
    """Interpret only explicit JSONL terminal states from this CLI."""
    if OPENHANDS_CONFIGURATION.search(stdout) or OPENHANDS_CONFIGURATION.search(stderr):
        return OpenHandsInterpretation("OPENHANDS_CONFIGURATION_REQUIRED", True, None)
    objects: list[dict] = []
    non_json_lines = 0
    for line in stdout.splitlines():
        candidate = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            non_json_lines += 1
            continue
        if isinstance(value, dict):
            objects.append(value)
    if not objects:
        return OpenHandsInterpretation(
            "OPENHANDS_JSON_INVALID" if non_json_lines else "OPENHANDS_NO_TERMINAL_STATE",
            True,
            None,
        )
    states: list[str] = []
    final_message = None
    for item in objects:
        for key in ("status", "state", "agent_state", "event_type", "type"):
            state = _normalized_state(item.get(key))
            if (
                state
                in OPENHANDS_SUCCESS_STATES
                | OPENHANDS_FAILURE_STATES
                | OPENHANDS_INTERACTION_STATES
            ):
                states.append(state)
        for key in ("final_message", "message"):
            if isinstance(item.get(key), str) and item[key].strip():
                final_message = item[key]
    categories = {
        "success"
        if state in OPENHANDS_SUCCESS_STATES
        else "failure"
        if state in OPENHANDS_FAILURE_STATES
        else "interaction"
        for state in states
    }
    if len(categories) > 1:
        return OpenHandsInterpretation(
            "OPENHANDS_CONTRADICTORY_TERMINAL_STATE", True, final_message
        )
    if not states:
        return OpenHandsInterpretation("OPENHANDS_NO_TERMINAL_STATE", True, final_message)
    state = states[-1]
    if state in OPENHANDS_INTERACTION_STATES:
        return OpenHandsInterpretation("OPENHANDS_INTERACTION_REQUIRED", True, final_message)
    if state in OPENHANDS_FAILURE_STATES:
        return OpenHandsInterpretation("OPENHANDS_TASK_FAILED", False, final_message)
    return OpenHandsInterpretation(None, False, final_message)


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
        interpretation = parse_openhands_output(completed.stdout, completed.stderr)
        return CodexProcessResult(
            summary,
            completed.returncode,
            redact_secrets(completed.stdout),
            redact_secrets(completed.stderr),
            human_required=interpretation.human_required,
            final_message=redact_secrets(interpretation.final_message)
            if interpretation.final_message
            else None,
            transport_error=interpretation.status_code,
        )
