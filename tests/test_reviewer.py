from agf_orchestrator.models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from agf_orchestrator.review_models import ReviewStatus
from agf_orchestrator.reviewer import DeterministicReviewer


def plan_and_task():
    task = Task(
        "task-001", "Update file", "Update allowed.txt", ["allowed.txt"], [],
        ["allowed.txt contains after"], ["python -B -c \"assert True\""],
        "low", "Implementer", PlanStatus.READY,
    )
    plan = ExecutionPlan(
        "1.0", "plan-review", "1970-01-01T00:00:00Z",
        RepositoryContext("/repo", "feature", "origin", True, "abc"),
        "Update file", {"in": ["allowed.txt"]}, [], [],
        {"status": "approved", "requires_architect": False}, [task], [],
        [[task.task_id]], [], [], [], PlanStatus.READY,
    )
    return plan, task


def test_deterministic_reviewer_approves_bounded_validated_patch():
    plan, task = plan_and_task()
    report = DeterministicReviewer().review(
        plan, task, ["allowed.txt"], "@@ -1 +1 @@\n-before\n+after\n",
        ["validation python: exit_code=0; stdout=; stderr="],
    )
    assert report.status is ReviewStatus.APPROVE
    assert report.findings == []


def test_deterministic_reviewer_requests_changes_for_scope_or_validation():
    plan, task = plan_and_task()
    report = DeterministicReviewer().review(
        plan, task, ["allowed.txt", "secret.txt"], "patch",
        ["validation false: exit_code=1; stdout=; stderr="],
    )
    assert report.status is ReviewStatus.REQUEST_CHANGES
    assert {finding.code for finding in report.findings} == {"SCOPE", "VALIDATION"}
