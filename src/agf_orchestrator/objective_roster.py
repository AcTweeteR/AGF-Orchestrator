"""Additional Owner-approved Objective sessions in one signed generation.

Legacy objective_acceptance remains immutable in the active manifest.
Additional entries are append-only in a separately Owner-signed roster. Every
new entry is approved in the same independent Owner publication path; there is
no automatic approval, cross-session substitution, or override of earlier
sessions. Bound to the full Ed25519 authority generation and policy.
"""
from __future__ import annotations

import re
from typing import Any

_SESSION = re.compile(r"^session-[0-9a-f]{20,24}$")
_GENERATION = re.compile(r"^generation-[1-9][0-9]*$")
_LIMIT = 64


class ObjectiveRosterError(ValueError):
    pass


def validate_roster(
    value: object, project_id: str, legacy: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(legacy, dict) or not _SESSION.fullmatch(
        str(legacy.get("session_id", ""))
    ):
        raise ObjectiveRosterError("signed legacy Objective is invalid")
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "project_id", "entries"}
        or value["schema_version"] != "1.0"
        or value["project_id"] != project_id
        or not isinstance(value["entries"], dict)
        or not 1 <= len(value["entries"]) <= _LIMIT
    ):
        raise ObjectiveRosterError("signed Objective roster schema is invalid")
    entries = value["entries"]
    if legacy["session_id"] in entries:
        raise ObjectiveRosterError("signed legacy Objective is duplicated in roster")
    for key, component in entries.items():
        if (
            not isinstance(key, str) or not _SESSION.fullmatch(key)
            or not isinstance(component, dict)
            or component.get("session_id") != key
            or component.get("project_id") != project_id
            or component.get("schema_version") != "2.0"
            or not isinstance(component.get("generation_id"), str)
            or not _GENERATION.fullmatch(component["generation_id"])
            or not isinstance(component.get("approved_plan_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", component["approved_plan_sha256"])
        ):
            raise ObjectiveRosterError("signed Objective roster session binding is invalid")
    return entries


def add_reviewed_objective(
    roster: dict[str, Any] | None,
    legacy: dict[str, Any],
    component: dict[str, Any],
    project_id: str,
    session,
) -> dict[str, Any]:
    """Prepare a new signed roster candidate, never mutate the current roster."""
    prior = validate_roster(roster, project_id, legacy) if roster is not None else {}
    ident = component["session_id"]
    if ident == legacy["session_id"] or ident in prior:
        raise ObjectiveRosterError("Owner has already accepted this Objective session")
    if not _SESSION.fullmatch(ident) or ident != session.session_id:
        raise ObjectiveRosterError("new Objective session identity differs")
    if len(prior) >= _LIMIT:
        raise ObjectiveRosterError("signed Objective roster exceeds bounded capacity")
    return {
        "schema_version": "1.0",
        "project_id": project_id,
        "entries": {**prior, ident: component},
    }
