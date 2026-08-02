"""Provider-neutral adapter contract for plan generation."""

from __future__ import annotations

from typing import Protocol

from ..models import RepositoryContext


class DirectorAdapter(Protocol):
    """Interface consumed by Director; providers must not change the Director contract."""

    def build_plan_inputs(self, goal: str, repository: RepositoryContext) -> dict:
        """Return deterministic, structured plan inputs for a goal."""
