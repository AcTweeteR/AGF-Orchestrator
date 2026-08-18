"""Persistent, fail-closed verification of Owner-authorized project scope.

Scope authorization is deliberately separate from architecture, delivery, and
policy records.  The external Owner controller creates the signed record;
runtime code only verifies and consumes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .owner_authority import OwnerAuthorityError, verify_envelope
from .project_registry import parse_remote_url

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")


class ScopeAuthorizationError(ValueError):
    """Raised when Owner scope evidence is absent, stale, or invalid."""


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ScopeAuthorization:
    schema_version: str
    authorization_id: str
    project_id: str
    session_id: str | None
    repository_identity: str
    baseline_sha: str
    scope_id: str
    decision: str
    boundaries: tuple[str, ...]
    operation_id: str
    issued_at: str
    owner_payload: dict[str, Any]
    owner_envelope: dict[str, Any]
    evidence_hash: str

    def unsigned(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value.pop("evidence_hash")
        return value

    def signed_payload(self) -> dict[str, Any]:
        value = self.unsigned()
        value.pop("owner_payload")
        value.pop("owner_envelope")
        return value

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.authorization_id):
            raise ScopeAuthorizationError("scope authorization identity is invalid")
        if not self.project_id.startswith("project-"):
            raise ScopeAuthorizationError("scope authorization project binding is invalid")
        if self.session_id is not None and not self.session_id.startswith("session-"):
            raise ScopeAuthorizationError("scope authorization session binding is invalid")
        if not _SHA.fullmatch(self.baseline_sha):
            raise ScopeAuthorizationError("scope authorization baseline is invalid")
        if not _ID.fullmatch(self.scope_id) or not _ID.fullmatch(self.operation_id):
            raise ScopeAuthorizationError("scope authorization scope or operation is invalid")
        if self.decision != "AUTHORIZED_AND_REQUIRED":
            raise ScopeAuthorizationError("scope authorization decision is invalid")
        if not self.boundaries or any(
            not isinstance(item, str) or not item for item in self.boundaries
        ):
            raise ScopeAuthorizationError("scope authorization boundaries are invalid")
        if not isinstance(self.owner_payload, dict) or not isinstance(self.owner_envelope, dict):
            raise ScopeAuthorizationError("scope authorization signature is missing")
        expected = self.signed_payload()
        if self.evidence_hash != _hash(self.unsigned()):
            raise ScopeAuthorizationError("scope authorization evidence hash is invalid")
        if self.owner_payload != expected:
            raise ScopeAuthorizationError("scope authorization signed payload mismatch")
        try:
            verify_envelope(self.owner_payload, self.owner_envelope)
        except OwnerAuthorityError as exc:
            raise ScopeAuthorizationError("scope authorization signature is invalid") from exc


class ScopeAuthorizationStore:
    """Immutable, idempotent storage under the project state namespace."""

    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).expanduser().resolve() / "scope-authorizations"

    def _path(self, project_id: str, authorization_id: str) -> Path:
        if not project_id.startswith("project-") or not _ID.fullmatch(authorization_id):
            raise ScopeAuthorizationError("scope authorization path identity is invalid")
        return self.root / project_id / f"{authorization_id}.json"

    def get(self, project_id: str, authorization_id: str) -> ScopeAuthorization | None:
        path = self._path(project_id, authorization_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["boundaries"] = tuple(payload["boundaries"])
            payload["owner_payload"]["boundaries"] = tuple(payload["owner_payload"]["boundaries"])
            item = ScopeAuthorization(**payload)
            item.validate()
            return item
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ScopeAuthorizationError("scope authorization record is unreadable") from exc

    def put(self, item: ScopeAuthorization) -> str:
        item.validate()
        path = self._path(item.project_id, item.authorization_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(item.__dict__, sort_keys=True, ensure_ascii=False) + "\n"
        existing = self.get(item.project_id, item.authorization_id)
        if existing is not None:
            if existing != item:
                raise ScopeAuthorizationError("scope authorization replay conflicts")
            return existing.evidence_hash
        path.write_text(payload, encoding="utf-8")
        return item.evidence_hash


def verify_scope_authorization(
    item: ScopeAuthorization,
    project: Any,
    repository: str | Path,
    *,
    target_sha: str,
    scope_id: str,
    session_id: str | None = None,
    allowed_boundaries: tuple[str, ...] | None = None,
) -> None:
    """Verify a scope authorization at a current or derived target."""
    item.validate()
    if item.project_id != project.project_id or item.scope_id != scope_id:
        raise ScopeAuthorizationError("scope authorization project or scope mismatch")
    if session_id is not None and item.session_id != session_id:
        raise ScopeAuthorizationError("scope authorization session mismatch")
    if allowed_boundaries is not None and not set(item.boundaries).issubset(allowed_boundaries):
        raise ScopeAuthorizationError("scope authorization exceeds its allowed boundaries")
    if not _SHA.fullmatch(target_sha):
        raise ScopeAuthorizationError("scope authorization target is invalid")
    if (
        parse_remote_url(item.repository_identity).identity
        != parse_remote_url(project.origin_url).identity
    ):
        raise ScopeAuthorizationError("scope authorization repository mismatch")
    root = Path(repository).resolve()
    try:
        subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", item.baseline_sha, target_sha],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScopeAuthorizationError("scope authorization target is outside its lineage") from exc


def authorization_id(
    project_id: str, session_id: str | None, scope_id: str, operation_id: str
) -> str:
    value = f"{project_id}:{session_id or '-'}:{scope_id}:{operation_id}"
    return "scope-" + hashlib.sha256(value.encode()).hexdigest()[:32]
