"""Persistent, restartable campaign execution with bounded external waits.

The runner deliberately separates durable orchestration state from provider
invocations.  A provider may finish after one invocation; the persisted state
and the runner's wake condition are what keep the campaign alive.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

from .locking import project_lock


class CampaignRunnerError(RuntimeError):
    """Raised when a campaign state or runner operation is unsafe."""


class CampaignStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_CI = "WAITING_CI"
    WAITING_REVIEW = "WAITING_REVIEW"
    WAITING_GITHUB = "WAITING_GITHUB"
    WAITING_ARTIFACT = "WAITING_ARTIFACT"
    WAITING_DEPLOYMENT = "WAITING_DEPLOYMENT"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    COMPLETE = "COMPLETE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    BLOCKED_NON_RETRYABLE = "BLOCKED_NON_RETRYABLE"
    CANCELLED = "CANCELLED"


WAITING_STATUSES = {
    CampaignStatus.WAITING_CI,
    CampaignStatus.WAITING_REVIEW,
    CampaignStatus.WAITING_GITHUB,
    CampaignStatus.WAITING_ARTIFACT,
    CampaignStatus.WAITING_DEPLOYMENT,
    CampaignStatus.WAITING_PROVIDER,
    CampaignStatus.WAITING_EXTERNAL,
    CampaignStatus.RETRY_BACKOFF,
}
TERMINAL_STATUSES = {
    CampaignStatus.COMPLETE,
    CampaignStatus.HUMAN_REQUIRED,
    CampaignStatus.BLOCKED_NON_RETRYABLE,
    CampaignStatus.CANCELLED,
}
_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
_MAX_TEXT = 4000
_MAX_EVENTS = 500
_MAX_RETRY_BUDGET = 1000
_SHA = re.compile(r"^[0-9a-f]{40}$")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime(_TIMESTAMP)


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, _TIMESTAMP).replace(tzinfo=UTC)
    except (TypeError, ValueError) as exc:
        raise CampaignRunnerError("campaign timestamp is invalid") from exc


@dataclass(frozen=True)
class CampaignEvent:
    sequence: int
    event_type: str
    status: str
    timestamp: str
    summary: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "status": self.status,
            "timestamp": self.timestamp,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class CampaignState:
    schema_version: str
    campaign_id: str
    project_id: str
    session_id: str
    status: CampaignStatus
    phase: str
    reason: str | None
    resource: str | None
    expected_condition: str | None
    created_at: str
    updated_at: str
    waiting_since: str | None
    next_check_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    retry_count: int
    retry_budget: int
    operation_id: str
    target_sha: str
    lineage_binding: str
    wake_generation: int
    event_sequence: int
    events: tuple[CampaignEvent, ...] = ()

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise CampaignRunnerError("campaign schema_version must be 1.0")
        if not isinstance(self.status, CampaignStatus):
            raise CampaignRunnerError("campaign status is invalid")
        for label, value, prefix in (
            ("campaign_id", self.campaign_id, "campaign-"),
            ("project_id", self.project_id, "project-"),
            ("session_id", self.session_id, "session-"),
            ("operation_id", self.operation_id, "operation-"),
        ):
            if (
                not isinstance(value, str)
                or not value.startswith(prefix)
                or "/" in value
                or "\\" in value
            ):
                raise CampaignRunnerError(f"{label} is invalid")
        if not isinstance(self.phase, str) or not self.phase.strip() or len(self.phase) > _MAX_TEXT:
            raise CampaignRunnerError("campaign phase is invalid")
        if self.reason is not None and (not self.reason.strip() or len(self.reason) > _MAX_TEXT):
            raise CampaignRunnerError("campaign reason is invalid")
        for label, value in (
            ("resource", self.resource), ("expected_condition", self.expected_condition)
        ):
            if value is not None and (not value.strip() or len(value) > _MAX_TEXT):
                raise CampaignRunnerError(f"campaign {label} is invalid")
        for label, value in (("created_at", self.created_at), ("updated_at", self.updated_at)):
            parse_timestamp(value)
        if self.waiting_since is not None:
            parse_timestamp(self.waiting_since)
        if self.next_check_at is not None:
            parse_timestamp(self.next_check_at)
        if self.lease_expires_at is not None:
            parse_timestamp(self.lease_expires_at)
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise CampaignRunnerError("campaign lease fields must be paired")
        if self.status in WAITING_STATUSES:
            if not self.reason or not self.resource or not self.expected_condition:
                raise CampaignRunnerError("waiting campaign requires wake condition")
            if not self.waiting_since or not self.next_check_at:
                raise CampaignRunnerError("waiting campaign requires schedule")
        elif any(value is not None for value in (self.waiting_since, self.next_check_at)):
            raise CampaignRunnerError("non-waiting campaign cannot retain wake schedule")
        if not isinstance(self.retry_count, int) or self.retry_count < 0:
            raise CampaignRunnerError("retry_count is invalid")
        if (
            not isinstance(self.retry_budget, int)
            or not 0 <= self.retry_budget <= _MAX_RETRY_BUDGET
        ):
            raise CampaignRunnerError("retry_budget is invalid")
        if self.retry_count > self.retry_budget:
            raise CampaignRunnerError("retry_count exceeds retry_budget")
        if not isinstance(self.target_sha, str) or not _SHA.fullmatch(self.target_sha):
            raise CampaignRunnerError("target_sha is invalid")
        if not isinstance(self.lineage_binding, str) or not self.lineage_binding.strip():
            raise CampaignRunnerError("lineage_binding is invalid")
        if not isinstance(self.wake_generation, int) or self.wake_generation < 0:
            raise CampaignRunnerError("wake_generation is invalid")
        if not isinstance(self.event_sequence, int) or self.event_sequence < 0:
            raise CampaignRunnerError("event_sequence is invalid")
        if len(self.events) > _MAX_EVENTS or len(self.events) != self.event_sequence:
            raise CampaignRunnerError("campaign event journal is invalid")
        for expected, event in enumerate(self.events, 1):
            if (
                event.sequence != expected
                or not event.event_type.strip()
                or not event.summary.strip()
            ):
                raise CampaignRunnerError("campaign event is invalid")
            parse_timestamp(event.timestamp)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "phase": self.phase,
            "reason": self.reason,
            "resource": self.resource,
            "expected_condition": self.expected_condition,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "waiting_since": self.waiting_since,
            "next_check_at": self.next_check_at,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "retry_count": self.retry_count,
            "retry_budget": self.retry_budget,
            "operation_id": self.operation_id,
            "target_sha": self.target_sha,
            "lineage_binding": self.lineage_binding,
            "wake_generation": self.wake_generation,
            "event_sequence": self.event_sequence,
            "events": [event.to_dict() for event in self.events],
        }


def campaign_from_dict(payload: dict[str, object]) -> CampaignState:
    if not isinstance(payload, dict):
        raise CampaignRunnerError("campaign state is not an object")
    required = {
        "schema_version", "campaign_id", "project_id", "session_id", "status", "phase",
        "reason", "resource", "expected_condition", "created_at", "updated_at",
        "waiting_since", "next_check_at", "lease_owner", "lease_expires_at", "retry_count",
        "retry_budget", "operation_id",
        "target_sha", "lineage_binding", "wake_generation", "event_sequence", "events",
    }
    if set(payload) != required:
        raise CampaignRunnerError("campaign schema is missing or contains unknown fields")
    try:
        events = tuple(CampaignEvent(**item) for item in payload["events"])
        state = CampaignState(
            schema_version=payload["schema_version"], campaign_id=payload["campaign_id"],
            project_id=payload["project_id"], session_id=payload["session_id"],
            status=CampaignStatus(payload["status"]), phase=payload["phase"],
            reason=payload["reason"], resource=payload["resource"],
            expected_condition=payload["expected_condition"], created_at=payload["created_at"],
            updated_at=payload["updated_at"], waiting_since=payload["waiting_since"],
            next_check_at=payload["next_check_at"], lease_owner=payload["lease_owner"],
            lease_expires_at=payload["lease_expires_at"], retry_count=payload["retry_count"],
            retry_budget=payload["retry_budget"], operation_id=payload["operation_id"],
            target_sha=payload["target_sha"], lineage_binding=payload["lineage_binding"],
            wake_generation=payload["wake_generation"], event_sequence=payload["event_sequence"],
            events=events,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignRunnerError("campaign state structure is invalid") from exc
    state.validate()
    return state


class CampaignStore:
    """Atomic, project-isolated persistence for one campaign."""

    def __init__(self, state_dir: str | Path, project_id: str, campaign_id: str):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_id = project_id
        self.campaign_id = campaign_id
        self.path = self.state_dir / "campaigns" / project_id / f"{campaign_id}.json"

    def create(self, state: CampaignState) -> CampaignState:
        self._validate_identity(state)
        with project_lock(self.state_dir, self.project_id, "campaign-create", timeout=5.0):
            existing = self._load_unlocked()
            if existing is not None:
                if existing == state:
                    return existing
                raise CampaignRunnerError("campaign already exists with different content")
            self._save_unlocked(state)
        return state

    def load(self) -> CampaignState:
        with project_lock(self.state_dir, self.project_id, "campaign-load", timeout=5.0):
            state = self._load_unlocked()
        if state is None:
            raise CampaignRunnerError("campaign state was not found")
        return state

    def save(self, state: CampaignState) -> CampaignState:
        self._validate_identity(state)
        with project_lock(self.state_dir, self.project_id, "campaign-save", timeout=5.0):
            current = self._load_unlocked()
            if current is None:
                raise CampaignRunnerError("campaign state was not found")
            if state.event_sequence < current.event_sequence:
                raise CampaignRunnerError("campaign event sequence regressed")
            if state.event_sequence == current.event_sequence and state != current:
                raise CampaignRunnerError("campaign state changed concurrently")
            self._save_unlocked(state)
        return state

    def claim(self, worker_id: str, lease_expires_at: str) -> CampaignState | None:
        """Atomically claim one provider/work invocation, or return None if busy."""
        if not worker_id.startswith("runner-") or "/" in worker_id:
            raise CampaignRunnerError("campaign worker identity is invalid")
        expiry = parse_timestamp(lease_expires_at)
        with project_lock(self.state_dir, self.project_id, "campaign-claim", timeout=5.0):
            current = self._load_unlocked()
            if current is None:
                raise CampaignRunnerError("campaign state was not found")
            if current.status in TERMINAL_STATUSES:
                return None
            if (
                current.lease_owner is not None
                and parse_timestamp(current.lease_expires_at or "") > utc_now()
            ):
                return None
            event = CampaignEvent(
                current.event_sequence + 1, "WORK_CLAIM", current.status.value,
                timestamp(utc_now()), "provider/work lease claimed",
            )
            claimed = replace(
                current, lease_owner=worker_id, lease_expires_at=timestamp(expiry),
                updated_at=event.timestamp, event_sequence=event.sequence,
                events=(*current.events, event),
            )
            self._save_unlocked(claimed)
            return claimed

    def _validate_identity(self, state: CampaignState) -> None:
        state.validate()
        if state.project_id != self.project_id or state.campaign_id != self.campaign_id:
            raise CampaignRunnerError("campaign state identity does not match store")

    def _load_unlocked(self) -> CampaignState | None:
        if not self.path.exists():
            return None
        try:
            return campaign_from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise CampaignRunnerError("campaign state cannot be read") from exc

    def _save_unlocked(self, state: CampaignState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".campaign.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise CampaignRunnerError("campaign state write failed") from exc


@dataclass(frozen=True)
class WaitRequest:
    status: CampaignStatus
    reason: str
    resource: str
    expected_condition: str
    next_check_at: str

    def __post_init__(self) -> None:
        if self.status not in WAITING_STATUSES:
            raise CampaignRunnerError("wait request status is not resumable")


@dataclass(frozen=True)
class StepResult:
    outcome: str
    wait: WaitRequest | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            "CONTINUE", "WAIT", "COMPLETE", "HUMAN_REQUIRED",
            "BLOCKED_NON_RETRYABLE", "CANCELLED",
        }
        if self.outcome not in allowed:
            raise CampaignRunnerError("campaign step outcome is invalid")
        if self.outcome == "WAIT" and self.wait is None:
            raise CampaignRunnerError("WAIT outcome requires a wait request")
        if self.outcome != "WAIT" and self.wait is not None:
            raise CampaignRunnerError("only WAIT may carry a wait request")


class CampaignProbe(Protocol):
    def __call__(self, state: CampaignState) -> bool: ...


class CampaignWork(Protocol):
    def __call__(self, state: CampaignState) -> StepResult: ...


def make_initial_state(
    *, project_id: str, campaign_id: str, session_id: str, phase: str,
    operation_id: str, target_sha: str, lineage_binding: str, retry_budget: int,
    now: datetime | None = None,
) -> CampaignState:
    current = timestamp(now or utc_now())
    state = CampaignState(
        schema_version="1.0", campaign_id=campaign_id, project_id=project_id,
        session_id=session_id, status=CampaignStatus.RUNNING, phase=phase,
        reason=None, resource=None, expected_condition=None, created_at=current,
        updated_at=current, waiting_since=None, next_check_at=None, retry_count=0,
        lease_owner=None, lease_expires_at=None,
        retry_budget=retry_budget, operation_id=operation_id, target_sha=target_sha,
        lineage_binding=lineage_binding, wake_generation=0, event_sequence=0, events=(),
    )
    state.validate()
    return state


class PersistentCampaignRunner:
    """Drive one campaign while making every wait and wake durable.

    ``tick`` performs at most one external probe and one provider/work call.
    A service may call ``run_forever``; a restart simply constructs a runner
    over the same store and calls ``tick`` again.
    """

    def __init__(
        self, store: CampaignStore, *, now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] | None = None,
        base_backoff_seconds: int = 30, max_backoff_seconds: int = 3600,
    ):
        if not 1 <= base_backoff_seconds <= max_backoff_seconds:
            raise CampaignRunnerError("backoff bounds are invalid")
        self.store = store
        self.now = now
        self.sleep = sleep
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.worker_id = f"runner-{uuid.uuid4().hex}"
        self.lease_seconds = 300

    def tick(self, probe: CampaignProbe, work: CampaignWork) -> CampaignState:
        state = self.store.load()
        if state.status in TERMINAL_STATUSES:
            return state
        current_time = self.now().astimezone(UTC).replace(microsecond=0)
        if state.status in WAITING_STATUSES:
            next_check = parse_timestamp(state.next_check_at or "")
            if current_time < next_check:
                return state
            try:
                ready = bool(probe(state))
            except Exception as exc:  # noqa: BLE001 - external boundary is fail-safe
                return self._schedule_retry(state, f"external probe failed: {type(exc).__name__}")
            if not ready:
                return self._schedule_retry(state, "external condition not satisfied")
            state = self._append(state, "WAKE", "RUNNING", "external condition satisfied")
            state = replace(
                state, status=CampaignStatus.RUNNING, reason=None, resource=None,
                expected_condition=None, waiting_since=None, next_check_at=None,
            )
            try:
                self.store.save(state)
            except CampaignRunnerError as exc:
                if "changed concurrently" not in str(exc):
                    raise
                return self.store.load()
        claimed = self.store.claim(
            self.worker_id, timestamp(self.now() + timedelta(seconds=self.lease_seconds))
        )
        if claimed is None:
            return self.store.load()
        try:
            result = work(claimed)
        except Exception as exc:  # noqa: BLE001 - provider boundary is persisted
            return self._schedule_retry(claimed, f"work invocation failed: {type(exc).__name__}")
        return self._apply_result(claimed, result)

    def run_forever(
        self, probe: CampaignProbe, work: CampaignWork, *, max_ticks: int | None = None,
    ) -> CampaignState:
        ticks = 0
        while True:
            state = self.tick(probe, work)
            ticks += 1
            if state.status in TERMINAL_STATUSES:
                return state
            if max_ticks is not None and ticks >= max_ticks:
                return state
            wait_seconds = self._sleep_seconds(state)
            if self.sleep is None:
                raise CampaignRunnerError("persistent runner requires a sleep function")
            self.sleep(wait_seconds)

    def _apply_result(self, state: CampaignState, result: StepResult) -> CampaignState:
        now = timestamp(self.now())
        if result.outcome == "WAIT":
            wait = result.wait
            assert wait is not None
            updated = self._append(state, "WAIT", wait.status.value, result.reason or wait.reason)
            updated = replace(
                updated, status=wait.status, reason=wait.reason, resource=wait.resource,
                expected_condition=wait.expected_condition, waiting_since=now,
                next_check_at=wait.next_check_at, retry_count=state.retry_count,
                lease_owner=None, lease_expires_at=None,
            )
        else:
            target = (
                CampaignStatus.RUNNING
                if result.outcome == "CONTINUE"
                else CampaignStatus(result.outcome)
            )
            updated = self._append(state, "STEP", target.value, result.reason or result.outcome)
            updated = replace(updated, status=target, reason=result.reason,
                              waiting_since=None, next_check_at=None,
                              lease_owner=None, lease_expires_at=None)
        self.store.save(updated)
        return updated

    def _schedule_retry(self, state: CampaignState, reason: str) -> CampaignState:
        count = state.retry_count + 1
        if count > state.retry_budget:
            updated = self._append(state, "RETRY_EXHAUSTED", "BLOCKED_NON_RETRYABLE", reason)
            updated = replace(updated, status=CampaignStatus.BLOCKED_NON_RETRYABLE,
                              reason=reason, waiting_since=None, next_check_at=None,
                              retry_count=count - 1, lease_owner=None, lease_expires_at=None)
        else:
            delay = min(
                self.max_backoff_seconds,
                self.base_backoff_seconds * (2 ** min(count - 1, 10)),
            )
            next_check = timestamp(self.now() + timedelta(seconds=delay))
            updated = self._append(
                state, "RETRY_BACKOFF", CampaignStatus.RETRY_BACKOFF.value, reason
            )
            updated = replace(
                updated, status=CampaignStatus.RETRY_BACKOFF, reason=reason,
                resource=state.resource or "campaign-external-boundary",
                expected_condition=state.expected_condition or "retry budget remains",
                waiting_since=timestamp(self.now()), next_check_at=next_check,
                retry_count=count, lease_owner=None, lease_expires_at=None,
            )
        self.store.save(updated)
        return updated

    def _append(
        self, state: CampaignState, event_type: str, status: str, summary: str
    ) -> CampaignState:
        sequence = state.event_sequence + 1
        event = CampaignEvent(sequence, event_type, status, timestamp(self.now()), summary)
        return replace(
            state, updated_at=timestamp(self.now()), event_sequence=sequence,
            wake_generation=state.wake_generation + (1 if event_type == "WAKE" else 0),
            events=(*state.events, event),
        )

    def _sleep_seconds(self, state: CampaignState) -> float:
        if state.next_check_at is None:
            return 0.0
        delta = (parse_timestamp(state.next_check_at) - self.now().astimezone(UTC)).total_seconds()
        return max(0.0, delta)
