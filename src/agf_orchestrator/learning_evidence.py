"""Bounded, attributable outcome evidence for self-audit."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class LearningEvidenceError(ValueError):
    """Raised when outcome evidence is invalid, contradictory, or out of scope."""


class OutcomeStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    BLOCKED = "BLOCKED"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_TEXT = 1000
_MAX_RECORDS = 200


@dataclass(frozen=True)
class LearningEvidence:
    schema_version: str
    evidence_id: str
    project_id: str
    observation_id: str
    subject_id: str
    outcome: OutcomeStatus
    score_delta: int
    source: str
    observed_at: str
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "project_id": self.project_id,
            "observation_id": self.observation_id,
            "subject_id": self.subject_id,
            "outcome": self.outcome.value,
            "score_delta": self.score_delta,
            "source": self.source,
            "observed_at": self.observed_at,
            "content_sha256": self.content_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise LearningEvidenceError("schema_version must be 1.0")
        if (
            not isinstance(self.evidence_id, str)
            or not _ID.fullmatch(self.evidence_id)
            or not self.evidence_id.startswith("learning-")
        ):
            raise LearningEvidenceError("evidence_id is invalid")
        if not isinstance(self.project_id, str) or not _PROJECT.fullmatch(self.project_id):
            raise LearningEvidenceError("project_id is invalid")
        for label, value, prefix in (
            ("observation_id", self.observation_id, "observation-"),
            ("subject_id", self.subject_id, "subject-"),
        ):
            if (
                not isinstance(value, str)
                or not _ID.fullmatch(value)
                or not value.startswith(prefix)
            ):
                raise LearningEvidenceError(f"{label} is invalid")
        if not isinstance(self.outcome, OutcomeStatus):
            raise LearningEvidenceError("outcome is invalid")
        if not isinstance(self.score_delta, int) or isinstance(self.score_delta, bool):
            raise LearningEvidenceError("score_delta is invalid")
        if not -10 <= self.score_delta <= 10:
            raise LearningEvidenceError("score_delta is outside the bound")
        _bounded_text("source", self.source)
        if not isinstance(self.observed_at, str) or not _TIMESTAMP.fullmatch(self.observed_at):
            raise LearningEvidenceError("observed_at is invalid")
        try:
            datetime.strptime(self.observed_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise LearningEvidenceError("observed_at is not a real UTC instant") from exc
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise LearningEvidenceError("content_sha256 is invalid")
        if self.content_sha256 != evidence_hash(self):
            raise LearningEvidenceError("content_sha256 does not match evidence")


class LearningEvidenceLedger:
    """Project-isolated, idempotent ledger with contradiction and restart checks."""

    def __init__(self, project_id: str) -> None:
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise LearningEvidenceError("project_id is invalid")
        self.project_id = project_id
        self._records: dict[str, LearningEvidence] = {}

    def record(self, evidence: LearningEvidence) -> bool:
        evidence.validate()
        if evidence.project_id != self.project_id:
            raise LearningEvidenceError("evidence project binding does not match ledger")
        previous = self._records.get(evidence.observation_id)
        if previous is not None:
            if previous.content_sha256 == evidence.content_sha256:
                return False
            raise LearningEvidenceError("contradictory evidence is rejected")
        if len(self._records) >= _MAX_RECORDS:
            raise LearningEvidenceError("evidence ledger bound exceeded")
        self._records[evidence.observation_id] = evidence
        return True

    def get(self, observation_id: str) -> LearningEvidence:
        try:
            return self._records[observation_id]
        except KeyError as exc:
            raise LearningEvidenceError("evidence is not recorded") from exc

    def export_state(self) -> str:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "project_id": self.project_id,
            "records": [record.to_dict() for _, record in sorted(self._records.items())],
        }
        canonical = _canonical(payload)
        return json.dumps(
            {**payload, "state_sha256": _sha256(canonical)},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    @classmethod
    def from_state(cls, serialized: str) -> "LearningEvidenceLedger":
        try:
            payload = json.loads(serialized)
            state_hash = payload.pop("state_sha256")
            if _sha256(_canonical(payload)) != state_hash:
                raise LearningEvidenceError("ledger state hash is invalid")
            if payload.get("schema_version") != "1.0" or not isinstance(
                payload.get("records"), list
            ):
                raise LearningEvidenceError("ledger state schema is invalid")
            ledger = cls(payload["project_id"])
            for item in payload["records"]:
                ledger.record(evidence_from_dict(item))
            return ledger
        except LearningEvidenceError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LearningEvidenceError("ledger state is invalid") from exc


def evidence_hash(evidence: LearningEvidence) -> str:
    payload = evidence.to_dict()
    payload["content_sha256"] = ""
    return _sha256(_canonical(payload))


def evidence_from_dict(payload: dict[str, Any]) -> LearningEvidence:
    required = {
        "schema_version", "evidence_id", "project_id", "observation_id", "subject_id",
        "outcome", "score_delta", "source", "observed_at", "content_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise LearningEvidenceError("evidence schema is missing or contains unknown fields")
    try:
        evidence = LearningEvidence(
            payload["schema_version"], payload["evidence_id"], payload["project_id"],
            payload["observation_id"], payload["subject_id"], OutcomeStatus(payload["outcome"]),
            payload["score_delta"], payload["source"], payload["observed_at"],
            payload["content_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningEvidenceError("evidence structure is invalid") from exc
    evidence.validate()
    return evidence


def _bounded_text(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise LearningEvidenceError(f"{label} is invalid")
    if _SECRET.search(value):
        raise LearningEvidenceError(f"{label} contains secret-shaped data")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
