"""Director orchestration for deterministic execution-plan generation."""

from __future__ import annotations

import hashlib
import json

from .adapters.base import DirectorAdapter
from .adapters.mock import MockAdapter
from .engineering_memory_evidence import MemoryEvidenceError, validate_query_evidence
from .models import ExecutionPlan, PlanStatus, RepositoryContext, Task
from .target_assessment import (
    ArchitectureDecision,
    TargetAssessment,
    architecture_to_tasks,
)

DETERMINISTIC_CREATED_AT = "1970-01-01T00:00:00Z"


class Director:
    """Turn a goal and preflight context into a validated machine-readable plan."""

    def __init__(self, adapter: DirectorAdapter | None = None) -> None:
        self.adapter = adapter or MockAdapter()

    def create_plan(
        self, goal: str, repository: RepositoryContext, *, memory_evidence: str | None = None
    ) -> ExecutionPlan:
        draft = self.adapter.build_plan_inputs(goal, repository)
        plan_id = self._plan_id(goal, repository)
        tasks = [Task(**{**item, "status": PlanStatus(item["status"])}) for item in draft["tasks"]]
        risks = list(draft["risks"])
        required_evidence = list(draft["required_evidence"])
        if memory_evidence is not None:
            try:
                validate_query_evidence(memory_evidence)
            except MemoryEvidenceError as exc:
                raise ValueError(str(exc)) from exc
            required_evidence.append(memory_evidence)
        if not repository.clean:
            dirty_risk = "The plan was created from a dirty working tree."
            dirty_evidence = "uncommitted working-tree status captured at preflight"
            if dirty_risk not in risks:
                risks.append(dirty_risk)
            if dirty_evidence not in required_evidence:
                required_evidence.append(dirty_evidence)
        plan = ExecutionPlan(
            schema_version="1.0",
            plan_id=plan_id,
            created_at=DETERMINISTIC_CREATED_AT,
            repository=repository,
            goal=" ".join(goal.split()),
            scope=draft["scope"],
            assumptions=draft["assumptions"],
            risks=risks,
            architecture_impact=draft["architecture_impact"],
            tasks=tasks,
            dependencies=draft["dependencies"],
            parallel_groups=draft["parallel_groups"],
            required_reviews=draft["required_reviews"],
            required_evidence=required_evidence,
            human_intervention=draft["human_intervention"],
            status=PlanStatus(draft["status"]),
        )
        plan.validate()
        return plan

    def create_assessed_plan(
        self,
        goal: str,
        repository: RepositoryContext,
        assessment: TargetAssessment,
        architecture: ArchitectureDecision,
        *,
        lineage: str | None = None,
        lineage_hash: str | None = None,
    ) -> ExecutionPlan:
        """Create a plan only from validated assessment/architecture evidence."""
        assessment.validate(repository)
        architecture.validate(assessment)
        tasks = architecture_to_tasks(architecture) if architecture.status == "approved" else []
        plan_status = PlanStatus.READY if architecture.status == "approved" else PlanStatus.BLOCKED
        scope = {
            "in": [architecture.bounded_objective],
            "out": list(architecture.prohibited_paths),
            "assessment_hash": assessment.evidence_hash,
            "architecture_hash": hashlib.sha256(
                json.dumps(
                    architecture.to_dict(), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "delivery_branch": architecture.delivery_branch,
            "lineage": lineage,
            "predecessor_plan_sha256": lineage_hash,
        }
        plan = ExecutionPlan(
            schema_version="1.0",
            plan_id=self._assessed_plan_id(goal, repository, assessment, architecture),
            created_at=DETERMINISTIC_CREATED_AT,
            repository=repository,
            goal=" ".join(goal.split()),
            scope=scope,
            assumptions=["Assessment and architecture evidence are bound to the baseline SHA."],
            risks=list(architecture.risk_indicators),
            architecture_impact={
                "status": "approved" if architecture.status == "approved" else "blocked",
                "requires_architect": False if architecture.status == "approved" else True,
                "assessment_hash": assessment.evidence_hash,
                "provider_selection": architecture.provider_selection,
                "planning_outcome": architecture.planning_outcome,
            },
            tasks=tasks,
            dependencies=[],
            parallel_groups=[[task.task_id for task in tasks]] if tasks else [],
            required_reviews=["Reviewer", "Compliance Officer"],
            required_evidence=list(architecture.required_evidence),
            human_intervention=[] if plan_status is PlanStatus.READY else [architecture.rationale],
            status=plan_status,
        )
        plan.validate()
        return plan

    @staticmethod
    def _assessed_plan_id(goal, repository, assessment, architecture):
        source = json.dumps({
            "goal": " ".join(goal.split()), "root": repository.root,
            "head": repository.head_sha, "assessment": assessment.evidence_hash,
            "branch": architecture.delivery_branch,
        }, sort_keys=True).encode()
        return f"plan-{hashlib.sha256(source).hexdigest()[:16]}"

    @staticmethod
    def _plan_id(goal: str, repository: RepositoryContext) -> str:
        source = json.dumps(
            {"goal": " ".join(goal.split()), "root": repository.root, "head": repository.head_sha},
            sort_keys=True,
        ).encode()
        return f"plan-{hashlib.sha256(source).hexdigest()[:16]}"
