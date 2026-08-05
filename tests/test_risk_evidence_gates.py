from agf_orchestrator.compliance import ComplianceChecker
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ComplianceStatus, ReviewStatus
from agf_orchestrator.reviewer import DeterministicReviewer
from agf_orchestrator.risk_engine import assess_risk, risk_evidence
from agf_orchestrator.risk_models import RollbackDifficulty


def context():
    task = Task(
        "task-001", "Update file", "Update allowed.txt", ["allowed.txt"], [],
        ["content is after"], ["python -B -c \"assert True\""], "low", "Implementer",
        PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0", "plan-risk", "1970-01-01T00:00:00Z",
        RepositoryContext("/repo", "feature", "https://github.com/example/repo.git", True, "abc"),
        "Update file", {}, [], [], {"status": "approved", "requires_architect": False},
        [task], [], [[task.task_id]], ["Reviewer"], ["risk", "validation"], [], PlanStatus.READY,
    )
    return plan, task


def assessment():
    return assess_risk(
        assessment_id="risk-task-001", project_id="project-agf-orchestrator", task_id="task-001",
        changed_paths=("allowed.txt",), protected_paths=(),
        rollback_difficulty=RollbackDifficulty.EASY, incident_count=0, reviewer_blockers=0,
        validation_passed=True, evidence_refs=("evidence-risk",),
    )


def test_reviewer_and_compliance_receive_the_same_bounded_risk_evidence():
    plan, task = context()
    risk = assessment()
    summary = risk_evidence(risk)
    review = DeterministicReviewer().review(
        plan, task, ["allowed.txt"], "patch", ["validation: exit_code=0"],
        risk_assessment=risk,
    )
    compliance = ComplianceChecker().check(
        plan, task, review, ["allowed.txt"], ["validation: exit_code=0"],
        [summary], True, "abc", ["allowed.txt"], risk_assessment=risk,
    )

    assert review.status is ReviewStatus.APPROVE
    assert summary in review.evidence
    assert compliance.status is ComplianceStatus.PASS


def test_compliance_blocks_missing_risk_evidence():
    plan, task = context()
    risk = assessment()
    review = DeterministicReviewer().review(
        plan, task, ["allowed.txt"], "patch", ["validation: exit_code=0"],
        risk_assessment=risk,
    )
    result = ComplianceChecker().check(
        plan, task, review, ["allowed.txt"], ["validation: exit_code=0"],
        ["other evidence"], True, "abc", ["allowed.txt"], risk_assessment=risk,
    )
    assert result.status is ComplianceStatus.FAIL
    assert "risk assessment evidence is missing" in result.blocking_issues
