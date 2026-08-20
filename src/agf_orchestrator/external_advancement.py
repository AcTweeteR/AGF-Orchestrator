"""Fail-closed reconciliation of owner-authorized external target advances."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .locking import FileLock
from .owner_authority import OwnerAuthorityError, verify_envelope
from .project_registry import _git, parse_remote_url


class ExternalAdvancementError(ValueError):
    """Raised when an external target advance cannot be proven safely."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExternalAdvancement:
    schema_version: str
    advancement_id: str
    project_id: str
    session_id: str
    repository_identity: str
    branch: str
    previous_sha: str
    target_sha: str
    github: dict[str, Any]
    owner_payload: dict[str, Any]
    owner_envelope: dict[str, Any]
    observed_at: str
    provenance: str
    evidence_hash: str

    def unsigned(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value.pop("evidence_hash")
        return value

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.advancement_id):
            raise ExternalAdvancementError("external advancement identity is invalid")
        if not self.project_id.startswith("project-") or not self.session_id.startswith("session-"):
            raise ExternalAdvancementError("external advancement binding is invalid")
        if not _SHA.fullmatch(self.previous_sha) or not _SHA.fullmatch(self.target_sha):
            raise ExternalAdvancementError("external advancement SHA is invalid")
        if self.previous_sha == self.target_sha:
            raise ExternalAdvancementError("external advancement target or branch is invalid")
        if self.provenance != "EXTERNAL_OWNER_AUTHORIZED_ADVANCE":
            raise ExternalAdvancementError("external advancement provenance is invalid")
        if not isinstance(self.github, dict) or self.github.get("state") != "MERGED":
            raise ExternalAdvancementError("GitHub merge evidence is missing")
        if self.github.get("merge_commit") != self.target_sha:
            raise ExternalAdvancementError("GitHub merge target does not match")
        if self.github.get("base_branch") != self.branch:
            raise ExternalAdvancementError("GitHub base branch does not match")
        if not isinstance(self.owner_payload, dict) or not isinstance(self.owner_envelope, dict):
            raise ExternalAdvancementError("Owner authorization evidence is missing")
        try:
            if self.owner_payload.get("project_id") != self.project_id:
                raise ExternalAdvancementError("Owner authorization project mismatch")
            if self.owner_payload.get("target_sha") != self.target_sha:
                raise ExternalAdvancementError("Owner authorization target mismatch")
            if self.owner_payload.get("operation_id") != self.advancement_id:
                raise ExternalAdvancementError("Owner authorization operation mismatch")
            if self.owner_payload.get("decision") != "AUTHORIZE_EXTERNAL_ADVANCE":
                raise ExternalAdvancementError("Owner authorization decision mismatch")
            if self.owner_payload.get("branch") != self.branch:
                raise ExternalAdvancementError("Owner authorization branch mismatch")
            if self.owner_payload.get("previous_sha") != self.previous_sha:
                raise ExternalAdvancementError("Owner authorization baseline mismatch")
            if self.owner_payload.get("github") != self.github:
                raise ExternalAdvancementError("Owner authorization merge evidence mismatch")
            if (
                parse_remote_url(self.owner_payload.get("repository_identity", "")).identity
                != parse_remote_url(self.repository_identity).identity
            ):
                raise ExternalAdvancementError("Owner authorization repository mismatch")
            verify_envelope(self.owner_payload, self.owner_envelope)
        except (AttributeError, TypeError, OwnerAuthorityError) as exc:
            raise ExternalAdvancementError("Owner authorization is invalid") from exc
        if self.evidence_hash != _hash(self.unsigned()):
            raise ExternalAdvancementError("external advancement evidence hash is invalid")


