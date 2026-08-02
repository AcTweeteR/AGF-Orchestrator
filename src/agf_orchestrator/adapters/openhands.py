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
from urllib.parse import urlparse

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
OPENHANDS_LLM_ENV_KEYS = ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")
OPENHANDS_LLM_ENV_NOT_AUTHORIZED = "OPENHANDS_LLM_ENV_NOT_AUTHORIZED"
OPENHANDS_LLM_API_KEY_MISSING = "OPENHANDS_LLM_API_KEY_MISSING"
OPENHANDS_LLM_MODEL_MISSING = "OPENHANDS_LLM_MODEL_MISSING"
OPENHANDS_LLM_MODEL_INVALID = "OPENHANDS_LLM_MODEL_INVALID"
OPENHANDS_LLM_BASE_URL_INVALID = "OPENHANDS_LLM_BASE_URL_INVALID"
_MAX_MODEL_LENGTH = 200


def _configuration_error(code: str, summary: str) -> CodexProcessResult:
    return CodexProcessResult(
        summary,
        None,
        "",
        code,
        human_required=True,
        transport_error=code,
    )


def _validate_llm_environment(
    environment: dict[str, str], *, authorized: bool
) -> tuple[str | None, tuple[str, ...]]:
    if not authorized:
        return OPENHANDS_LLM_ENV_NOT_AUTHORIZED, ()
    api_key = environment.get("LLM_API_KEY", "")
    model = environment.get("LLM_MODEL", "")
    base_url = environment.get("LLM_BASE_URL", "")
    if not api_key:
        return OPENHANDS_LLM_API_KEY_MISSING, ()
    if not model:
        return OPENHANDS_LLM_MODEL_MISSING, (api_key,)
    if (
        len(model) > _MAX_MODEL_LENGTH
        or not model.isprintable()
    ):
        return OPENHANDS_LLM_MODEL_INVALID, (api_key,)
    if base_url:
        parsed = urlparse(base_url)
        local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            any(ord(char) < 32 or ord(char) == 127 for char in base_url)
            or (parsed.scheme != "https" and not local)
            or not parsed.netloc
        ):
            return OPENHANDS_LLM_BASE_URL_INVALID, (api_key,)
    return None, (api_key,)


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

    def __init__(
        self,
        executable: str = "openhands",
        timeout: float = 300.0,
        *,
        allow_llm_env: bool = False,
    ):
        self.executable = executable
        self.timeout = timeout
        self.allow_llm_env = allow_llm_env

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
        command = [self.executable]
        if self.allow_llm_env:
            command.append("--override-with-envs")
        command.extend(
            [
                "--headless",
                "--json",
                "--exit-without-confirmation",
                "--task",
                instruction,
            ]
        )
        override_summary = " --override-with-envs" if self.allow_llm_env else ""
        summary = (
            "openhands"
            f"{override_summary} --headless --json --exit-without-confirmation "
            "--task <task-instruction>"
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
        llm_environment = {
            key: os.environ[key] for key in OPENHANDS_LLM_ENV_KEYS if key in os.environ
        }
        code, secrets = _validate_llm_environment(
            llm_environment, authorized=self.allow_llm_env
        )
        if code:
            return _configuration_error(code, summary)
        if self.allow_llm_env:
            environment.update(llm_environment)
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
                redact_secrets(_as_text(exc.stdout), additional_secrets=secrets),
                redact_secrets(_as_text(exc.stderr), additional_secrets=secrets),
                timed_out=True,
            )
        except OSError as exc:
            return CodexProcessResult(
                summary,
                None,
                "",
                redact_secrets(str(exc), additional_secrets=secrets),
                human_required=True,
                transport_error="OPENHANDS_PROCESS_FAILED",
            )
        interpretation = parse_openhands_output(completed.stdout, completed.stderr)
        return CodexProcessResult(
            summary,
            completed.returncode,
            redact_secrets(completed.stdout, additional_secrets=secrets),
            redact_secrets(completed.stderr, additional_secrets=secrets),
            human_required=interpretation.human_required,
            final_message=redact_secrets(interpretation.final_message, additional_secrets=secrets)
            if interpretation.final_message
            else None,
            transport_error=interpretation.status_code,
        )
