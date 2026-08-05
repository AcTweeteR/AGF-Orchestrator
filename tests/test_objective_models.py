import json
from pathlib import Path

import pytest

from agf_orchestrator.objective_models import (
    ObjectiveStatus,
    ObjectiveValidationError,
    objective_from_dict,
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