class ExternalAdvancementStore:
    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).expanduser().resolve() / "external-advancements"

    def _path(self, project_id: str, advancement_id: str) -> Path:
        if not project_id.startswith("project-") or not _ID.fullmatch(advancement_id):
            raise ExternalAdvancementError("external advancement path identity is invalid")
        return self.root / project_id / f"{advancement_id}.json"

    def get(self, project_id: str, advancement_id: str) -> ExternalAdvancement | None:
        path = self._path(project_id, advancement_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        item = ExternalAdvancement(**payload)
        item.validate()
        return item

    def put(self, item: ExternalAdvancement) -> str:
        item.validate()
        path = self._path(item.project_id, item.advancement_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.get(item.project_id, item.advancement_id)
        if existing is not None and existing != item:
            raise ExternalAdvancementError("external advancement replay conflicts")
        if existing is None:
            path.write_text(json.dumps(item.__dict__, sort_keys=True) + "\n", encoding="utf-8")
        return item.evidence_hash


@dataclass(frozen=True)
class ExternalResultAcceptance:
    """Owner acceptance of an already-observed external result.

    This record never authorizes the historical external action and is kept
    separate from ExternalAdvancement to prevent provenance laundering.
    """

    schema_version: str
    acceptance_id: str
    project_id: str
    session_id: str
    repository_identity: str
    branch: str
    previous_sha: str
    target_sha: str
    github: dict[str, Any]
    owner_payload: dict[str, Any]
    owner_envelope: dict[str, Any]
    observed_at: str
    provenance: str
    evidence_hash: str

    def unsigned(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value.pop("evidence_hash")
        return value

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.acceptance_id):
            raise ExternalAdvancementError("external result acceptance identity is invalid")
        if not self.project_id.startswith("project-") or not self.session_id.startswith("session-"):
            raise ExternalAdvancementError("external result acceptance binding is invalid")
        if not _SHA.fullmatch(self.previous_sha) or not _SHA.fullmatch(self.target_sha):
            raise ExternalAdvancementError("external result acceptance SHA is invalid")
        if self.provenance != "EXTERNAL_RESULT_ACCEPTANCE":
            raise ExternalAdvancementError("external result acceptance provenance is invalid")
        if not isinstance(self.github, dict) or self.github.get("state") != "MERGED":
            raise ExternalAdvancementError("external result merge evidence is missing")
        if self.github.get("merge_commit") != self.target_sha:
            raise ExternalAdvancementError("external result target does not match")
        if not isinstance(self.owner_payload, dict) or not isinstance(self.owner_envelope, dict):
            raise ExternalAdvancementError("external result Owner acceptance is missing")
        if self.owner_payload.get("decision") != "ACCEPT_EXTERNAL_RESULT":
            raise ExternalAdvancementError("external result acceptance decision is invalid")
        if self.owner_payload.get("acceptance_id") != self.acceptance_id:
            raise ExternalAdvancementError("external result acceptance identity mismatch")
        if self.owner_payload.get("project_id") != self.project_id:
            raise ExternalAdvancementError("external result acceptance project mismatch")
        if self.owner_payload.get("target_sha") != self.target_sha:
            raise ExternalAdvancementError("external result acceptance target mismatch")
        if self.owner_payload.get("previous_sha") != self.previous_sha:
            raise ExternalAdvancementError("external result acceptance baseline mismatch")
        if self.owner_payload.get("github") != self.github:
            raise ExternalAdvancementError("external result acceptance merge mismatch")
        try:
            verify_envelope(self.owner_payload, self.owner_envelope)
        except (AttributeError, TypeError, OwnerAuthorityError) as exc:
            raise ExternalAdvancementError("external result Owner signature is invalid") from exc
        if self.evidence_hash != _hash(self.unsigned()):
            raise ExternalAdvancementError("external result acceptance hash is invalid")


class ExternalResultAcceptanceStore:
    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).expanduser().resolve() / "external-result-acceptances"

    def _path(self, project_id: str, acceptance_id: str) -> Path:
        if not project_id.startswith("project-") or not _ID.fullmatch(acceptance_id):
            raise ExternalAdvancementError("external result acceptance path identity is invalid")
        return self.root / project_id / f"{acceptance_id}.json"

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

    def get(self, project_id: str, acceptance_id: str) -> ExternalResultAcceptance | None:
        path = self._path(project_id, acceptance_id)
        if not path.exists():
            return None
        if path.is_symlink():
            raise ExternalAdvancementError("external result acceptance must not be a symlink")
        try:
            item = ExternalResultAcceptance(**json.loads(path.read_text(encoding="utf-8")))
            item.validate()
            return item
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ExternalAdvancementError("external result acceptance is unreadable") from exc

    def put(self, item: ExternalResultAcceptance) -> str:
        item.validate()
        path = self._path(item.project_id, item.acceptance_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(f".{path.name}.lock")
        with FileLock(lock, "external-result-acceptance"):
            existing = self.get(item.project_id, item.acceptance_id)
            if existing is not None and existing != item:
                raise ExternalAdvancementError("external result acceptance replay conflicts")
            if existing is None:
                self._atomic_json(path, item.__dict__)
        return item.evidence_hash



def verify_external_advancement(
    item: ExternalAdvancement,
    project,
    repository: str | Path,
    *,
    require_current_target: bool = True,
) -> None:
    item.validate()
    root = Path(repository).resolve()
    if item.project_id != project.project_id:
        raise ExternalAdvancementError("external advancement project mismatch")
    if item.branch != project.default_branch:
        raise ExternalAdvancementError("external advancement branch binding mismatch")
    project_identity = parse_remote_url(project.origin_url).identity
    evidence_identity = parse_remote_url(item.repository_identity).identity
    if project_identity != evidence_identity:
        raise ExternalAdvancementError("external advancement repository mismatch")
    if require_current_target:
        if _git(root, "branch", "--show-current") != project.default_branch:
            raise ExternalAdvancementError("external advancement branch is not canonical")
        if _git(root, "rev-parse", "HEAD") != item.target_sha:
            raise ExternalAdvancementError("external advancement target is not checked out")
    try:
        subprocess.run(
            [
                "git", "-C", str(root), "merge-base", "--is-ancestor",
                item.previous_sha, item.target_sha,
            ],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ExternalAdvancementError(
            "external advancement lineage is not an ancestor relation"
        ) from exc
