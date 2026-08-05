from agf_orchestrator.compliance import ComplianceChecker
from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ComplianceStatus, ReviewReport, ReviewStatus


def context():
    task = Task(
        "task-001", "Update file", "Update allowed.txt", ["allowed.txt"], [],
        ["content is after"], ["python -B -c \"assert True\""],
        "low", "Implementer", PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0", "plan-compliance", "1970-01-01T00:00:00Z",
        RepositoryContext("/repo", "feature", "origin", True, "abc"),
        "Update file", {}, [], [], {"status": "approved", "requires_architect": False},
        [task], [], [[task.task_id]], [], [], [], PlanStatus.READY,
    )
    review = ReviewReport("test", ReviewStatus.APPROVE, [], ["review evidence"], [])
    return plan, task, review


def test_compliance_passes_only_with_complete_evidence():
    plan, task, review = context()
    result = ComplianceChecker().check(
        plan, task, review, ["allowed.txt"],
        ["validation: exit_code=0; stdout=; stderr="], ["gate evidence"], True, "abc",
    )
    assert result.status is ComplianceStatus.PASS
    assert any("objective traceability" in item for item in result.evidence)


def test_compliance_fails_rejected_review_and_dirty_caller():
    plan, task, _ = context()
    review = ReviewReport("test", ReviewStatus.REQUEST_CHANGES, [], [], ["scope"])
    result = ComplianceChecker().check(
        plan, task, review, ["allowed.txt", "bad.txt"],
        ["validation: exit_code=1; stdout=; stderr="], [], False, "abc",
    )
    assert result.status is ComplianceStatus.FAIL
    assert len(result.blocking_issues) >= 3
