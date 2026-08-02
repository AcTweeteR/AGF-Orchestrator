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
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .codex import CodexProcessResult, _as_text, redact_secrets

OPENHANDS_SUCCESS_STATES = {"completed", "complete", "finished", "success", "succeeded"}
OPENHANDS_FAILURE_STATES = {"failed", "failure", "error", "rejected", "cancelled", "canceled"}
OPENHANDS_INTERACTION_STATES = {
    "awaiting_confirmation",
    "waiting_for_confirmation",
    "awaiting_input",
    "confirmation_required",
    "interaction_required",
    "paused",
    "needs_input",
}
OPENHANDS_STATE_EVENT = "conversationstateupdateevent"
OPENHANDS_EXECUTION_STATUS_KEY = "execution_status"
OPENHANDS_FULL_STATE_KEY = "full_state"
OPENHANDS_STRUCTURED_OUTPUT_MISSING = "OPENHANDS_STRUCTURED_OUTPUT_MISSING"
OPENHANDS_STRUCTURED_OUTPUT_CONFLICT = "OPENHANDS_STRUCTURED_OUTPUT_CONFLICT"
OPENHANDS_STDERR_EVENT_STREAM_INVALID = "OPENHANDS_STDERR_EVENT_STREAM_INVALID"
OPENHANDS_JSON_TRUNCATED = "OPENHANDS_JSON_TRUNCATED"
OPENHANDS_JSON_INVALID = "OPENHANDS_JSON_INVALID"
OPENHANDS_NO_TERMINAL_STATE = "OPENHANDS_NO_TERMINAL_STATE"
OPENHANDS_CONTRADICTORY_TERMINAL_STATE = "OPENHANDS_CONTRADICTORY_TERMINAL_STATE"
_MAX_OPENHANDS_OUTPUT = 1_000_000
_MAX_OPENHANDS_OBJECTS = 1_000
OPENHANDS_EVENT_KINDS = {
    "messageevent",
    "conversationstateupdateevent",
    "actionevent",
    "observationevent",
    "agenterrorevent",
    "pauseevent",
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
    object_count: int = 0
    terminal_event_found: bool = False
    terminal_execution_status: str | None = None
    final_agent_message_present: bool = False
    transport: str | None = None


def _normalized_state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _nested_message(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    message = value.get("llm_message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    parts = [
        item.get("text")
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    text = "".join(parts).strip()
    return text or None


def _state_event_state(item: dict) -> str | None:
    if _normalized_state(item.get("kind")) != OPENHANDS_STATE_EVENT:
        return None
    key = _normalized_state(item.get("key"))
    value = item.get("value")
    if key == OPENHANDS_EXECUTION_STATUS_KEY:
        return _normalized_state(value)
    if key == OPENHANDS_FULL_STATE_KEY and isinstance(value, dict):
        return _normalized_state(value.get(OPENHANDS_EXECUTION_STATUS_KEY))
    return None


def _json_failure_code(text: str, error: json.JSONDecodeError) -> str:
    remaining = text[error.pos:].rstrip()
    truncated = not remaining or error.pos >= len(text.rstrip()) - 1
    return OPENHANDS_JSON_TRUNCATED if truncated else OPENHANDS_JSON_INVALID


def _extract_json_values(stdout: str) -> tuple[list[object], str | None]:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout)
    if len(text) > _MAX_OPENHANDS_OUTPUT:
        return [], OPENHANDS_JSON_INVALID
    decoder = json.JSONDecoder()
    values: list[object] = []
    cursor = 0
    candidate_seen = False
    while cursor < len(text):
        match = re.search(r"[\[{]", text[cursor:])
        if match is None:
            break
        start = cursor + match.start()
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError as error:
            if candidate_seen or not text[:start].strip():
                return values, _json_failure_code(text, error)
            cursor = start + 1
            continue
        candidate_seen = True
        values.append(value)
        cursor = end
        if len(values) > _MAX_OPENHANDS_OBJECTS:
            return values, OPENHANDS_JSON_INVALID
    return values, None


def _walk_json(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _stream_metadata(stream: str) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    values, extraction_error = _extract_json_values(stream)
    objects = [item for value in values for item in _walk_json(value)]
    events = [
        item
        for item in objects
        if _normalized_state(item.get("kind")) in OPENHANDS_EVENT_KINDS
    ]
    fingerprints = tuple(
        json.dumps(item, sort_keys=True, separators=(",", ":")) for item in events
    )
    kinds = tuple(_normalized_state(item.get("kind")) or "" for item in events)
    return kinds, fingerprints, extraction_error


def _parse_openhands_stream(stream: str) -> OpenHandsInterpretation:
    """Interpret one bounded OpenHands JSON/JSONL stream."""
    values, extraction_error = _extract_json_values(stream)
    if extraction_error:
        return OpenHandsInterpretation(extraction_error, True, None)
    objects = list(item for value in values for item in _walk_json(value))
    if len(objects) > _MAX_OPENHANDS_OBJECTS:
        return OpenHandsInterpretation(OPENHANDS_JSON_INVALID, True, None)
    if not objects:
        code = OPENHANDS_NO_TERMINAL_STATE if values else OPENHANDS_JSON_INVALID
        return OpenHandsInterpretation(code, True, None)
    states: list[str] = []
    final_message = None
    for item in objects:
        nested_state = _state_event_state(item)
        if nested_state:
            states.append(nested_state)
        for key in ("final_message", "message"):
            if isinstance(item.get(key), str) and item[key].strip():
                final_message = item[key]
        nested_message = _nested_message(item)
        if nested_message:
            final_message = nested_message
    categories = {
        "success"
        if state in OPENHANDS_SUCCESS_STATES
        else "failure"
        if state in OPENHANDS_FAILURE_STATES
        else "interaction"
        for state in states
    }
    terminal_statuses = {
        state
        for state in states
        if state
        in OPENHANDS_SUCCESS_STATES | OPENHANDS_FAILURE_STATES | OPENHANDS_INTERACTION_STATES
    }
    if len(categories) > 1:
        return OpenHandsInterpretation(
            OPENHANDS_CONTRADICTORY_TERMINAL_STATE,
            True,
            final_message,
            len(objects),
            bool(terminal_statuses),
            states[-1] if states else None,
            bool(final_message),
        )
    if not terminal_statuses:
        return OpenHandsInterpretation(
            OPENHANDS_NO_TERMINAL_STATE,
            True,
            final_message,
            len(objects),
            False,
            None,
            bool(final_message),
        )
    state = states[-1]
    if state in OPENHANDS_INTERACTION_STATES:
        return OpenHandsInterpretation(
            "OPENHANDS_INTERACTION_REQUIRED",
            True,
            final_message,
            len(objects),
            True,
            state,
            bool(final_message),
        )
    if state in OPENHANDS_FAILURE_STATES:
        return OpenHandsInterpretation(
            "OPENHANDS_TASK_FAILED",
            False,
            final_message,
            len(objects),
            True,
            state,
            bool(final_message),
        )
    return OpenHandsInterpretation(
        None, False, final_message, len(objects), True, state, bool(final_message)
    )


def parse_openhands_output(stdout: str, stderr: str = "") -> OpenHandsInterpretation:
    """Select one independent, recognized OpenHands structured stream."""
    if OPENHANDS_CONFIGURATION.search(stdout) or OPENHANDS_CONFIGURATION.search(stderr):
        return OpenHandsInterpretation("OPENHANDS_CONFIGURATION_REQUIRED", True, None)
    stdout_result = _parse_openhands_stream(stdout)
    stderr_result = _parse_openhands_stream(stderr)
    stdout_kinds, stdout_fingerprints, stdout_error = _stream_metadata(stdout)
    stderr_kinds, stderr_fingerprints, stderr_error = _stream_metadata(stderr)
    stdout_has_events = bool(stdout_kinds)
    stderr_has_events = bool(stderr_kinds)
    if stdout_has_events and stderr_has_events:
        if stdout_fingerprints != stderr_fingerprints:
            return OpenHandsInterpretation(OPENHANDS_STRUCTURED_OUTPUT_CONFLICT, True, None)
        return replace(stdout_result, transport="stdout")
    if stdout_has_events:
        return replace(stdout_result, transport="stdout")
    if stderr_has_events:
        if stderr_error in {OPENHANDS_JSON_INVALID, OPENHANDS_JSON_TRUNCATED}:
            return OpenHandsInterpretation(OPENHANDS_STDERR_EVENT_STREAM_INVALID, True, None)
        return replace(stderr_result, transport="stderr")
    error = stdout_error or stderr_error
    if error in {OPENHANDS_JSON_INVALID, OPENHANDS_JSON_TRUNCATED}:
        return OpenHandsInterpretation(error, True, None)
    if stdout_result.status_code == OPENHANDS_NO_TERMINAL_STATE:
        return stdout_result
    if stderr_result.status_code == OPENHANDS_NO_TERMINAL_STATE:
        return stderr_result
    return OpenHandsInterpretation(OPENHANDS_STRUCTURED_OUTPUT_MISSING, True, None)


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
