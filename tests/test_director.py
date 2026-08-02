from dataclasses import replace

import pytest

from agf_orchestrator.adapters.mock import MockAdapter
from agf_orchestrator.director import Director
from agf_orchestrator.models import PlanStatus, PlanValidationError, RepositoryContext

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


def test_dirty_plan_preserves_state_risk_and_evidence():
    dirty_repository = replace(REPOSITORY, clean=False)
    plan = Director().create_plan("Build a bounded feature", dirty_repository)
    assert plan.repository.clean is False
    assert any("dirty" in risk for risk in plan.risks)
    assert any("uncommitted" in evidence for evidence in plan.required_evidence)


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda plan: replace(plan, tasks=[]), "at least one task"),
        (lambda plan: replace(plan, human_intervention=["clarification"]), "human intervention"),
        (lambda plan: replace(plan, goal="   "), "goal is required"),
        (
            lambda plan: replace(
                plan,
                tasks=[
                    replace(plan.tasks[0], task_id="task-001"),
                    replace(plan.tasks[0], title="Duplicate"),
                ],
            ),
            "task_id values must be unique",
        ),
        (
            lambda plan: replace(plan, tasks=[replace(plan.tasks[0], dependencies=["missing"])]),
            "unknown dependency",
        ),
        (lambda plan: replace(plan, parallel_groups=[["missing"]]), "parallel_groups"),
        (
            lambda plan: replace(plan, tasks=[replace(plan.tasks[0], status="INVALID")]),
            "invalid status",
        ),
    ],
)
def test_invalid_plan_invariants_rejected(mutator, message):
    with pytest.raises(PlanValidationError, match=message):
        mutator(Director().create_plan("Build a bounded feature", REPOSITORY)).validate()
