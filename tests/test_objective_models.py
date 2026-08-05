import json
from pathlib import Path

import pytest

from agf_orchestrator.objective_models import (
    ObjectiveStatus,
    ObjectiveValidationError,
    canonical_objective_json,
    normalize_objective,
    objective_from_dict,
    objective_hash,
)

FIXTURES = Path(__file__).parent / "fixtures" / "objectives"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_fixture_round_trips_without_mutable_collections():
    objective = objective_from_dict(load_fixture("valid_objective.json"))

    assert objective.status is ObjectiveStatus.DRAFT
    assert objective.to_dict() == load_fixture("valid_objective.json")
    assert isinstance(objective.requirements, tuple)
    assert isinstance(objective.constraints, tuple)


@pytest.mark.parametrize(
    "fixture, message",
    [
        ("invalid_duplicate_requirement.json", "requirement_id values must be unique"),
        ("invalid_secret.json", "contains secret-shaped data"),
    ],
)
def test_invalid_fixtures_are_rejected(fixture, message):
    with pytest.raises(ObjectiveValidationError, match=message):
        objective_from_dict(load_fixture(fixture))


@pytest.mark.parametrize(
    "field",
    ["schema_version", "objective_id", "title", "statement", "requirements", "status"],
)
def test_missing_required_field_is_rejected(field):
    payload = load_fixture("valid_objective.json")
    payload.pop(field)

    with pytest.raises(ObjectiveValidationError, match="missing or contains unknown"):
        objective_from_dict(payload)


def test_unknown_field_is_rejected():
    payload = load_fixture("valid_objective.json")
    payload["transcript"] = "not permitted"

    with pytest.raises(ObjectiveValidationError, match="missing or contains unknown"):
        objective_from_dict(payload)


def test_approved_status_is_structurally_supported_without_approval_side_effect():
    payload = load_fixture("valid_objective.json")
    payload["status"] = "APPROVED"

    objective = objective_from_dict(payload)

    assert objective.status is ObjectiveStatus.APPROVED


def test_empty_completion_criteria_is_rejected():
    payload = load_fixture("valid_objective.json")
    payload["completion_criteria"] = []

    with pytest.raises(ObjectiveValidationError, match="completion_criteria must not be empty"):
        objective_from_dict(payload)


def test_invalid_objective_and_requirement_ids_are_rejected():
    payload = load_fixture("valid_objective.json")
    payload["objective_id"] = "unsafe/id"
    with pytest.raises(ObjectiveValidationError, match="objective_id is invalid"):
        objective_from_dict(payload)

    payload = load_fixture("valid_objective.json")
    payload["requirements"][0]["requirement_id"] = "unsafe/id"
    with pytest.raises(ObjectiveValidationError, match="requirement_id is invalid"):
        objective_from_dict(payload)


def test_normalization_is_pure_and_canonicalizes_text_and_collection_order():
    payload = load_fixture("valid_objective.json")
    source = objective_from_dict(payload)
    payload["title"] = "  Build   a safe system  "
    payload["requirements"] = list(reversed(payload["requirements"]))
    payload["constraints"] = list(reversed(payload["constraints"]))
    variant = objective_from_dict(payload)

    normalized = normalize_objective(variant)

    assert source.title == "Build a safe autonomous system"
    assert normalized.title == "Build a safe system"
    assert normalized.requirements[0].requirement_id == "requirement-demonstrable-completion"
    assert objective_hash(source) != objective_hash(variant)


def test_equivalent_objectives_have_equal_canonical_json_and_hash():
    first = objective_from_dict(load_fixture("valid_objective.json"))
    payload = load_fixture("valid_objective.json")
    payload["statement"] = (
        "  Build a safe system that reaches   demonstrable completion under approved governance.  "
    )
    payload["requirements"] = list(reversed(payload["requirements"]))
    payload["constraints"] = list(reversed(payload["constraints"]))
    second = objective_from_dict(payload)

    assert canonical_objective_json(first) == canonical_objective_json(second)
    assert objective_hash(first) == objective_hash(second)


def test_semantic_change_changes_hash():
    first = objective_from_dict(load_fixture("valid_objective.json"))
    payload = load_fixture("valid_objective.json")
    payload["statement"] = "Build a different system."
    second = objective_from_dict(payload)

    assert objective_hash(first) != objective_hash(second)
