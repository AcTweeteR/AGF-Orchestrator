"""Bounded deterministic risk assessment schema."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any


class RiskValidationError(ValueError):
    """Raised when a risk assessment or signal is invalid."""


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class SignalLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class RollbackDifficulty(StrEnum):
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    UNKNOWN = "UNKNOWN"


_ASSESSMENT_ID = re.compile(r"^risk-[a-z0-9][a-z0-9-]{0,79}$")
_SIGNAL_ID = re.compile(r"^signal-[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT_ID = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_TASK_ID = re.compile(r"^task-[a-z0-9][a-z0-9-]{0,79}$")
_MAX_TEXT = 4000
_MAX_ITEMS = 200
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")


@dataclass(frozen=True)
class RiskSignal:
    signal_id: str
    category: str
    level: SignalLevel
    value: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "category": self.category,
            "level": self.level.value,
            "value": self.value,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class RiskAssessment:
    schema_version: str
    assessment_id: str
    project_id: str
    task_id: str
    level: RiskLevel
    signals: tuple[RiskSignal, ...]
    rollback_difficulty: RollbackDifficulty
    incident_count: int | None
    protected_paths: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assessment_id": self.assessment_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "level": self.level.name,
            "signals": [signal.to_dict() for signal in self.signals],
            "rollback_difficulty": self.rollback_difficulty.value,
            "incident_count": self.incident_count,
            "protected_paths": list(self.protected_paths),
            "evidence_refs": list(self.evidence_refs),
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise RiskValidationError("schema_version must be 1.0")
        if not _ASSESSMENT_ID.fullmatch(self.assessment_id):
            raise RiskValidationError("assessment_id is invalid")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise RiskValidationError("project_id is invalid")
        if not _TASK_ID.fullmatch(self.task_id):
            raise RiskValidationError("task_id is invalid")
        if not isinstance(self.level, RiskLevel):
            raise RiskValidationError("level is invalid")
        if not isinstance(self.rollback_difficulty, RollbackDifficulty):
            raise RiskValidationError("rollback_difficulty is invalid")
        if self.incident_count is not None and (
            not isinstance(self.incident_count, int) or isinstance(self.incident_count, bool)
        ):
            raise RiskValidationError("incident_count is invalid")
        if self.incident_count is not None and self.incident_count < 0:
            raise RiskValidationError("incident_count is invalid")
        self._bounded_list("protected_paths", self.protected_paths, allow_empty=True)
        self._bounded_list("evidence_refs", self.evidence_refs, allow_empty=True)
        if self.protected_paths and self.level is not RiskLevel.CRITICAL:
            raise RiskValidationError("protected paths require CRITICAL risk")
        if not self.signals or len(self.signals) > _MAX_ITEMS:
            raise RiskValidationError("signals must contain 1 to 200 items")
        signal_ids: set[str] = set()
        highest = RiskLevel.LOW
        for signal in self.signals:
            self._validate_signal(signal, signal_ids)
            if signal.level is SignalLevel.UNKNOWN:
                highest = RiskLevel.CRITICAL
            else:
                highest = max(highest, RiskLevel[signal.level.value])
        if self.level < highest:
            required = "CRITICAL" if highest is RiskLevel.CRITICAL else highest.name
            raise RiskValidationError(f"assessment level is lower than required {required}")
        if (
            self.rollback_difficulty is RollbackDifficulty.UNKNOWN
            and self.level is not RiskLevel.CRITICAL
        ):
            raise RiskValidationError("unknown rollback difficulty requires CRITICAL risk")

    @classmethod
    def _validate_signal(cls, signal: RiskSignal, signal_ids: set[str]) -> None:
        if not isinstance(signal, RiskSignal) or not _SIGNAL_ID.fullmatch(signal.signal_id):
            raise RiskValidationError("signal is invalid")
        if signal.signal_id in signal_ids:
            raise RiskValidationError("signal IDs must be unique")
        signal_ids.add(signal.signal_id)
        for label, value in (("signal category", signal.category), ("signal value", signal.value)):
            cls._bounded_text(label, value)
        if not isinstance(signal.level, SignalLevel):
            raise RiskValidationError("signal level is invalid")
        cls._bounded_list("signal evidence_refs", signal.evidence_refs, allow_empty=True)

    @staticmethod
    def _bounded_text(label: str, value: Any) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_TEXT
            or _SECRET.search(value)
        ):
            raise RiskValidationError(f"{label} is invalid")

    @classmethod
    def _bounded_list(cls, label: str, values: Any, *, allow_empty: bool) -> None:
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_ITEMS:
            raise RiskValidationError(f"{label} is invalid")
        if not allow_empty and not values:
            raise RiskValidationError(f"{label} is invalid")
        for value in values:
            cls._bounded_text(label, value)


def risk_from_dict(payload: dict[str, Any]) -> RiskAssessment:
    """Construct and validate an exact JSON-shaped risk assessment."""
    required = {
        "schema_version", "assessment_id", "project_id", "task_id", "level", "signals",
        "rollback_difficulty", "incident_count", "protected_paths", "evidence_refs",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RiskValidationError("risk schema is missing or contains unknown fields")
    try:
        assessment = RiskAssessment(
            schema_version=payload["schema_version"], assessment_id=payload["assessment_id"],
            project_id=payload["project_id"], task_id=payload["task_id"],
            level=RiskLevel[payload["level"]],
            signals=tuple(
                RiskSignal(
                    signal_id=item["signal_id"], category=item["category"],
                    level=SignalLevel(item["level"]), value=item["value"],
                    evidence_refs=tuple(item["evidence_refs"]),
                ) for item in payload["signals"]
            ),
            rollback_difficulty=RollbackDifficulty(payload["rollback_difficulty"]),
            incident_count=payload["incident_count"],
            protected_paths=tuple(payload["protected_paths"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RiskValidationError(f"invalid risk structure: {exc}") from exc
    assessment.validate()
    return assessment
