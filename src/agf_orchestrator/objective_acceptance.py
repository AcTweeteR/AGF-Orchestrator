"""Public-only reading of Objective acceptance in the existing authority bundle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .authority_context import AuthorityContextError, resolve_authority
from .objective_models import (
    Objective,
    ObjectiveGateStatus,
    ObjectiveStatus,
    analyze_objective,
    normalize_objective,
    objective_from_dict,
    objective_hash,
)
from .owner_authority import canonical_bytes
from .remote_identity import canonical_remote_identity


class ObjectiveAcceptanceError(ValueError):
    pass


def content_hash(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def criterion_statements(objective: Objective) -> dict[str, str]:
    """Enumerate normalized mandatory criteria without dropping constraints."""
    objective = normalize_objective(objective)
    statements = {
        f"requirement:{item.requirement_id}:{index}": text
        for item in objective.requirements if item.mandatory
        for index, text in enumerate(item.acceptance_criteria)
    }
    for prefix, items in (
        ("completion", objective.completion_criteria),
        ("constraint", objective.constraints),
        ("prohibited", objective.prohibited_outcomes),
    ):
        statements.update({f"{prefix}:{index}": text for index, text in enumerate(items)})
    return statements


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    statement_sha256: str
    method: str
    task_ids: tuple[str, ...]
    validation_commands: tuple[str, ...]


@dataclass(frozen=True)
class ObjectiveAcceptance:
    objective: Objective
    objective_sha256: str
    component_sha256: str
    generation_id: str
    generation_hash: str
    criteria: tuple[AcceptanceCriterion, ...]
    policy_hash: str
    constitution_id: str
    approved_plan_sha256: str | None = None


def read_objective_acceptance(project, session) -> ObjectiveAcceptance:
    """Resolve only the installed owner generation, never a caller artifact."""
    try:
        runtime = resolve_authority(project.project_id)
        context = runtime.context
        if (context is None or context.scheme != "Ed25519"
                or runtime.constitution is None or runtime.policy is None):
            raise ObjectiveAcceptanceError("authenticated Objective generation is not installed")
        return _from_context(context, project, session)
    except (AuthorityContextError, KeyError, TypeError, ValueError) as exc:
        raise ObjectiveAcceptanceError("Objective acceptance authority is unavailable") from exc


def parse_objective_proposal(payload, project, session, generation_id):
    """Validate content only; this function does not authenticate owner approval."""
    required = {
        "schema_version", "generation_id", "project_id", "session_id",
        "repository_identity", "goal_sha256", "objective", "objective_sha256", "criteria",
    }
    if isinstance(payload, dict) and payload.get("schema_version") == "2.0":
        required.add("approved_plan_sha256")
        digest = payload.get("approved_plan_sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ObjectiveAcceptanceError("approved plan hash is invalid")
    if not isinstance(payload, dict) or set(payload) != required:
        raise ObjectiveAcceptanceError("Objective component schema is invalid")
    if (payload["schema_version"] not in {"1.0", "2.0"}
            or payload["generation_id"] != generation_id
            or payload["project_id"] != project.project_id
            or payload["session_id"] != session.session_id
            or session.project_id != project.project_id
            or payload["goal_sha256"] != content_hash(" ".join(session.goal.split()))
            or payload["repository_identity"] != canonical_remote_identity(project.origin_url)):
        raise ObjectiveAcceptanceError("Objective component binding differs")
    objective = normalize_objective(objective_from_dict(payload["objective"]))
    if (objective.status is not ObjectiveStatus.APPROVED
            or payload["objective_sha256"] != objective_hash(objective)
            or analyze_objective(objective).status is not ObjectiveGateStatus.READY):
        raise ObjectiveAcceptanceError("Objective approval or semantics are unresolved")
    statements = criterion_statements(objective)
    criteria = []
    if not isinstance(payload["criteria"], list) or len(payload["criteria"]) != len(statements):
        raise ObjectiveAcceptanceError("mandatory criterion coverage is incomplete")
    seen = set()
    for item in payload["criteria"]:
        if not isinstance(item, dict) or set(item) != {
            "criterion_id", "statement_sha256", "method", "task_ids", "validation_commands",
        }:
            raise ObjectiveAcceptanceError("criterion mapping schema is invalid")
        identifier = item["criterion_id"]
        if (not isinstance(identifier, str) or identifier not in statements or identifier in seen
                or item["statement_sha256"] != content_hash(statements[identifier])):
            raise ObjectiveAcceptanceError("criterion identity or text binding differs")
        seen.add(identifier)
        for field in ("task_ids", "validation_commands"):
            values = item[field]
            if (not isinstance(values, list) or len(values) > 200
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(set(values)) != len(values)):
                raise ObjectiveAcceptanceError("criterion mapping values are invalid")
        if not item["task_ids"] or item["method"] not in {"validation", "human"}:
            raise ObjectiveAcceptanceError("criterion verification method is invalid")
        if bool(item["validation_commands"]) != (item["method"] == "validation"):
            raise ObjectiveAcceptanceError("criterion verification method is ambiguous")
        criteria.append(AcceptanceCriterion(
            identifier, item["statement_sha256"], item["method"],
            tuple(item["task_ids"]), tuple(item["validation_commands"]),
        ))
    return objective, tuple(criteria)


def _from_context(context, project, session) -> ObjectiveAcceptance:
    payload = context.artifacts.get("objective_acceptance")
    objective, criteria = parse_objective_proposal(
        payload, project, session, context.generation_id,
    )
    return ObjectiveAcceptance(
        objective, objective_hash(objective), content_hash(payload),
        context.generation_id, context.manifest_hash, tuple(criteria),
        context.policy_hash, context.artifacts["constitution"]["constitution_id"],
        payload.get("approved_plan_sha256"),
    )
