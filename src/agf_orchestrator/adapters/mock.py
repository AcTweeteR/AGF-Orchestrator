"""Deterministic local adapter used by the MVP and its tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..models import RepositoryContext
from ..target_assessment import (
    _SECRET_NAME,
    _SENSITIVE_DIRS,
    AssessmentError,
    _validate_scoped_path,
)


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
        # The no-provider mock is an inert planning placeholder, NOT an
        # architect. It may only propose one explicitly named, tracked and
        # non-sensitive file already evidenced by this repository. The Owner
        # must still independently approve the exact plan and Objective.
        scoped_path = self._explicit_tracked_path(normalized, repository)
        commands = (
            ["git diff --check"] if scoped_path and "git diff --check" in normalized
            else ["python -m pytest"]
        )
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
                    "allowed_paths": [scoped_path] if scoped_path else [],
                    "dependencies": [],
                    "acceptance_criteria": ["The stated goal is addressed within approved scope."],
                    "validation_commands": commands,
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
    def _explicit_tracked_path(goal: str, repository: RepositoryContext) -> str | None:
        matches = re.findall(
            r"(?:\bpath|\bfile)\s*:\s*([^\s,]+)",
            goal, flags=re.IGNORECASE,
        )
        # Ambiguous or multiple path specifications require a real architect.
        if len(matches) != 1:
            return None
        path = matches[0]
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
            return None
        try:
            _validate_scoped_path(path)
        except AssessmentError:
            return None
        parts = path.split("/")
        if (
            len(path) > 200 or path.startswith(".")
            or any(part in _SENSITIVE_DIRS or _SECRET_NAME.search(part) for part in parts)
        ):
            return None
        root = Path(repository.root)
        candidate = root / path
        if not candidate.is_file() or candidate.is_symlink():
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--error-unmatch", "--", path],
                capture_output=True, shell=False, timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        # --error-unmatch accepts pathspecs. Require the exact single index
        # entry instead of a wildcard or directory prefix match.
        if result.returncode or result.stdout != path.encode("utf-8") + b"\0":
            return None
        return path

    @staticmethod
    def is_ambiguous(goal: str) -> bool:
        generic = {"do it", "do something", "fix it", "improve this", "make it better"}
        return len(goal) < 12 or goal.lower() in generic
