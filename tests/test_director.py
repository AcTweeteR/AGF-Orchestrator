from agf_orchestrator.adapters.mock import MockAdapter
from agf_orchestrator.director import Director
from agf_orchestrator.models import PlanStatus, RepositoryContext

REPOSITORY = RepositoryContext("/repo", "main", "https://example.invalid/repo.git", True, "abc123")


def test_mock_adapter_is_deterministic():
    adapter = MockAdapter()
    first = adapter.build_plan_inputs("Build a bounded feature", REPOSITORY)
    second = adapter.build_plan_inputs("Build a bounded feature", REPOSITORY)
    assert first == second


def test_director_returns_valid_ready_plan():
    plan = Director().create_plan("Build a bounded feature", REPOSITORY)
    assert plan.status is PlanStatus.READY
    assert plan.tasks[0].assigned_role == "Implementer"
    plan.validate()


def test_ambiguous_goal_requires_human():
    plan = Director().create_plan("fix it", REPOSITORY)
    assert plan.status is PlanStatus.HUMAN_REQUIRED
    assert plan.human_intervention
