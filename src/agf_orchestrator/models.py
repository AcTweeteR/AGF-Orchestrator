"""Typed models and validation for Director execution plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class PlanStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class PlanValidationError(ValueError):
    """Raised when a plan does not satisfy the Director schema."""


@dataclass(frozen=True)
class RepositoryContext:
    root: str
    branch: str
    origin: str | None
    clean: bool
    head_sha: str


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    objective: str
    allowed_paths: list[str]
    dependencies: list[str]
    acceptance_criteria: list[str]
    validation_commands: list[str]
    risk_level: str
    assigned_role: str
    status: PlanStatus


@dataclass(frozen=True)
class ExecutionPlan:
    schema_version: str
    plan_id: str
    created_at: str
    repository: RepositoryContext
    goal: str
    scope: dict[str, Any]
    assumptions: list[str]
    risks: list[str]
    architecture_impact: dict[str, Any]
    tasks: list[Task]
    dependencies: list[dict[str, Any]]
    parallel_groups: list[list[str]]
    required_reviews: list[str]
    required_evidence: list[str]
    human_intervention: list[str]
    status: PlanStatus

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        """Validate schema, references, and task dependency safety."""
        if self.schema_version != "1.0":
            raise PlanValidationError("schema_version must be 1.0")
        required_strings = {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "goal": self.goal,
        }
        for name, value in required_strings.items():
            if not isinstance(value, str) or not value.strip():
                raise PlanValidationError(f"{name} is required")
        if self.status not in PlanStatus:
            raise PlanValidationError("status is invalid")
        if not self.repository.root or not self.repository.head_sha:
            raise PlanValidationError("repository context is incomplete")
        if not isinstance(self.scope, dict) or not isinstance(self.architecture_impact, dict):
            raise PlanValidationError("scope and architecture_impact must be objects")

        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise PlanValidationError("task_id values must be unique")
        task_id_set = set(task_ids)
        for task in self.tasks:
            self._validate_task(task, task_id_set)
        for group in self.parallel_groups:
            if not group or not set(group).issubset(task_id_set):
                raise PlanValidationError("parallel_groups reference unknown or empty tasks")
        for dependency in self.dependencies:
            if set(dependency) != {"task_id", "depends_on"}:
                raise PlanValidationError("dependencies must contain task_id and depends_on")
            if (
                dependency["task_id"] not in task_id_set
                or dependency["depends_on"] not in task_id_set
            ):
                raise PlanValidationError("dependencies reference unknown tasks")
        self._validate_acyclic(task_ids)

    def _validate_task(self, task: Task, task_ids: set[str]) -> None:
        required = {
            "task_id": task.task_id,
            "title": task.title,
            "objective": task.objective,
            "risk_level": task.risk_level,
            "assigned_role": task.assigned_role,
        }
        if any(not isinstance(value, str) or not value.strip() for value in required.values()):
            raise PlanValidationError("task required fields are incomplete")
        if task.assigned_role != "Implementer":
            raise PlanValidationError("tasks must be assigned to Implementer")
        if task.status not in PlanStatus:
            raise PlanValidationError(f"invalid status for task {task.task_id}")
        if not task.acceptance_criteria or not task.validation_commands:
            raise PlanValidationError(f"task {task.task_id} needs criteria and validation")
        if not set(task.dependencies).issubset(task_ids):
            raise PlanValidationError(f"task {task.task_id} has an unknown dependency")

    def _validate_acyclic(self, task_ids: list[str]) -> None:
        edges = {task_id: set() for task_id in task_ids}
        for task in self.tasks:
            edges[task.task_id].update(task.dependencies)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise PlanValidationError("task dependencies contain a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in sorted(edges[task_id]):
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)


def plan_from_dict(payload: dict[str, Any]) -> ExecutionPlan:
    """Build and validate a plan from a JSON-compatible mapping."""
    required = {
        "schema_version", "plan_id", "created_at", "repository", "goal", "scope",
        "assumptions", "risks", "architecture_impact", "tasks", "dependencies",
        "parallel_groups", "required_reviews", "required_evidence", "human_intervention",
        "status",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise PlanValidationError(f"missing plan fields: {', '.join(missing)}")
    repository = RepositoryContext(**payload["repository"])
    tasks = [Task(**{**item, "status": PlanStatus(item["status"])}) for item in payload["tasks"]]
    plan = ExecutionPlan(
        schema_version=payload["schema_version"],
        plan_id=payload["plan_id"],
        created_at=payload["created_at"],
        repository=repository,
        goal=payload["goal"],
        scope=payload["scope"],
        assumptions=payload["assumptions"],
        risks=payload["risks"],
        architecture_impact=payload["architecture_impact"],
        tasks=tasks,
        dependencies=payload["dependencies"],
        parallel_groups=payload["parallel_groups"],
        required_reviews=payload["required_reviews"],
        required_evidence=payload["required_evidence"],
        human_intervention=payload["human_intervention"],
        status=PlanStatus(payload["status"]),
    )
    plan.validate()
    return plan
