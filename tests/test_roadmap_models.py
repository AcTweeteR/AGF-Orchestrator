import json
from dataclasses import replace
from pathlib import Path

import pytest

from agf_orchestrator.roadmap_models import (
    RoadmapItem,
    RoadmapItemStatus,
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


def test_lifecycle_requires_explicit_valid_transitions_and_dependencies():
    roadmap = roadmap_from_dict(load_fixture("valid_roadmap.json"))

    progressing = roadmap.transition("item-backlog", RoadmapItemStatus.IN_PROGRESS)
    completed = progressing.transition("item-backlog", RoadmapItemStatus.COMPLETED)

    assert roadmap.items[1].status is RoadmapItemStatus.READY
    assert completed.items[1].status is RoadmapItemStatus.COMPLETED
    with pytest.raises(RoadmapValidationError, match="invalid lifecycle transition"):
        roadmap.transition("item-backlog", RoadmapItemStatus.COMPLETED)


def test_supersession_is_explicit_and_non_destructive():
    roadmap = roadmap_from_dict(load_fixture("valid_roadmap.json"))
    replacement = RoadmapItem(
        "item-backlog-v2", "Replacement backlog", ("requirement-traceability",), (),
        ("Replacement is explicit",), "LOW", RoadmapItemStatus.READY,
    )
    roadmap = replace(roadmap, items=(*roadmap.items, replacement))
    roadmap.validate()

    superseded = roadmap.supersede("item-backlog", "item-backlog-v2")

    old = next(item for item in superseded.items if item.item_id == "item-backlog")
    assert old.status is RoadmapItemStatus.SUPERSEDED
    assert old.superseded_by == "item-backlog-v2"
    assert any(item.item_id == "item-backlog-v2" for item in superseded.items)


def test_eligible_items_are_ready_and_dependency_complete():
    roadmap = roadmap_from_dict(load_fixture("valid_roadmap.json"))

    assert [item.item_id for item in roadmap.eligible_items()] == ["item-backlog"]

    blocked = replace(
        roadmap,
        items=(
            replace(roadmap.items[0], status=RoadmapItemStatus.READY),
            roadmap.items[1],
        ),
    )
    assert [item.item_id for item in blocked.eligible_items()] == ["item-foundation"]


def test_critical_path_is_deterministic():
    roadmap = roadmap_from_dict(load_fixture("valid_roadmap.json"))

    assert roadmap.critical_path() == ("item-foundation", "item-backlog")


def test_eligible_items_use_priority_then_id_and_revision_is_monotonic():
    roadmap = roadmap_from_dict(load_fixture("valid_roadmap.json"))
    revised = roadmap.revise("2")

    assert revised.version == "2"
    assert roadmap.version == "1"
    with pytest.raises(RoadmapValidationError, match="increase monotonically"):
        roadmap.revise("1")
