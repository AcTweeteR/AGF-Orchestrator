"""Immutable schema and validation for owner-defined project objectives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ObjectiveValidationError(ValueError):
    """Raised when an objective does not satisfy the bounded schema."""


class ObjectiveStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    AMENDMENT_PROPOSED = "AMENDMENT_PROPOSED"


_OBJECTIVE_ID = re.compile(r"^objective-[a-z0-9][a-z0-9-]{0,79}$")
_REQUIREMENT_ID = re.compile(r"^requirement-[a-z0-9][a-z0-9-]{0,79}$")
_SECRET_SHAPED = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")
_MAX_TEXT = 4000
_MAX_ITEMS = 200


@dataclass(frozen=True)
class ObjectiveRequirement:
    requirement_id: str
    statement: str
    mandatory: bool
    acceptance_criteria: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "statement": self.statement,
            "mandatory": self.mandatory,
            "acceptance_criteria": list(self.acceptance_criteria),
        }


@dataclass(frozen=True)
class Objective:
    schema_version: str
    objective_id: str
    title: str
    statement: str
    requirements: tuple[ObjectiveRequirement, ...]
    constraints: tuple[str, ...]
    prohibited_outcomes: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    owner_namespace: str
    status: ObjectiveStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "objective_id": self.objective_id,
            "title": self.title,
            "statement": self.statement,
            "requirements": [item.to_dict() for item in self.requirements],
            "constraints": list(self.constraints),
            "prohibited_outcomes": list(self.prohibited_outcomes),
            "completion_criteria": list(self.completion_criteria),
            "owner_namespace": self.owner_namespace,
            "status": self.status.value,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ObjectiveValidationError("schema_version must be 1.0")
        if not _OBJECTIVE_ID.fullmatch(self.objective_id):
            raise ObjectiveValidationError("objective_id is invalid")
        for field_name, value in (
            ("title", self.title),
            ("statement", self.statement),
            ("owner_namespace", self.owner_namespace),
        ):
            self._bounded_text(field_name, value)
        if not isinstance(self.status, ObjectiveStatus):
            raise ObjectiveValidationError("status is invalid")
        self._bounded_list("constraints", self.constraints, allow_empty=True)
        self._bounded_list("prohibited_outcomes", self.prohibited_outcomes, allow_empty=True)
        self._bounded_list("completion_criteria", self.completion_criteria)
        if not self.requirements or len(self.requirements) > _MAX_ITEMS:
            raise ObjectiveValidationError("requirements must contain 1 to 200 items")
        ids = []
        for requirement in self.requirements:
            if not isinstance(requirement, ObjectiveRequirement):
                raise ObjectiveValidationError("requirements contain an invalid item")
            if not _REQUIREMENT_ID.fullmatch(requirement.requirement_id):
                raise ObjectiveValidationError("requirement_id is invalid")
            if requirement.requirement_id in ids:
                raise ObjectiveValidationError("requirement_id values must be unique")
            ids.append(requirement.requirement_id)
            self._bounded_text("requirement statement", requirement.statement)
            if not isinstance(requirement.mandatory, bool):
                raise ObjectiveValidationError("requirement mandatory must be boolean")
            self._bounded_list(
                "requirement acceptance_criteria", requirement.acceptance_criteria
            )

    @staticmethod
    def _bounded_text(label: str, value: Any) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
            raise ObjectiveValidationError(f"{label} must be a bounded non-empty string")
        if _SECRET_SHAPED.search(value):
            raise ObjectiveValidationError(f"{label} contains secret-shaped data")

    @classmethod
    def _bounded_list(cls, label: str, values: Any, *, allow_empty: bool = False) -> None:
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_ITEMS:
            raise ObjectiveValidationError(f"{label} must be a bounded list")
        if not allow_empty and not values:
            raise ObjectiveValidationError(f"{label} must not be empty")
        for value in values:
            cls._bounded_text(label, value)


def objective_from_dict(payload: dict[str, Any]) -> Objective:
    """Construct and validate an objective from an exact JSON-shaped mapping."""
    required = {
        "schema_version",
        "objective_id",
        "title",
        "statement",
        "requirements",
        "constraints",
        "prohibited_outcomes",
        "completion_criteria",
        "owner_namespace",
        "status",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ObjectiveValidationError("objective schema is missing or contains unknown fields")
    try:
        requirements = tuple(
            ObjectiveRequirement(
                requirement_id=item["requirement_id"],
                statement=item["statement"],
                mandatory=item["mandatory"],
                acceptance_criteria=tuple(item["acceptance_criteria"]),
            )
            for item in payload["requirements"]
        )
        objective = Objective(
            schema_version=payload["schema_version"],
            objective_id=payload["objective_id"],
            title=payload["title"],
            statement=payload["statement"],
            requirements=requirements,
            constraints=tuple(payload["constraints"]),
            prohibited_outcomes=tuple(payload["prohibited_outcomes"]),
            completion_criteria=tuple(payload["completion_criteria"]),
            owner_namespace=payload["owner_namespace"],
            status=ObjectiveStatus(payload["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ObjectiveValidationError(f"invalid objective structure: {exc}") from exc
    objective.validate()
    return objective
