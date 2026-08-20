"""Independent macOS-friendly process supervisor for persistent campaigns.

The campaign runner owns state transitions; this module owns the process
lifecycle.  Driver commands are explicit argv lists and exchange only bounded
JSON on stdin/stdout.  No shell is used and no provider is invoked merely to
poll an external condition.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .authority_context import AuthorityContext, AuthorityContextError
from .campaign_runner import (
    CampaignState,
    CampaignStatus,
    CampaignStore,
    PersistentCampaignRunner,
    StepResult,
    WaitRequest,
    parse_timestamp,
    timestamp,
    utc_now,
)
from .external_actions import ExternalActionError, ExternalActionExecutor, ExternalActionRequest
from .locking import LockError, project_lock
from .project_registry import ProjectRegistry, ProjectRegistryError, _git
from .session_store import SessionStore, SessionStoreError


class CampaignDaemonError(RuntimeError):
    """Raised when the independent campaign supervisor cannot continue safely."""


class CanonicalBindingError(CampaignDaemonError):
    """Raised only when persisted campaign identity no longer matches reality."""


class RetryableCanonicalBindingError(CanonicalBindingError):
    """Raised when binding evidence is temporarily unavailable to verify."""


_MAX_COMMAND_OUTPUT = 64 * 1024
_MAX_COMMAND_ARGS = 64


def _reject_unguarded_external_command(command: tuple[str, ...]) -> None:
    """Reject known mutating adapters; mutations use ExternalActionExecutor."""
    lowered = tuple(item.lower() for item in command)
    patterns = (("gh", "pr", "merge"), ("gh", "pr", "close"),
                ("git", "push"), ("git", "tag"))
    if any(
        any(
            lowered[index:index + len(pattern)] == pattern
            for index in range(len(lowered) - len(pattern) + 1)
        )
        for pattern in patterns
    ):
        raise CampaignDaemonError(
            "unguarded external action command is forbidden; use ExternalActionExecutor"
        )


@dataclass(frozen=True)
class CampaignDriverSpec:
    project_id: str
    campaign_id: str
    state_dir: str
    probe_command: tuple[str, ...]
    work_command: tuple[str, ...]
    poll_seconds: int = 30

    def validate(self) -> None:
        if not self.project_id.startswith("project-"):
            raise CampaignDaemonError("driver project_id is invalid")
        if not self.campaign_id.startswith("campaign-"):
            raise CampaignDaemonError("driver campaign_id is invalid")
        for name, command in (("probe", self.probe_command), ("work", self.work_command)):
            if not command or len(command) > _MAX_COMMAND_ARGS or any(
                not isinstance(item, str) or not item or len(item) > 4096 for item in command
            ):
                raise CampaignDaemonError(f"{name} command is invalid")
            _reject_unguarded_external_command(command)
        if not 1 <= self.poll_seconds <= 3600:
            raise CampaignDaemonError("poll_seconds is outside bounded limits")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "project_id": self.project_id,
            "campaign_id": self.campaign_id,
            "state_dir": self.state_dir,
            "probe_command": list(self.probe_command),
            "work_command": list(self.work_command),
            "poll_seconds": self.poll_seconds,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "CampaignDriverSpec":
        if not isinstance(payload, dict):
            raise CampaignDaemonError("driver spec is not an object")
        try:
            spec = cls(
                project_id=payload["project_id"], campaign_id=payload["campaign_id"],
                state_dir=payload["state_dir"],
                probe_command=tuple(payload["probe_command"]),
                work_command=tuple(payload["work_command"]),
                poll_seconds=payload.get("poll_seconds", 30),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CampaignDaemonError("driver spec is malformed") from exc
        spec.validate()
        return spec


@dataclass(frozen=True)
class RunnerStatus:
    pid: int
    instance_id: str
    runner_active: bool
    campaigns_active: int
    campaigns_waiting: int
    next_wake: str | None
    last_wake: str | None
    last_action: str | None
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class CommandCampaignDriver:
    """Invoke persisted, explicit command adapters without a shell."""

    def __init__(
        self,
        spec: CampaignDriverSpec,
        *,
        external_executor: ExternalActionExecutor | None = None,
    ):
        self.spec = spec
        self.external_executor = external_executor

    def _run(self, command: tuple[str, ...], state: CampaignState) -> dict[str, object]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "GH_CONFIG_DIR": os.environ.get(
                "GH_CONFIG_DIR", str(Path.home() / ".config" / "gh")
            ),
            "GH_PAGER": "cat",
            "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "AGF_CAMPAIGN_ID": state.campaign_id,
            "AGF_SESSION_ID": state.session_id,
        }
        for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy",
        ):
            if os.environ.get(name):
                environment[name] = os.environ[name]
        try:
            process = subprocess.Popen(
                list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, shell=False, start_new_session=True,
                env=environment,
            )
            stdout, _stderr = process.communicate(
                input=json.dumps(state.to_dict()) + "\n", timeout=120
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            raise CampaignDaemonError("campaign driver command timed out") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise CampaignDaemonError("campaign driver command failed") from exc
        if process.returncode != 0:
            raise CampaignDaemonError("campaign driver command returned failure")
        if len(stdout) > _MAX_COMMAND_OUTPUT:
            raise CampaignDaemonError("campaign driver output is too large")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CampaignDaemonError("campaign driver returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CampaignDaemonError("campaign driver returned a non-object")
        return payload

    def probe(self, state: CampaignState) -> bool:
        payload = self._run(self.spec.probe_command, state)
        if not isinstance(payload.get("ready"), bool):
            raise CampaignDaemonError("probe result must contain boolean ready")
        return payload["ready"]

    def work(self, state: CampaignState) -> StepResult:
        payload = self._run(self.spec.work_command, state)
        outcome = payload.get("outcome")
        if outcome == "WAIT":
            wait = payload.get("wait")
            if not isinstance(wait, dict):
                raise CampaignDaemonError("WAIT result requires wait object")
            return StepResult(
                "WAIT", WaitRequest(
                    CampaignStatus(wait["status"]), wait["reason"], wait["resource"],
                    wait["expected_condition"], wait["next_check_at"],
                ), payload.get("reason"),
            )
        if outcome == "EXTERNAL_ACTION":
            request = ExternalActionRequest.from_payload(
                payload.get("action"), project_id=state.project_id, session_id=state.session_id
            )
            if self.external_executor is None:
                raise CampaignDaemonError("external action executor is not configured")
            try:
                reason = self.external_executor.execute_authorized(request)
            except ExternalActionError as exc:
                raise CampaignDaemonError(str(exc)) from exc
            return StepResult("COMPLETE", reason=reason)
        if outcome not in {"CONTINUE", "COMPLETE", "HUMAN_REQUIRED",
                           "BLOCKED_NON_RETRYABLE", "CANCELLED"}:
            raise CampaignDaemonError("work result outcome is invalid")
        return StepResult(outcome, reason=payload.get("reason"))


class CampaignDaemon:
    """A single independent process that survives provider and terminal lifecycles."""

    def __init__(self, state_dir: str | Path, *, sleep: Callable[[float], None] = time.sleep):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.root = self.state_dir / "campaign-daemon"
        self.spec_dir = self.root / "drivers"
        self.lock_path = self.root / "daemon.lock"
        self.status_path = self.root / "status.json"
        self.sleep = sleep
        self.instance_id = f"daemon-{os.getpid()}"
        self._stop = False
        self._lock_handle = None

    def register(self, spec: CampaignDriverSpec) -> None:
        spec.validate()
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        path = self.spec_dir / f"{spec.campaign_id}.json"
        self._atomic_json(path, spec.to_dict())

    def rebind_interpreters(self, interpreter: str) -> int:
        """Rebind persisted Python driver commands to a launchd-safe runtime."""
        runtime = Path(interpreter).expanduser().resolve()
        if not runtime.is_file() or not os.access(runtime, os.X_OK):
            raise CampaignDaemonError("interpreter is not an executable file")
        changed = 0
        for spec in self._load_specs():
            def rebind(command: tuple[str, ...]) -> tuple[str, ...]:
                if command and Path(command[0]).name.startswith("python"):
                    return (str(runtime), *command[1:])
                return command
            updated = CampaignDriverSpec(
                spec.project_id, spec.campaign_id, spec.state_dir,
                rebind(spec.probe_command), rebind(spec.work_command), spec.poll_seconds,
            )
            if updated.to_dict() != spec.to_dict():
                self.register(updated)
                changed += 1
        return changed

    def status(self) -> RunnerStatus:
        if not self.status_path.exists():
            return RunnerStatus(0, "none", False, 0, 0, None, None, None, timestamp(utc_now()))
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            return RunnerStatus(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CampaignDaemonError("runner status is invalid") from exc

    def run_forever(self, *, max_loops: int | None = None) -> None:
        self._acquire_lock()
        previous = None
        loops = 0
        self._install_signal_handlers()
        try:
            while not self._stop:
                specs = self._load_specs()
                active = waiting = 0
                next_wake = None
                last_action = None
                for spec in specs:
                    store = CampaignStore(spec.state_dir, spec.project_id, spec.campaign_id)
                    state = store.load()
                    if state.status in {CampaignStatus.COMPLETE, CampaignStatus.HUMAN_REQUIRED,
                                         CampaignStatus.BLOCKED_NON_RETRYABLE,
                                         CampaignStatus.CANCELLED}:
                        continue
                    try:
                        with project_lock(spec.state_dir, state.project_id, "campaign-binding"):
                            self._validate_canonical_binding(state, spec.state_dir)
                        active += 1
                        waiting_statuses = {
                            CampaignStatus.WAITING_CI, CampaignStatus.WAITING_REVIEW,
                            CampaignStatus.WAITING_GITHUB, CampaignStatus.WAITING_ARTIFACT,
                            CampaignStatus.WAITING_DEPLOYMENT, CampaignStatus.WAITING_PROVIDER,
                            CampaignStatus.WAITING_EXTERNAL, CampaignStatus.RETRY_BACKOFF,
                        }
                        if state.status in waiting_statuses:
                            waiting += 1
                            if state.next_check_at and (
                                next_wake is None or state.next_check_at < next_wake
                            ):
                                next_wake = state.next_check_at
                        driver = CommandCampaignDriver(spec)
                        before = state.event_sequence

                        def guarded_work(claimed: CampaignState) -> StepResult:
                            with project_lock(spec.state_dir, claimed.project_id, "campaign-work"):
                                try:
                                    self._validate_canonical_binding(claimed, spec.state_dir)
                                except RetryableCanonicalBindingError:
                                    raise
                                except CanonicalBindingError as exc:
                                    return StepResult("BLOCKED_NON_RETRYABLE", reason=str(exc))
                                return driver.work(claimed)

                        def guarded_probe(current: CampaignState) -> None:
                            with project_lock(
                                spec.state_dir, current.project_id, "campaign-wake-binding"
                            ):
                                self._validate_canonical_binding(current, spec.state_dir)

                        after = PersistentCampaignRunner(store).tick(
                            driver.probe, guarded_work, wake_guard=guarded_probe
                        )
                    except (RetryableCanonicalBindingError, LockError) as exc:
                        after = PersistentCampaignRunner(store).schedule_retry(str(exc))
                        last_action = after.events[-1].event_type
                        continue
                    except CanonicalBindingError as exc:
                        after = PersistentCampaignRunner(store).invalidate_binding(str(exc))
                        last_action = after.events[-1].event_type
                        continue
                    if after.event_sequence != before:
                        last_action = after.events[-1].event_type
                self._write_status(RunnerStatus(
                    os.getpid(), self.instance_id, True, active, waiting, next_wake,
                    timestamp(utc_now()) if last_action == "WAKE" else previous,
                    last_action, timestamp(utc_now()),
                ))
                previous = timestamp(utc_now()) if last_action else previous
                loops += 1
                if max_loops is not None and loops >= max_loops:
                    return
                delay = 30.0
                if next_wake:
                    delay = max(
                        1.0,
                        min(30.0, (parse_timestamp(next_wake) - utc_now()).total_seconds()),
                    )
                self.sleep(delay)
        finally:
            self._write_status(RunnerStatus(os.getpid(), self.instance_id, False, 0, 0,
                                            None, previous, "STOPPED", timestamp(utc_now())))
            self._release_lock()

    def _validate_canonical_binding(self, state: CampaignState, state_dir: str | Path) -> None:
        """Reject waits whose target or explicit lineage no longer matches reality."""
        registry_root = Path(state_dir).expanduser().resolve()
        registry_path = registry_root / "projects.json"
        if not registry_path.exists():
            raise CanonicalBindingError("campaign project registry is unavailable")
        try:
            project = ProjectRegistry(registry_root).verify_read_only(state.project_id)
            if project.status.value != "ACTIVE":
                raise CanonicalBindingError("campaign project registration is not ACTIVE")
            root = Path(project.repository_root)
            if _git(root, "branch", "--show-current") != project.default_branch:
                raise CanonicalBindingError("campaign target is not on the canonical branch")
            actual = _git(root, "rev-parse", "HEAD")
            if actual != project.current_head_sha:
                raise CanonicalBindingError("campaign project registration target is stale")
            if actual != state.target_sha:
                raise CanonicalBindingError("campaign target SHA is stale or incompatible")
            expected_lineage = f"{project.name}:{project.default_branch}:{actual}"
            lineage_parts = state.lineage_binding.split(":")
            if (
                len(lineage_parts) == 3
                and lineage_parts[0] == project.name
                and lineage_parts[1] == project.default_branch
                and re.fullmatch(r"[0-9a-f]{40}", lineage_parts[2])
                and state.lineage_binding != expected_lineage
            ):
                raise CanonicalBindingError("campaign lineage binding is stale or incompatible")
            try:
                session = SessionStore(registry_root).load(state.session_id)
            except SessionStoreError as exc:
                raise CanonicalBindingError("campaign session binding cannot be verified") from exc
            if (
                session.project_id != state.project_id
                or session.base_sha != actual
                or session.status.value in {
                    "BLOCKED", "HUMAN_REQUIRED", "FAILED", "STALE", "COMPLETED", "CANCELLED",
                }
            ):
                raise CanonicalBindingError("campaign session binding is stale or incompatible")
            # Reconciliation evidence is optional for ordinary sessions. The
            # session base binding and project registry establish their target;
            # requiring this artifact would retire every pre-reconciliation
            # campaign on its first daemon wake.
            canonical_target = session.artifact_hashes.get("canonical_target")
            if canonical_target is not None and canonical_target != actual:
                raise CanonicalBindingError("campaign session target evidence is stale")
            try:
                authority = AuthorityContext.resolve_runtime(state.project_id, registry_root)
            except AuthorityContextError as exc:
                message = str(exc).lower()
                if any(
                    marker in message
                    for marker in ("unavailable", "cannot be read", "not found", "no such file")
                ):
                    raise RetryableCanonicalBindingError(
                        "campaign authority evidence is temporarily unavailable"
                    ) from exc
                raise CanonicalBindingError("campaign authority binding is invalid") from exc
            except (OSError, ValueError) as exc:
                raise RetryableCanonicalBindingError(
                    "campaign authority evidence is temporarily unavailable"
                ) from exc
            if authority is None:
                if state.policy_binding is not None or state.authority_generation is not None:
                    raise CanonicalBindingError("campaign authority binding is unavailable")
            elif (
                state.policy_binding != authority.policy_hash
                or state.authority_generation != authority.generation_number
            ):
                raise CanonicalBindingError("campaign policy or authority binding is stale")
        except (ProjectRegistryError, OSError, ValueError) as exc:
            raise CanonicalBindingError("campaign canonical binding cannot be verified") from exc

    def stop(self) -> None:
        self._stop = True

    def _load_specs(self) -> tuple[CampaignDriverSpec, ...]:
        if not self.spec_dir.exists():
            return ()
        specs = []
        for path in sorted(self.spec_dir.glob("campaign-*.json")):
            try:
                specs.append(CampaignDriverSpec.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError) as exc:
                raise CampaignDaemonError("campaign driver spec cannot be read") from exc
        return tuple(specs)

    def _acquire_lock(self) -> None:
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock_handle = self.lock_path.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise CampaignDaemonError("another campaign daemon is active") from exc

    def _release_lock(self) -> None:
        if self._lock_handle is not None:
            import fcntl
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        signal.signal(signal.SIGINT, lambda *_: self.stop())

    def _write_status(self, status: RunnerStatus) -> None:
        self._atomic_json(self.status_path, status.to_dict())

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".status.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise CampaignDaemonError("daemon state write failed") from exc


def render_launchd_plist(*, label: str, program: str, state_dir: str, log_dir: str) -> str:
    """Render a user launchd agent; loading it remains an explicit OS action."""
    for value in (label, program, state_dir, log_dir):
        if not value or any(char in value for char in "&<>"):
            raise CampaignDaemonError("launchd value is invalid")
    program_path = Path(program).expanduser().resolve()
    venv_root = program_path.parents[1] if program_path.parent.name == "bin" else None
    venv_bin = venv_root / "bin" if venv_root else program_path.parent
    source_root = venv_root.parent if venv_root else program_path.parent
    python_path = source_root / "src"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{program}</string><string>campaign-runner</string><string>run</string><string>--state-dir</string><string>{state_dir}</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>EnvironmentVariables</key><dict>
<key>PATH</key><string>{venv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/sbin</string>
<key>HOME</key><string>{os.path.expanduser('~')}</string>
<key>VIRTUAL_ENV</key><string>{venv_root or ''}</string>
<key>PYTHONNOUSERSITE</key><string>1</string>
<key>PYTHONPATH</key><string>{python_path}</string>
</dict>
<key>StandardOutPath</key><string>{log_dir}/campaign-runner.out.log</string>
<key>StandardErrorPath</key><string>{log_dir}/campaign-runner.err.log</string>
</dict></plist>
'''
