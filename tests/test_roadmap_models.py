import json
from pathlib import Path

import pytest

from agf_orchestrator.roadmap_models import (
    RoadmapStatus,
    RoadmapValidationError,
    roadmap_from_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "roadmaps"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_roadmap_round_trips_and_preserves_dependency_order():
    payload = load_fixture("valid_roadmap.json")
    roadmap = roadmap_from_dict(payload)

    assert roadmap.status is RoadmapStatus.ACTIVE
    assert roadmap.to_dict() == payload
    assert roadmap.items[1].depends_on == ("item-foundation",)


@pytest.mark.parametrize(
    "fixture, message",
    [
        ("invalid_cycle.json", "contain a cycle"),
        ("invalid_missing_dependency.json", "unknown dependency"),
    ],
)
def test_invalid_dependency_fixtures_are_rejected(fixture, message):
    with pytest.raises(RoadmapValidationError, match=message):
        roadmap_from_dict(load_fixture(fixture))


def test_unknown_top_level_field_is_rejected():
    payload = load_fixture("valid_roadmap.json")
    payload["transcript"] = "not permitted"

    with pytest.raises(RoadmapValidationError, match="missing or contains unknown"):
        roadmap_from_dict(payload)


def test_duplicate_item_ids_are_rejected():
    payload = load_fixture("valid_roadmap.json")
    payload["items"][1]["item_id"] = payload["items"][0]["item_id"]

    with pytest.raises(RoadmapValidationError, match="item_id values must be unique"):
        roadmap_from_dict(payload)
