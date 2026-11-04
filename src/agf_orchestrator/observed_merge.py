"""Owner-attested *observation* of already merged GitHub PRs.

These are NOT retroactive approvals and NEVER authorize a release or a
delivery. They establish a narrow, auditable fact for provider-target rebind.
The Owner controller is the sole issuer; runtime can only verify.
"""
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

from .locking import FileLock
from .owner_authority import OwnerAuthorityError, verify_envelope
from .project_registry import parse_remote_url


class ObservedMergeError(ValueError):
    """Observed result was absent, ambiguous, altered, or not authorized."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^project-[0-9a-f]{16}$")
_GITHUB_FIELDS = frozenset({
    "state", "merge_commit", "base_branch", "pr_number", "merged_at", "merged_by",
})


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, text=True,
        capture_output=True, timeout=15,
    ).stdout.strip()


@dataclass(frozen=True)
class ObservedMerge:
    schema_version: str
    evidence_id: str
    project_id: str
    repository_identity: str
    branch: str
    previous_sha: str
    target_sha: str
    github: dict
    owner_payload: dict
    owner_envelope: dict
    observed_at: str
    provenance: str
    evidence_hash: str

    def unsigned(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "evidence_hash"}

    def validate(self) -> None:
        if self.schema_version != "1.0" or self.provenance != "OWNER_POSTHOC_GITHUB_MERGE":
            raise ObservedMergeError("observed merge provenance is invalid")
        if not _ID.fullmatch(self.project_id) or not self.evidence_id.startswith(
            "observed-pr-"
        ):
            raise ObservedMergeError("observed merge identity is invalid")
        if not _SHA.fullmatch(self.previous_sha) or not _SHA.fullmatch(self.target_sha):
            raise ObservedMergeError("observed merge SHA is invalid")
        if self.previous_sha == self.target_sha:
            raise ObservedMergeError("observed merge has no target advance")
        if self.branch != "main" or not isinstance(self.repository_identity, str):
            raise ObservedMergeError("observed merge branch or repository is invalid")
        identity = parse_remote_url(self.repository_identity).identity
        if not identity.startswith("github.com/"):
            raise ObservedMergeError("observed merge must originate from GitHub")
        try:
            repository_owner, _name = identity.removeprefix("github.com/").split("/")
        except ValueError as exc:
            raise ObservedMergeError("observed merge GitHub origin is invalid") from exc
        if (
            not isinstance(self.github, dict) or set(self.github) != _GITHUB_FIELDS
            or self.github.get("state") != "MERGED"
            or self.github.get("base_branch") != self.branch
            or self.github.get("merge_commit") != self.target_sha
            or type(self.github.get("pr_number")) is not int
            or self.github["pr_number"] < 1
            or self.evidence_id != f"observed-pr-{self.github['pr_number']}-{self.target_sha[:12]}"
            or not isinstance(self.github.get("merged_at"), str)
            or not self.github["merged_at"]
            or not isinstance(self.github.get("merged_by"), str)
            or not self.github["merged_by"]
            or self.github["merged_by"].casefold() != repository_owner.casefold()
        ):
            raise ObservedMergeError("observed merge GitHub facts are invalid")
        expected_payload = {
            "schema_version": "1.0", "decision": "ACKNOWLEDGE_ALREADY_MERGED",
            "evidence_id": self.evidence_id,
            "project_id": self.project_id,
            "repository_identity": self.repository_identity,
            "branch": self.branch, "previous_sha": self.previous_sha,
            "target_sha": self.target_sha, "github": self.github,
            "observed_at": self.observed_at,
        }
        if self.owner_payload != expected_payload:
            raise ObservedMergeError("owner observed merge payload differs")
        try:
            verify_envelope(self.owner_payload, self.owner_envelope)
        except (OwnerAuthorityError, ValueError, TypeError) as exc:
            raise ObservedMergeError("observed merge Owner signature is invalid") from exc
        if _digest(self.unsigned()) != self.evidence_hash:
            raise ObservedMergeError("observed merge evidence checksum differs")

    def verify_project(self, project) -> None:
        self.validate()
        if (
            self.project_id != project.project_id
            or self.branch != project.default_branch
            or parse_remote_url(self.repository_identity).identity
            != parse_remote_url(project.origin_url).identity
        ):
            raise ObservedMergeError("observed merge project binding differs")
        repo = project.repository_root
        try:
            parents = _git(repo, "show", "-s", "--format=%P", self.target_sha).split()
            if not parents or parents[0] != self.previous_sha:
                raise ObservedMergeError("observed merge does not follow first parent")
            head = _git(repo, "rev-parse", "HEAD")
            first_parent = _git(repo, "rev-list", "--first-parent", head).splitlines()
            if self.target_sha not in first_parent:
                raise ObservedMergeError("observed merge is not canonical main history")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ObservedMergeError("observed merge local Git lineage is unavailable") from exc


class ObservedMergeStore:
    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).expanduser().resolve() / "observed-merges"

    def _path(self, item: ObservedMerge) -> Path:
        if not _ID.fullmatch(item.project_id):
            raise ObservedMergeError("observed merge namespace is invalid")
        return self.root / item.project_id / (item.evidence_id + ".json")

    def put(self, item: ObservedMerge) -> str:
        item.validate()
        path = self._path(item)
        if self.root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise ObservedMergeError("observed merge store symlink is invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(path.with_suffix(".lock"), "owner-observed-merge"):
            if path.exists():
                if self.get(item.project_id, path.stem) != item:
                    raise ObservedMergeError("conflicting observed merge replay")
            else:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=path.parent,
                        prefix=".observed.", delete=False,
                    ) as handle:
                        temporary = Path(handle.name)
                        handle.write(json.dumps(item.__dict__, sort_keys=True) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        return item.evidence_hash

    def get(self, project_id: str, evidence_id: str) -> ObservedMerge | None:
        if not _ID.fullmatch(project_id) or not re.fullmatch(
            r"observed-pr-[0-9]+-[0-9a-f]{12}", evidence_id
        ):
            raise ObservedMergeError("observed merge lookup identity is invalid")
        path = self.root / project_id / (evidence_id + ".json")
        if self.root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise ObservedMergeError("observed merge lookup symlink is invalid")
        if not path.is_file():
            return None
        try:
            item = ObservedMerge(**json.loads(path.read_text(encoding="utf-8")))
            item.validate()
            return item
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ObservedMergeError("observed merge record is invalid") from exc

    def all(self, project_id: str) -> tuple[ObservedMerge, ...]:
        if not _ID.fullmatch(project_id):
            raise ObservedMergeError("observed merge project is invalid")
        folder = self.root / project_id
        if self.root.is_symlink() or folder.is_symlink():
            raise ObservedMergeError("observed merge directory is invalid")
        if not folder.exists():
            return ()
        if not folder.is_dir():
            raise ObservedMergeError("observed merge directory is not a directory")
        return tuple(
            item for file in sorted(folder.glob("*.json"))
            if (item := self.get(project_id, file.stem)) is not None
        )


def verify_observed_merge_chain(
    store: ObservedMergeStore, project, old_sha: str, target_sha: str,
) -> bool:
    """A complete FIRST-PARENT chain only; no inferred commits or side parents."""
    if not _SHA.fullmatch(old_sha) or not _SHA.fullmatch(target_sha):
        raise ObservedMergeError("target hashes are invalid")
    by_previous: dict[str, list[ObservedMerge]] = {}
    for item in store.all(project.project_id):
        item.verify_project(project)
        by_previous.setdefault(item.previous_sha, []).append(item)
    cursor, visited = old_sha, set()
    while cursor != target_sha:
        options = by_previous.get(cursor, ())
        if len(options) != 1:
            return False
        item = options[0]
        if item.target_sha in visited:
            raise ObservedMergeError("observed merge cycle")
        visited.add(item.target_sha)
        cursor = item.target_sha
    return True
