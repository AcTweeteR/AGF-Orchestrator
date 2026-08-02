"""Persistent project registration models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ProjectPolicy:
    allowed_remote_hosts: list[str] = field(default_factory=list)
    allow_dirty_planning: bool = False
    allow_live_execution: bool = False
    allow_delivery: bool = False
    require_human_merge: bool = True
    maximum_correction_rounds: int = 2


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    repository_root: str
    origin_url: str
    default_branch: str
    current_head_sha: str
    registered_at: str
    verified_at: str
    status: ProjectStatus
    policy: ProjectPolicy
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_from_dict(payload: dict[str, Any]) -> Project:
    required = {
        "project_id",
        "name",
        "repository_root",
        "origin_url",
        "default_branch",
        "current_head_sha",
        "registered_at",
        "verified_at",
        "status",
        "policy",
        "metadata",
    }
    if set(payload) != required:
        raise ValueError("project schema is missing or contains unknown fields")
    policy = ProjectPolicy(**payload["policy"])
    return Project(
        project_id=payload["project_id"],
        name=payload["name"],
        repository_root=payload["repository_root"],
        origin_url=payload["origin_url"],
        default_branch=payload["default_branch"],
        current_head_sha=payload["current_head_sha"],
        registered_at=payload["registered_at"],
        verified_at=payload["verified_at"],
        status=ProjectStatus(payload["status"]),
        policy=policy,
        metadata=payload["metadata"],
    )
