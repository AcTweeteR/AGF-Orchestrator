"""Disposable, project-isolated intake boundary for autonomous pilots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any


class PilotIntakeError(ValueError):
    """Raised when pilot intake cannot be accepted safely."""


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_OBJECTIVE = re.compile(r"^objective-[a-z0-9][a-z0-9-]{0,79}$")
_POLICY = re.compile(r"^policy-[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_INTAKES = 100


@dataclass(frozen=True)
class PilotIntake:
    schema_version: str
    intake_id: str
    project_id: str
    objective_id: str
    policy_id: str
    policy_hash: str
    budget_steps: int
    created_at: str
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intake_id": self.intake_id,
            "project_id": self.project_id,
            "objective_id": self.objective_id,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "budget_steps": self.budget_steps,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise PilotIntakeError("schema_version must be 1.0")
        if not isinstance(self.intake_id, str) or not _ID.fullmatch(self.intake_id):
            raise PilotIntakeError("intake_id is invalid")
        for label, value, pattern in (
            ("project_id", self.project_id, _PROJECT),
            ("objective_id", self.objective_id, _OBJECTIVE),
            ("policy_id", self.policy_id, _POLICY),
        ):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise PilotIntakeError(f"{label} is invalid")
        if not isinstance(self.policy_hash, str) or not _SHA256.fullmatch(self.policy_hash):
            raise PilotIntakeError("policy_hash is invalid")
        if (
            not isinstance(self.budget_steps, int)
            or isinstance(self.budget_steps, bool)
            or not 1 <= self.budget_steps <= 100
        ):
            raise PilotIntakeError("budget_steps is outside the bound")
        if not isinstance(self.created_at, str) or not _TIMESTAMP.fullmatch(self.created_at):
            raise PilotIntakeError("created_at is invalid")
        try:
            datetime.strptime(self.created_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise PilotIntakeError("created_at is not a real UTC instant") from exc
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise PilotIntakeError("content_sha256 is invalid")
        if self.content_sha256 != intake_hash(self):
            raise PilotIntakeError("content_sha256 does not match intake")
        if _SECRET.search(json.dumps(self.to_dict(), sort_keys=True)):
            raise PilotIntakeError("intake contains secret-shaped data")


@dataclass(frozen=True)
class PilotIntakeEvidence:
    intake_id: str
    project_id: str
    policy_id: str
    policy_hash: str
    accepted_at: str
    bounded: bool = True
    external_mutation: bool = False


class PilotIntakeLedger:
    """In-memory, hash-bound intake ledger; it cannot execute or activate pilots."""

    def __init__(
        self,
        project_id: str,
        *,
        policy_id: str | None = None,
        policy_hash: str | None = None,
    ) -> None:
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise PilotIntakeError("project_id is invalid")
        if (policy_id is None) != (policy_hash is None):
            raise PilotIntakeError("policy binding is incomplete")
        if policy_id is not None and (
            not _POLICY.fullmatch(policy_id) or not _SHA256.fullmatch(policy_hash)
        ):
            raise PilotIntakeError("policy binding is invalid")
        self.project_id = project_id
        self._policy_id = policy_id
        self._policy_hash = policy_hash
        self._records: dict[str, PilotIntake] = {}

    def record(
        self,
        intake: PilotIntake,
        *,
        expected_policy_id: str,
        expected_policy_hash: str,
    ) -> PilotIntakeEvidence:
        intake.validate()
        if intake.project_id != self.project_id:
            raise PilotIntakeError("intake project binding does not match ledger")
        if self._policy_id is None:
            self._policy_id = expected_policy_id
            self._policy_hash = expected_policy_hash
        elif (
            self._policy_id != expected_policy_id or self._policy_hash != expected_policy_hash
        ):
            raise PilotIntakeError("ledger policy binding does not match expected policy")
        if intake.policy_id != expected_policy_id or intake.policy_hash != expected_policy_hash:
            raise PilotIntakeError("intake policy binding does not match expected policy")
        previous = self._records.get(intake.intake_id)
        if previous is not None:
            if previous.content_sha256 != intake.content_sha256:
                raise PilotIntakeError("conflicting intake is rejected")
        elif len(self._records) >= _MAX_INTAKES:
            raise PilotIntakeError("intake ledger bound exceeded")
        else:
            self._records[intake.intake_id] = intake
        return PilotIntakeEvidence(
            intake.intake_id, intake.project_id, intake.policy_id,
            intake.policy_hash, intake.created_at,
        )

    def get(self, intake_id: str) -> PilotIntake:
        try:
            return self._records[intake_id]
        except KeyError as exc:
            raise PilotIntakeError("intake is not recorded") from exc

    def export_state(self) -> str:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "project_id": self.project_id,
            "policy_id": self._policy_id,
            "policy_hash": self._policy_hash,
            "records": [record.to_dict() for _, record in sorted(self._records.items())],
        }
        return json.dumps(
            {**payload, "state_sha256": _sha256(_canonical(payload))},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    @classmethod
    def from_state(cls, serialized: str) -> "PilotIntakeLedger":
        try:
            payload = json.loads(serialized)
            if not isinstance(payload, dict):
                raise PilotIntakeError("ledger state is invalid")
            state_hash = payload.pop("state_sha256")
            if _sha256(_canonical(payload)) != state_hash:
                raise PilotIntakeError("ledger state hash is invalid")
            required = {"schema_version", "project_id", "policy_id", "policy_hash", "records"}
            if set(payload) != required or payload.get("schema_version") != "1.0" or not isinstance(
                payload.get("records"), list
            ):
                raise PilotIntakeError("ledger state schema is invalid")
            ledger = cls(
                payload["project_id"], policy_id=payload["policy_id"],
                policy_hash=payload["policy_hash"],
            )
            for item in payload["records"]:
                intake = intake_from_dict(item)
                ledger.record(
                    intake, expected_policy_id=intake.policy_id,
                    expected_policy_hash=intake.policy_hash,
                )
            return ledger
        except PilotIntakeError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotIntakeError("ledger state is invalid") from exc


def intake_hash(intake: PilotIntake) -> str:
    payload = intake.to_dict()
    payload["content_sha256"] = ""
    return _sha256(_canonical(payload))


def intake_from_dict(payload: dict[str, Any]) -> PilotIntake:
    required = {
        "schema_version", "intake_id", "project_id", "objective_id", "policy_id",
        "policy_hash", "budget_steps", "created_at", "content_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise PilotIntakeError("intake schema is missing or contains unknown fields")
    try:
        intake = PilotIntake(
            payload["schema_version"], payload["intake_id"], payload["project_id"],
            payload["objective_id"], payload["policy_id"], payload["policy_hash"],
            payload["budget_steps"], payload["created_at"], payload["content_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PilotIntakeError("intake structure is invalid") from exc
    intake.validate()
    return intake


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PilotRunError(ValueError):
    """Raised when a bounded pilot run transition is unsafe."""


@dataclass(frozen=True)
class PilotRunEvent:
    operation_id: str
    run_id: str
    project_id: str
    sequence: int
    kind: str
    detail: str
    observed_at: str
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id, "run_id": self.run_id,
            "project_id": self.project_id, "sequence": self.sequence,
            "kind": self.kind, "detail": self.detail,
            "observed_at": self.observed_at, "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class PilotRun:
    run_id: str
    intake_id: str
    project_id: str
    policy_id: str
    policy_hash: str
    budget_steps: int
    sequence: int
    status: str


class PilotRunLedger:
    """Project-isolated bounded transition ledger with restart-safe idempotency."""

    def __init__(self, project_id: str, *, policy_id: str, policy_hash: str) -> None:
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise PilotRunError("project_id is invalid")
        if not _POLICY.fullmatch(policy_id) or not _SHA256.fullmatch(policy_hash):
            raise PilotRunError("policy binding is invalid")
        self.project_id = project_id
        self.policy_id = policy_id
        self.policy_hash = policy_hash
        self._runs: dict[str, PilotRun] = {}
        self._events: dict[str, PilotRunEvent] = {}

    def start(self, intake: PilotIntake) -> PilotRun:
        intake.validate()
        if intake.project_id != self.project_id:
            raise PilotRunError("intake project binding does not match ledger")
        if intake.policy_id != self.policy_id or intake.policy_hash != self.policy_hash:
            raise PilotRunError("intake policy binding does not match ledger")
        run_id = f"run-{intake.intake_id.removeprefix('intake-')}"
        current = self._runs.get(run_id)
        if current is not None:
            if current.intake_id != intake.intake_id or current.budget_steps != intake.budget_steps:
                raise PilotRunError("conflicting run start is rejected")
            return current
        run = PilotRun(
            run_id, intake.intake_id, intake.project_id, intake.policy_id,
            intake.policy_hash, intake.budget_steps, 0, "RUNNING",
        )
        self._runs[run_id] = run
        return run

    def apply(
        self, run_id: str, operation_id: str, kind: str, detail: str, observed_at: str
    ) -> PilotRun:
        if not _ID.fullmatch(operation_id) or not operation_id.startswith("operation-"):
            raise PilotRunError("operation_id is invalid")
        if kind not in {"STEP", "FAIL", "COMPLETE"}:
            raise PilotRunError("event kind is invalid")
        if (
            not isinstance(detail, str)
            or not detail.strip()
            or len(detail) > 200
            or _SECRET.search(detail)
        ):
            raise PilotRunError("event detail is invalid")
        if not _TIMESTAMP.fullmatch(observed_at):
            raise PilotRunError("observed_at is invalid")
        try:
            datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise PilotRunError("observed_at is not a real UTC instant") from exc
        run = self._runs.get(run_id)
        if run is None:
            raise PilotRunError("run is not recorded")
        previous = self._events.get(operation_id)
        event = PilotRunEvent(
            operation_id, run_id, self.project_id,
            previous.sequence if previous is not None else run.sequence + 1,
            kind, detail, observed_at, "0" * 64,
        )
        event = replace(event, content_sha256=event_hash(event))
        if previous is not None:
            if previous.content_sha256 == event.content_sha256:
                return run
            raise PilotRunError("conflicting operation replay is rejected")
        if run.status != "RUNNING":
            raise PilotRunError("terminal run cannot advance")
        if event.sequence > run.budget_steps:
            raise PilotRunError("run budget exceeded")
        self._events[operation_id] = event
        status = "FAILED" if kind == "FAIL" else "COMPLETED" if kind == "COMPLETE" else "RUNNING"
        updated = replace(run, sequence=event.sequence, status=status)
        self._runs[run_id] = updated
        return updated

    def get(self, run_id: str) -> PilotRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise PilotRunError("run is not recorded") from exc

    def export_state(self) -> str:
        payload: dict[str, Any] = {
            "schema_version": "1.0", "project_id": self.project_id,
            "policy_id": self.policy_id, "policy_hash": self.policy_hash,
            "runs": [run.__dict__ for _, run in sorted(self._runs.items())],
            "events": [event.to_dict() for _, event in sorted(self._events.items())],
        }
        return json.dumps(
            {**payload, "state_sha256": _sha256(_canonical(payload))},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    @classmethod
    def from_state(cls, serialized: str) -> "PilotRunLedger":
        try:
            payload = json.loads(serialized)
            if not isinstance(payload, dict):
                raise PilotRunError("run state is invalid")
            state_hash = payload.pop("state_sha256")
            if _sha256(_canonical(payload)) != state_hash:
                raise PilotRunError("run state hash is invalid")
            required = {
                "schema_version", "project_id", "policy_id", "policy_hash", "runs", "events",
            }
            if set(payload) != required or payload["schema_version"] != "1.0":
                raise PilotRunError("run state schema is invalid")
            ledger = cls(
                payload["project_id"], policy_id=payload["policy_id"],
                policy_hash=payload["policy_hash"],
            )
            for raw in payload["runs"]:
                run = PilotRun(**raw)
                _validate_run(run, ledger)
                if run.run_id in ledger._runs:
                    raise PilotRunError("duplicate run identity is rejected")
                ledger._runs[run.run_id] = run
            for raw in payload["events"]:
                event = PilotRunEvent(**raw)
                _validate_event(event, ledger)
                if event.operation_id in ledger._events:
                    raise PilotRunError("duplicate operation identity is rejected")
                ledger._events[event.operation_id] = event
            for run in ledger._runs.values():
                events = sorted(
                    (event for event in ledger._events.values() if event.run_id == run.run_id),
                    key=lambda event: event.sequence,
                )
                if [event.sequence for event in events] != list(range(1, run.sequence + 1)):
                    raise PilotRunError("run event sequence is inconsistent")
                if any(event.kind in {"FAIL", "COMPLETE"} for event in events[:-1]):
                    raise PilotRunError("run contains an event after terminal state")
                if run.status == "RUNNING" and events and events[-1].kind != "STEP":
                    raise PilotRunError("running run terminal event is invalid")
                if run.status == "FAILED" and (not events or events[-1].kind != "FAIL"):
                    raise PilotRunError("failed run terminal event is invalid")
                if run.status == "COMPLETED" and (not events or events[-1].kind != "COMPLETE"):
                    raise PilotRunError("completed run terminal event is invalid")
            return ledger
        except PilotRunError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotRunError("run state is invalid") from exc


def event_hash(event: PilotRunEvent) -> str:
    payload = event.to_dict()
    payload["content_sha256"] = ""
    return _sha256(_canonical(payload))


def _validate_run(run: PilotRun, ledger: PilotRunLedger) -> None:
    if (
        not _ID.fullmatch(run.run_id)
        or not run.run_id.startswith("run-")
        or not _ID.fullmatch(run.intake_id)
        or not run.intake_id.startswith("intake-")
        or run.project_id != ledger.project_id
        or run.policy_id != ledger.policy_id
        or run.policy_hash != ledger.policy_hash
        or not isinstance(run.budget_steps, int)
        or isinstance(run.budget_steps, bool)
        or not 1 <= run.budget_steps <= 100
        or not isinstance(run.sequence, int)
        or isinstance(run.sequence, bool)
        or not 0 <= run.sequence <= run.budget_steps
        or run.status not in {"RUNNING", "FAILED", "COMPLETED"}
    ):
        raise PilotRunError("run invariants are invalid")


def _validate_event(event: PilotRunEvent, ledger: PilotRunLedger) -> None:
    run = ledger._runs.get(event.run_id)
    if (
        not _ID.fullmatch(event.operation_id)
        or not event.operation_id.startswith("operation-")
        or run is None
        or event.project_id != ledger.project_id
        or not 1 <= event.sequence <= run.budget_steps
        or event.kind not in {"STEP", "FAIL", "COMPLETE"}
        or not isinstance(event.detail, str)
        or not event.detail.strip()
        or len(event.detail) > 200
        or _SECRET.search(event.detail)
        or not _TIMESTAMP.fullmatch(event.observed_at)
        or not _SHA256.fullmatch(event.content_sha256)
        or event.content_sha256 != event_hash(event)
    ):
        raise PilotRunError("event invariants are invalid")
    try:
        datetime.strptime(event.observed_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PilotRunError("event timestamp is invalid") from exc
