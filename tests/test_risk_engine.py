import pytest

from agf_orchestrator.risk_engine import RiskEngineError, assess_risk
from agf_orchestrator.risk_models import RiskLevel, RollbackDifficulty

FACTS = {
    "assessment_id": "risk-task-001",
    "project_id": "project-agf-orchestrator",
    "task_id": "task-001",
    "changed_paths": ("allowed.txt",),
    "protected_paths": (),
    "rollback_difficulty": RollbackDifficulty.EASY,
    "incident_count": 0,
    "reviewer_blockers": 0,
    "validation_passed": True,
    "evidence_refs": ("evidence-001",),
}


def test_risk_engine_is_deterministic_for_low_facts():
    first = assess_risk(**FACTS)
    second = assess_risk(**FACTS)

    assert first == second
    assert first.level is RiskLevel.LOW


def test_risk_engine_escalates_protected_paths_and_review_blockers():
    assessment = assess_risk(
        **{
            **FACTS,
            "protected_paths": ("src/agf_orchestrator/constitution.py",),
            "reviewer_blockers": 1,
        }
    )
    assert assessment.level is RiskLevel.CRITICAL


def test_risk_engine_treats_unknown_rollback_conservatively():
    assessment = assess_risk(
        **{**FACTS, "rollback_difficulty": RollbackDifficulty.UNKNOWN}
    )
    assert assessment.level is RiskLevel.CRITICAL


def test_failed_validation_and_unbounded_facts_are_rejected():
    assessment = assess_risk(**{**FACTS, "validation_passed": False})
    assert assessment.level is RiskLevel.HIGH
    with pytest.raises(RiskEngineError, match="changed_paths"):
        assess_risk(**{**FACTS, "changed_paths": ()})
