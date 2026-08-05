"""Immutable roadmap and backlog schema with deterministic dependency checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class RoadmapValidationError(ValueError):
    """Raised when a roadmap or backlog item is invalid."""


class RoadmapStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class RoadmapItemStatus(StrEnum):
    TODO = "TODO"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"


_ROADMAP_ID = re.compile(r"^roadmap-[a-z0-9][a-z0-9-]{0,79}$")
_ITEM_ID = re.compile(r"^item-[a-z0-9][a-z0-9-]{0,79}$")
_OBJECTIVE_ID = re.compile(r"^objective-[a-z0-9][a-z0-9-]{0,79}$")
_REQUIREMENT_ID = re.compile(r"^requirement-[a-z0-9][a-z0-9-]{0,79}$")
_MAX_ITEMS = 500
_MAX_TEXT = 4000


@dataclass(frozen=True)
class RoadmapItem:
    item_id: str
    title: str
    objective_refs: tuple[str, ...]
    depends_on: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    risk_level: str
    status: RoadmapItemStatus
    superseded_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "objective_refs": list(self.objective_refs),
            "depends_on": list(self.depends_on),
            "acceptance_criteria": list(self.acceptance_criteria),
            "risk_level": self.risk_level,
            "status": self.status.value,
            "superseded_by": self.superseded_by,
        }


@dataclass(frozen=True)
class Roadmap:
    schema_version: str
    roadmap_id: str
    version: str
    objective_id: str
    items: tuple[RoadmapItem, ...]
    status: RoadmapStatus

    _TRANSITIONS = {
        RoadmapItemStatus.TODO: {
            RoadmapItemStatus.READY,
            RoadmapItemStatus.BLOCKED,
            RoadmapItemStatus.SUPERSEDED,
        },
        RoadmapItemStatus.READY: {
            RoadmapItemStatus.IN_PROGRESS,
            RoadmapItemStatus.BLOCKED,
            RoadmapItemStatus.SUPERSEDED,
        },
        RoadmapItemStatus.IN_PROGRESS: {
            RoadmapItemStatus.COMPLETED,
            RoadmapItemStatus.BLOCKED,
            RoadmapItemStatus.SUPERSEDED,
        },
        RoadmapItemStatus.BLOCKED: {
            RoadmapItemStatus.READY,
            RoadmapItemStatus.SUPERSEDED,
        },
        RoadmapItemStatus.COMPLETED: {RoadmapItemStatus.SUPERSEDED},
        RoadmapItemStatus.SUPERSEDED: set(),
    }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "roadmap_id": self.roadmap_id,
            "version": self.version,
            "objective_id": self.objective_id,
            "items": [item.to_dict() for item in self.items],
            "status": self.status.value,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise RoadmapValidationError("schema_version must be 1.0")
        if not _ROADMAP_ID.fullmatch(self.roadmap_id):
            raise RoadmapValidationError("roadmap_id is invalid")
        if not self.version.strip():
            raise RoadmapValidationError("version is required")
        if not _OBJECTIVE_ID.fullmatch(self.objective_id):
            raise RoadmapValidationError("objective_id is invalid")
        if not isinstance(self.status, RoadmapStatus):
            raise RoadmapValidationError("status is invalid")
        if not self.items or len(self.items) > _MAX_ITEMS:
            raise RoadmapValidationError("items must contain 1 to 500 items")
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise RoadmapValidationError("item_id values must be unique")
        known = set(ids)
        edges: dict[str, set[str]] = {item_id: set() for item_id in ids}
        for item in self.items:
            self._validate_item(item)
            if not set(item.depends_on).issubset(known):
                raise RoadmapValidationError(f"item {item.item_id} has an unknown dependency")
            edges[item.item_id].update(item.depends_on)
        self._validate_acyclic(edges)

    def transition(self, item_id: str, status: RoadmapItemStatus) -> "Roadmap":
        """Return a new roadmap after one explicit lifecycle transition."""
        item = self._item(item_id)
        if status not in self._TRANSITIONS[item.status]:
            raise RoadmapValidationError(
                f"invalid lifecycle transition: {item.status.value} -> {status.value}"
            )
        if status is RoadmapItemStatus.COMPLETED:
            items = {candidate.item_id: candidate for candidate in self.items}
            if any(
                items[dependency].status is not RoadmapItemStatus.COMPLETED
                for dependency in item.depends_on
            ):
                raise RoadmapValidationError("item cannot complete before dependencies")
        return self._replace_item(replace(item, status=status))

    def supersede(self, item_id: str, replacement_id: str) -> "Roadmap":
        """Return a new roadmap with an explicit, non-destructive replacement link."""
        if item_id == replacement_id:
            raise RoadmapValidationError("an item cannot supersede itself")
        item = self._item(item_id)
        self._item(replacement_id)
        if item.status is RoadmapItemStatus.SUPERSEDED:
            raise RoadmapValidationError("superseded item cannot be superseded again")
        return self._replace_item(
            replace(item, status=RoadmapItemStatus.SUPERSEDED, superseded_by=replacement_id)
        )

    def eligible_items(self) -> tuple[RoadmapItem, ...]:
        """Return READY items whose dependencies are all COMPLETED, sorted by ID."""
        items = {item.item_id: item for item in self.items}
        return tuple(
            sorted(
                (
                    item
                    for item in self.items
                    if item.status is RoadmapItemStatus.READY
                    and all(
                        items[dependency].status is RoadmapItemStatus.COMPLETED
                        for dependency in item.depends_on
                    )
                ),
                key=lambda item: item.item_id,
            )
        )

    def critical_path(self) -> tuple[str, ...]:
        """Return one deterministic longest dependency path from root to leaf."""
        items = {item.item_id: item for item in self.items}
        memo: dict[str, tuple[str, ...]] = {}

        def path(item_id: str) -> tuple[str, ...]:
            if item_id in memo:
                return memo[item_id]
            item = items[item_id]
            if not item.depends_on:
                result = (item_id,)
            else:
                predecessors = sorted((path(dependency) for dependency in item.depends_on))
                result = (*max(predecessors, key=lambda value: (len(value), value)), item_id)
            memo[item_id] = result
            return result

        return max(
            (path(item_id) for item_id in sorted(items)),
            key=lambda value: (len(value), value),
        )

    def _item(self, item_id: str) -> RoadmapItem:
        matches = [item for item in self.items if item.item_id == item_id]
        if len(matches) != 1:
            raise RoadmapValidationError("roadmap item is missing or ambiguous")
        return matches[0]

    def _replace_item(self, updated: RoadmapItem) -> "Roadmap":
        roadmap = replace(
            self,
            items=tuple(
                updated if item.item_id == updated.item_id else item for item in self.items
            ),
        )
        roadmap.validate()
        return roadmap

    @staticmethod
    def _validate_item(item: RoadmapItem) -> None:
        if not _ITEM_ID.fullmatch(item.item_id):
            raise RoadmapValidationError("item_id is invalid")
        if not isinstance(item.title, str) or not item.title.strip() or len(item.title) > _MAX_TEXT:
            raise RoadmapValidationError("item title is invalid")
        if not item.objective_refs or any(
            not _REQUIREMENT_ID.fullmatch(ref) for ref in item.objective_refs
        ):
            raise RoadmapValidationError(f"item {item.item_id} has invalid objective references")
        if len(item.depends_on) != len(set(item.depends_on)):
            raise RoadmapValidationError(f"item {item.item_id} dependencies must be unique")
        if not item.acceptance_criteria or any(
            not isinstance(value, str) or not value.strip() for value in item.acceptance_criteria
        ):
            raise RoadmapValidationError(f"item {item.item_id} acceptance criteria are invalid")
        if not isinstance(item.status, RoadmapItemStatus):
            raise RoadmapValidationError(f"item {item.item_id} status is invalid")
        if not item.risk_level.strip():
            raise RoadmapValidationError(f"item {item.item_id} risk level is required")
        if item.superseded_by is not None and not _ITEM_ID.fullmatch(item.superseded_by):
            raise RoadmapValidationError(f"item {item.item_id} superseded_by is invalid")
        if item.status is not RoadmapItemStatus.SUPERSEDED and item.superseded_by is not None:
            raise RoadmapValidationError("only SUPERSEDED items may have superseded_by")

    @staticmethod
    def _validate_acyclic(edges: dict[str, set[str]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item_id: str) -> None:
            if item_id in visiting:
                raise RoadmapValidationError("roadmap dependencies contain a cycle")
            if item_id in visited:
                return
            visiting.add(item_id)
            for dependency in sorted(edges[item_id]):
                visit(dependency)
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in sorted(edges):
            visit(item_id)


def roadmap_from_dict(payload: dict[str, Any]) -> Roadmap:
    required = {"schema_version", "roadmap_id", "version", "objective_id", "items", "status"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise RoadmapValidationError("roadmap schema is missing or contains unknown fields")
    try:
        items = tuple(
            RoadmapItem(
                item_id=item["item_id"],
                title=item["title"],
                objective_refs=tuple(item["objective_refs"]),
                depends_on=tuple(item["depends_on"]),
                acceptance_criteria=tuple(item["acceptance_criteria"]),
                risk_level=item["risk_level"],
                status=RoadmapItemStatus(item["status"]),
                superseded_by=item.get("superseded_by"),
            )
            for item in payload["items"]
        )
        roadmap = Roadmap(
            schema_version=payload["schema_version"],
            roadmap_id=payload["roadmap_id"],
            version=payload["version"],
            objective_id=payload["objective_id"],
            items=items,
            status=RoadmapStatus(payload["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RoadmapValidationError(f"invalid roadmap structure: {exc}") from exc
    roadmap.validate()
    return roadmap
