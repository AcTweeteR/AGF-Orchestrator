"""Disposable, project-isolated intake boundary for autonomous pilots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
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
