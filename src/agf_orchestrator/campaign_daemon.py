"""Independent macOS-friendly process supervisor for persistent campaigns.

The campaign runner owns state transitions; this module owns the process
lifecycle.  Driver commands are explicit argv lists and exchange only bounded
JSON on stdin/stdout.  No shell is used and no provider is invoked merely to
poll an external condition.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


class CampaignDaemonError(RuntimeError):
    """Raised when the independent campaign supervisor cannot continue safely."""


_MAX_COMMAND_OUTPUT = 64 * 1024
_MAX_COMMAND_ARGS = 64


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

    def __init__(self, spec: CampaignDriverSpec):
        self.spec = spec

    def _run(self, command: tuple[str, ...], state: CampaignState) -> dict[str, object]:
        try:
            result = subprocess.run(
                list(command), input=json.dumps(state.to_dict()) + "\n", text=True,
                capture_output=True, timeout=120, check=True, shell=False,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                     "AGF_CAMPAIGN_ID": state.campaign_id,
                     "AGF_SESSION_ID": state.session_id},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CampaignDaemonError("campaign driver command failed") from exc
        if len(result.stdout) > _MAX_COMMAND_OUTPUT:
            raise CampaignDaemonError("campaign driver output is too large")
        try:
            payload = json.loads(result.stdout)
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
                    after = PersistentCampaignRunner(store).tick(driver.probe, driver.work)
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
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{program}</string><string>campaign-runner</string><string>run</string><string>--state-dir</string><string>{state_dir}</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>{log_dir}/campaign-runner.out.log</string>
<key>StandardErrorPath</key><string>{log_dir}/campaign-runner.err.log</string>
</dict></plist>
'''
