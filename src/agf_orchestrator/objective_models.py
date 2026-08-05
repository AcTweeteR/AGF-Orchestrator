"""Immutable schema and validation for owner-defined project objectives."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
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
_AMBIGUOUS_LANGUAGE = re.compile(
    r"(?i)\b(as appropriate|as soon as possible|best possible|etc\.?|reasonable|soon)\b"
)
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


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def normalize_objective(objective: Objective) -> Objective:
    """Return a new canonical objective without mutating the source object."""
    objective.validate()
    normalized = Objective(
        schema_version=objective.schema_version,
        objective_id=objective.objective_id,
        title=_normalize_text(objective.title),
        statement=_normalize_text(objective.statement),
        requirements=tuple(
            sorted(
                (
                    ObjectiveRequirement(
                        requirement_id=item.requirement_id,
                        statement=_normalize_text(item.statement),
                        mandatory=item.mandatory,
                        acceptance_criteria=tuple(
                            sorted(_normalize_text(value) for value in item.acceptance_criteria)
                        ),
                    )
                    for item in objective.requirements
                ),
                key=lambda item: item.requirement_id,
            )
        ),
        constraints=tuple(sorted(_normalize_text(value) for value in objective.constraints)),
        prohibited_outcomes=tuple(
            sorted(_normalize_text(value) for value in objective.prohibited_outcomes)
        ),
        completion_criteria=tuple(
            sorted(_normalize_text(value) for value in objective.completion_criteria)
        ),
        owner_namespace=_normalize_text(objective.owner_namespace),
        status=objective.status,
    )
    normalized.validate()
    return normalized


def canonical_objective_json(objective: Objective) -> bytes:
    """Serialize the normalized objective for hashing or evidence."""
    normalized = normalize_objective(objective)
    try:
        return json.dumps(
            normalized.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ObjectiveValidationError("objective cannot be canonically serialized") from exc


def objective_hash(objective: Objective) -> str:
    """Return the deterministic SHA-256 hash of the canonical objective."""
    return hashlib.sha256(canonical_objective_json(objective)).hexdigest()


class ObjectiveGateStatus(StrEnum):
    READY = "READY"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class ObjectiveAnalysis:
    status: ObjectiveGateStatus
    contradictions: tuple[str, ...]
    ambiguities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "contradictions": list(self.contradictions),
            "ambiguities": list(self.ambiguities),
        }


@dataclass(frozen=True)
class AmendmentProposal:
    proposal_id: str
    base_objective_hash: str
    requested_changes: tuple[str, ...]
    reason: str
    status: str = "PROPOSED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "base_objective_hash": self.base_objective_hash,
            "requested_changes": list(self.requested_changes),
            "reason": self.reason,
            "status": self.status,
        }


def analyze_objective(objective: Objective) -> ObjectiveAnalysis:
    """Detect bounded contradictions and ambiguities without making decisions."""
    normalized = normalize_objective(objective)
    prohibited = set(normalized.prohibited_outcomes)
    contradictions = tuple(
        f"requirement {item.requirement_id} conflicts with a prohibited outcome"
        for item in normalized.requirements
        if item.statement in prohibited
    )
    ambiguities = tuple(
        sorted(
            {
                f"ambiguous language in {label}"
                for label, values in (
                    ("objective statement", (normalized.statement,)),
                    ("requirement", tuple(item.statement for item in normalized.requirements)),
                    ("constraint", normalized.constraints),
                    ("completion criterion", normalized.completion_criteria),
                )
                for value in values
                if _AMBIGUOUS_LANGUAGE.search(value)
            }
        )
    )
    status = (
        ObjectiveGateStatus.HUMAN_REQUIRED
        if contradictions or ambiguities
        else ObjectiveGateStatus.READY
    )
    return ObjectiveAnalysis(status, contradictions, ambiguities)


def propose_amendment(
    objective: Objective, requested_changes: tuple[str, ...], reason: str
) -> AmendmentProposal:
    """Create a deterministic inert amendment proposal; never approves it."""
    normalized = normalize_objective(objective)
    if not requested_changes or any(
        not isinstance(item, str) or not item.strip() for item in requested_changes
    ):
        raise ObjectiveValidationError("requested_changes must be non-empty strings")
    Objective._bounded_list("requested_changes", requested_changes)
    Objective._bounded_text("amendment reason", reason)
    base_hash = objective_hash(normalized)
    proposal_input = json.dumps(
        {
            "base_objective_hash": base_hash,
            "reason": _normalize_text(reason),
            "requested_changes": sorted(_normalize_text(item) for item in requested_changes),
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    proposal_id = "amendment-" + hashlib.sha256(proposal_input).hexdigest()[:16]
    return AmendmentProposal(
        proposal_id=proposal_id,
        base_objective_hash=base_hash,
        requested_changes=tuple(sorted(_normalize_text(item) for item in requested_changes)),
        reason=_normalize_text(reason),
    )
