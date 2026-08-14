"""Deterministic aggregation of evidence-based risk signals."""

from __future__ import annotations

from .risk_models import (
    RiskAssessment,
    RiskLevel,
    RiskSignal,
    RollbackDifficulty,
    SignalLevel,
)


class RiskEngineError(ValueError):
    """Raised when risk facts are incomplete or unbounded."""


def assess_risk(
    *,
    assessment_id: str,
    project_id: str,
    task_id: str,
    changed_paths: tuple[str, ...],
    protected_paths: tuple[str, ...],
    rollback_difficulty: RollbackDifficulty,
    incident_count: int | None,
    reviewer_blockers: int,
    validation_passed: bool,
    evidence_refs: tuple[str, ...],
) -> RiskAssessment:
    """Produce a reproducible assessment from bounded non-model facts."""
    if not changed_paths or len(changed_paths) > 200:
        raise RiskEngineError("changed_paths are invalid")
    if any(not isinstance(path, str) or not path.strip() for path in changed_paths):
        raise RiskEngineError("changed_paths are invalid")
    if incident_count is not None and (
        not isinstance(incident_count, int)
        or isinstance(incident_count, bool)
        or incident_count < 0
    ):
        raise RiskEngineError("incident_count is invalid")
    if (
        not isinstance(reviewer_blockers, int)
        or isinstance(reviewer_blockers, bool)
        or reviewer_blockers < 0
    ):
        raise RiskEngineError("reviewer_blockers is invalid")
    if not isinstance(validation_passed, bool):
        raise RiskEngineError("validation_passed is invalid")
    signals = [
        RiskSignal(
            "signal-change-size", "CHANGE_SIZE", _change_level(len(changed_paths)),
            f"changed_paths={len(changed_paths)}", evidence_refs,
        ),
        RiskSignal(
            "signal-protected-path", "PROTECTED_PATH",
            SignalLevel.HIGH if protected_paths else SignalLevel.LOW,
            f"protected_paths={len(protected_paths)}", evidence_refs,
        ),
        RiskSignal(
            "signal-rollback", "ROLLBACK_DIFFICULTY", _rollback_level(rollback_difficulty),
            f"rollback={rollback_difficulty.value}", evidence_refs,
        ),
        RiskSignal(
            "signal-incidents", "INCIDENT_HISTORY",
            SignalLevel.UNKNOWN if incident_count is None else (
                SignalLevel.HIGH if incident_count else SignalLevel.LOW
            ),
            f"incidents={incident_count if incident_count is not None else 'UNKNOWN'}",
            evidence_refs,
        ),
        RiskSignal(
            "signal-review", "REVIEW_FINDINGS",
            SignalLevel.HIGH if reviewer_blockers else SignalLevel.LOW,
            f"reviewer_blockers={reviewer_blockers}", evidence_refs,
        ),
        RiskSignal(
            "signal-validation", "VALIDATION",
            SignalLevel.LOW if validation_passed else SignalLevel.HIGH,
            f"validation_passed={validation_passed}", evidence_refs,
        ),
    ]
    level = _aggregate_level(signals)
    if protected_paths:
        level = RiskLevel.CRITICAL
    assessment = RiskAssessment(
        "1.0", assessment_id, project_id, task_id, level, tuple(signals),
        rollback_difficulty, incident_count, protected_paths, evidence_refs,
    )
    assessment.validate()
    return assessment


def _change_level(count: int) -> SignalLevel:
    if count <= 3:
        return SignalLevel.LOW
    if count <= 10:
        return SignalLevel.MEDIUM
    return SignalLevel.HIGH


def _rollback_level(value: RollbackDifficulty) -> SignalLevel:
    return {
        RollbackDifficulty.EASY: SignalLevel.LOW,
        RollbackDifficulty.MODERATE: SignalLevel.MEDIUM,
        RollbackDifficulty.HARD: SignalLevel.HIGH,
        RollbackDifficulty.UNKNOWN: SignalLevel.UNKNOWN,
    }[value]


def _aggregate_level(signals: list[RiskSignal]) -> RiskLevel:
    level = RiskLevel.LOW
    for signal in signals:
        if signal.level is SignalLevel.UNKNOWN:
            return RiskLevel.CRITICAL
        level = max(level, RiskLevel[signal.level.value])
    return level


def risk_evidence(assessment: RiskAssessment) -> str:
    """Return bounded review evidence without copying signal values."""
    assessment.validate()
    return (
        f"risk assessment: id={assessment.assessment_id}; level={assessment.level.name}; "
        f"signal_count={len(assessment.signals)}"
    )
