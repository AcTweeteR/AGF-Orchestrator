"""Controlled, provider-specific Codex CLI adapter."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)((?:key|api[_-]?key|token)=)([^&\s]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
)
SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
}
MACOS_CODEX_EXECUTABLE = "/Applications/ChatGPT.app/Contents/Resources/codex"


def redact_secrets(
    value: str, *, limit: int = 4000, additional_secrets: tuple[str, ...] = ()
) -> str:
    """Redact common secret-shaped values and cap report size."""
    redacted = value
    for secret in additional_secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
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
    transport_evidence: tuple[str, ...] = ()
    executable_resolved: bool = False
    process_started: bool = False
    process_completed: bool = False
    output_last_message_created: bool = False
    output_last_message_fresh: bool = False
    output_last_message_bytes: int = 0
    invocation_verified: bool = False


@dataclass(frozen=True)
class CodexExecutableResolution:
    """Canonical, version-probed Codex executable resolution."""

    path: str | None
    source: str
    error: str | None
    evidence: tuple[str, ...]


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
        executable: str | None = None,
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
        resolution = resolve_codex_executable(self.executable)
        if resolution.path is None:
            return CodexProcessResult(
                "codex executable could not be resolved", None, "", "",
                human_required=True,
                transport_error=resolution.error,
                transport_evidence=resolution.evidence,
            )
        executable = resolution.path
        profile = self.profile or discover_invocation_profile(executable)
        if profile is None:
            return CodexProcessResult(
                "codex invocation syntax could not be verified", None, "",
                "Codex invocation syntax could not be verified from parser help",
                human_required=True, transport_error="CODEX_PROCESS_NOT_STARTED",
                executable_resolved=True,
                transport_evidence=_transport_evidence(
                    executable_resolved=True, process_started=False,
                    process_completed=False, created=False, fresh=False, size=0,
                    parsed=False, verified=False,
                ),
            )
        approved_dir = Path(tempfile.mkdtemp(prefix="agf-codex-transport-"))
        final_message_path = approved_dir / "final-message.txt"
        invocation_started_ns = time.time_ns()
        command = profile.build_command(
            executable,
            instruction,
            sandbox=sandbox,
            final_message_path=str(final_message_path),
        )
        summary = (
            f'codex -c approval_policy="never" -s {sandbox} '
            "exec --output-last-message <approved-temp-file> <task-instruction>"
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
            final_message, transport_error, evidence = _read_verified_final_message(
                final_message_path, approved_dir, invocation_started_ns
            )
            result = CodexProcessResult(
                summary, None, stdout, stderr, timed_out=True,
                human_required=False,
                final_message=final_message,
                transport_error=(
                    "CODEX_PROCESS_FAILED" if transport_error is None else transport_error
                ),
                transport_evidence=evidence,
                executable_resolved=True, process_started=True,
                process_completed=False,
                output_last_message_created=_evidence_bool(evidence, "created"),
                output_last_message_fresh=_evidence_bool(evidence, "fresh"),
                output_last_message_bytes=_evidence_int(evidence, "bytes"),
            )
            shutil.rmtree(approved_dir, ignore_errors=True)
            return result
        except OSError as exc:
            shutil.rmtree(approved_dir, ignore_errors=True)
            return CodexProcessResult(
                summary, None, "", redact_secrets(str(exc)),
                human_required=True, transport_error="CODEX_PROCESS_NOT_STARTED",
                executable_resolved=True,
                transport_evidence=_transport_evidence(
                    executable_resolved=True, process_started=False,
                    process_completed=False, created=False, fresh=False, size=0,
                    parsed=False, verified=False,
                ),
            )
        final_message, transport_error, evidence = _read_verified_final_message(
            final_message_path, approved_dir, invocation_started_ns
        )
        if completed.returncode != 0:
            transport_error = "CODEX_PROCESS_FAILED"
        elif transport_error is None and final_message is not None:
            transport_error = None
        result = CodexProcessResult(
            summary,
            completed.returncode,
            redact_secrets(completed.stdout),
            redact_secrets(completed.stderr),
            final_message=final_message,
            human_required=transport_error in {
                "CODEX_EXECUTABLE_NOT_FOUND", "CODEX_PROCESS_NOT_STARTED",
                "CODEX_FINAL_MESSAGE_MISSING", "CODEX_FINAL_MESSAGE_STALE",
                "CODEX_FINAL_MESSAGE_EMPTY", "CODEX_FINAL_MESSAGE_UNREADABLE",
                "CODEX_REVIEW_TRANSPORT_CONFLICT",
            },
            transport_error=transport_error,
            transport_evidence=evidence,
            executable_resolved=True, process_started=True,
            process_completed=True,
            output_last_message_created=_evidence_bool(evidence, "created"),
            output_last_message_fresh=_evidence_bool(evidence, "fresh"),
            output_last_message_bytes=_evidence_int(evidence, "bytes"),
            invocation_verified=(
                completed.returncode == 0 and transport_error is None and final_message is not None
            ),
        )
        shutil.rmtree(approved_dir, ignore_errors=True)
        return result


def resolve_codex_executable(executable: str | None = None) -> CodexExecutableResolution:
    """Resolve Codex deterministically, probing each candidate before use."""
    explicit = bool(executable and (Path(executable).is_absolute() or "/" in executable))
    if executable == "":
        return _resolution_failure("explicit", True, "CODEX_EXECUTABLE_NOT_CONFIGURED")
    if explicit:
        return _inspect_candidate(executable, "explicit", explicit=True)

    path_name = executable or "codex"
    path_candidate = shutil.which(path_name)
    if path_candidate:
        resolution = _inspect_candidate(path_candidate, "PATH", explicit=False)
        if resolution.path is not None:
            return resolution

    return _inspect_candidate(
        MACOS_CODEX_EXECUTABLE, "macos-chatgpt", explicit=False,
    )


def _inspect_candidate(
    candidate: str,
    source: str,
    *,
    explicit: bool,
) -> CodexExecutableResolution:
    raw = Path(candidate).expanduser()
    try:
        canonical = raw.resolve(strict=False)
    except OSError:
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_NOT_FOUND")
    if not raw.exists():
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_NOT_FOUND")
    if not canonical.is_file():
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_NOT_REGULAR_FILE")
    if not os.access(canonical, os.X_OK):
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_NOT_EXECUTABLE")

    if raw.is_symlink():
        if source == "macos-chatgpt":
            trusted_root = Path(MACOS_CODEX_EXECUTABLE).parent.resolve()
        elif source == "PATH":
            trusted_root = raw.parent.resolve()
        else:
            trusted_root = raw.parent.resolve()
        if trusted_root not in canonical.parents and canonical != trusted_root:
            return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_NOT_REGULAR_FILE")

    try:
        version = subprocess.run(
            [str(canonical), "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            shell=False,
            env=_safe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_VERSION_PROBE_FAILED")
    if version.returncode != 0:
        return _resolution_failure(source, explicit, "CODEX_EXECUTABLE_VERSION_PROBE_FAILED")
    return CodexExecutableResolution(
        str(canonical), source, None,
        _resolution_evidence(explicit, source, True, True),
    )


def _resolution_failure(
    source: str, explicit: bool, error: str
) -> CodexExecutableResolution:
    return CodexExecutableResolution(
        None,
        source,
        error,
        _resolution_evidence(explicit, source, False, False),
    )


def _resolution_evidence(
    explicit: bool, source: str, resolved: bool, version_passed: bool,
) -> tuple[str, ...]:
    return (
        f"explicit path requested: {'yes' if explicit else 'no'}",
        f"candidate source: {source}",
        f"executable resolved: {'yes' if resolved else 'no'}",
        f"version probe passed: {'yes' if version_passed else 'no'}",
    )


def _resolve_executable(executable: str | None) -> str | None:
    """Backward-compatible path-only resolver for callers outside the adapter."""
    return resolve_codex_executable(executable).path


def _transport_evidence(
    *, executable_resolved: bool, process_started: bool, process_completed: bool,
    created: bool, fresh: bool, size: int, parsed: bool, verified: bool,
) -> tuple[str, ...]:
    return (
        f"Codex executable resolved: {'yes' if executable_resolved else 'no'}",
        f"Codex process started: {'yes' if process_started else 'no'}",
        f"Codex process completion captured: {'yes' if process_completed else 'no'}",
        f"output-last-message created: {'yes' if created else 'no'}",
        f"output-last-message fresh: {'yes' if fresh else 'no'}",
        f"output-last-message bytes: {size}",
        f"final message read: {'yes' if parsed else 'no'}",
        f"invocation verified: {'yes' if verified else 'no'}",
    )


def _evidence_bool(evidence: tuple[str, ...], name: str) -> bool:
    return any(item == f"output-last-message {name}: yes" for item in evidence)


def _evidence_int(evidence: tuple[str, ...], name: str) -> int:
    prefix = f"output-last-message {name}: "
    for item in evidence:
        if item.startswith(prefix):
            try:
                return int(item[len(prefix):])
            except ValueError:
                return 0
    return 0


def _read_verified_final_message(
    path: Path, approved_dir: Path, invocation_started_ns: int,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Read only a fresh, bounded final-message file created in approved_dir."""
    root = approved_dir.resolve()
    try:
        resolved = path.resolve(strict=False)
        inside = resolved == root or root in resolved.parents
    except OSError:
        inside = False
    if not inside:
        return None, "CODEX_FINAL_MESSAGE_UNREADABLE", _transport_evidence(
            executable_resolved=True, process_started=True, process_completed=True,
            created=False, fresh=False, size=0, parsed=False, verified=False,
        )
    try:
        stat = resolved.stat()
    except OSError:
        return None, "CODEX_FINAL_MESSAGE_MISSING", _transport_evidence(
            executable_resolved=True, process_started=True, process_completed=True,
            created=False, fresh=False, size=0, parsed=False, verified=False,
        )
    created = resolved.is_file()
    size = stat.st_size if created else 0
    fresh = created and stat.st_mtime_ns > invocation_started_ns
    base = dict(
        executable_resolved=True, process_started=True, process_completed=True,
        created=created, fresh=fresh, size=size, parsed=False, verified=False,
    )
    if not created:
        return None, "CODEX_FINAL_MESSAGE_MISSING", _transport_evidence(**base)
    if not fresh:
        return None, "CODEX_FINAL_MESSAGE_STALE", _transport_evidence(**base)
    if size == 0:
        return None, "CODEX_FINAL_MESSAGE_EMPTY", _transport_evidence(**base)
    if size > 100_000:
        return None, "CODEX_FINAL_MESSAGE_UNREADABLE", _transport_evidence(**base)
    try:
        value = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, "CODEX_FINAL_MESSAGE_UNREADABLE", _transport_evidence(**base)
    evidence = _transport_evidence(**{**base, "parsed": True, "verified": True})
    return redact_secrets(value), None, evidence


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
