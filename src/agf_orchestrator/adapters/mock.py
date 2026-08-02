"""Deterministic local adapter used by the MVP and its tests."""

from __future__ import annotations

import re

from ..models import RepositoryContext


class MockAdapter:
    """Create a stable plan draft without network access or model calls."""

    def build_plan_inputs(self, goal: str, repository: RepositoryContext) -> dict:
        normalized = " ".join(goal.split())
        ambiguous = self.is_ambiguous(normalized)
        if ambiguous:
            return {
                "scope": {"in": [], "out": ["implementation without human clarification"]},
                "assumptions": [],
                "risks": ["The goal is ambiguous and cannot be safely decomposed."],
                "architecture_impact": {"status": "unknown", "requires_architect": True},
                "tasks": [],
                "dependencies": [],
                "parallel_groups": [],
                "required_reviews": [],
                "required_evidence": ["clarified goal", "human decision record"],
                "human_intervention": ["Clarify objective, scope, and success condition."],
                "status": "HUMAN_REQUIRED",
            }
        slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:40]
        risks = ["The task requires independent review before release."]
        required_evidence = ["task outcome", "validation results", "review report"]
        if not repository.clean:
            risks.append("The plan was created from a dirty working tree.")
            required_evidence.append("uncommitted working-tree status captured at preflight")
        return {
            "scope": {"in": [normalized], "out": ["unrequested scope expansion"]},
            "assumptions": ["The goal is bounded by the supplied repository and policy context."],
            "risks": risks,
            "architecture_impact": {"status": "to_be_assessed", "requires_architect": True},
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": f"Plan outcome: {normalized}",
                    "objective": normalized,
                    "allowed_paths": [],
                    "dependencies": [],
                    "acceptance_criteria": ["The stated goal is addressed within approved scope."],
                    "validation_commands": ["python -m pytest"],
                    "risk_level": "medium",
                    "assigned_role": "Implementer",
                    "status": "READY",
                }
            ],
            "dependencies": [],
            "parallel_groups": [["task-001"]],
            "required_reviews": ["Reviewer", "Compliance Officer"],
            "required_evidence": required_evidence,
            "human_intervention": [],
            "status": "READY",
            "slug": slug,
            "repository": repository.root,
        }

    @staticmethod
    def is_ambiguous(goal: str) -> bool:
        generic = {"do it", "do something", "fix it", "improve this", "make it better"}
        return len(goal) < 12 or goal.lower() in generic
