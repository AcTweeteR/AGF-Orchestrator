from agf_orchestrator.models import (
    ExecutionPlan,
    PlanStatus,
    PlanValidationError,
    RepositoryContext,
    Task,
    plan_from_dict,
)


def make_plan() -> ExecutionPlan:
    return ExecutionPlan(
        schema_version="1.0",
        plan_id="plan-test",
        created_at="1970-01-01T00:00:00Z",
        repository=RepositoryContext(
            "/repo", "main", "https://example.invalid/repo.git", True, "abc123"
        ),
        goal="Build a bounded feature",
        scope={"in": ["feature"], "out": ["unrelated work"]},
        assumptions=[],
        risks=[],
        architecture_impact={"status": "to_be_assessed"},
        tasks=[
            Task(
                "task-001",
                "Feature",
                "Build a bounded feature",
                [],
                [],
                ["works"],
                ["pytest"],
                "low",
                "Implementer",
                PlanStatus.READY,
            )
        ],
        dependencies=[],
        parallel_groups=[["task-001"]],
        required_reviews=["Reviewer"],
        required_evidence=["test result"],
        human_intervention=[],
        status=PlanStatus.READY,
    )


def test_valid_plan_serialization_round_trip():
    plan = make_plan()
    plan.validate()
    restored = plan_from_dict(plan.to_dict())
    assert restored.to_dict() == plan.to_dict()


def test_invalid_plan_missing_fields_rejected():
    payload = make_plan().to_dict()
    del payload["tasks"]
    try:
        plan_from_dict(payload)
    except PlanValidationError as exc:
        assert "tasks" in str(exc)
    else:
        raise AssertionError("invalid plan was accepted")
