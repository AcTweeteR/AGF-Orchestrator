"""Read-only resilience diagnostics and bounded evidence ergonomics.

This module derives trust, doctor findings, scorecards, and evidence archives
from persisted AGF state.  It never authorizes work or mutates a repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .project_models import Project
from .remote_identity import RemoteIdentityError, canonical_remote_identity
from .session_models import (
    ACTOR_VALUES,
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Session,
    SessionEvent,
    SessionStatus,
)
from .session_store import SessionStore, SessionStoreError

_MAX_ARCHIVE_FILES = 256
_MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
_SECRET_SHAPED = re.compile(r"(?i)[\"']?(api[_-]?key|token|secret|password)[\"']?\s*[:=]")


class ResilienceError(RuntimeError):
    """Raised when persisted resilience evidence is unsafe or malformed."""


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WorkspaceTrust:
    project_id: str
    repository_root: str
    origin_identity: str
    target_sha: str

    def validate(self) -> None:
        if not self.project_id or not self.repository_root or not self.origin_identity:
            raise ResilienceError("workspace trust binding is incomplete")
        if len(self.target_sha) != 40 or any(c not in "0123456789abcdef" for c in self.target_sha):
            raise ResilienceError("workspace trust target SHA is invalid")


def bind_workspace(project: Project, *, repository_root: str, origin_url: str,
                   target_sha: str) -> WorkspaceTrust:
    """Create a binding from already verified project identity."""
    try:
        identity = canonical_remote_identity(origin_url)
        expected = canonical_remote_identity(project.origin_url)
    except RemoteIdentityError as exc:
        raise ResilienceError("workspace origin identity is unverifiable") from exc
    binding = WorkspaceTrust(
        project.project_id, str(Path(repository_root).resolve()), identity, target_sha
    )
    binding.validate()
    if (
        binding.repository_root != str(Path(project.repository_root).resolve())
        or identity != expected
    ):
        raise ResilienceError("workspace does not match registered project")
    return binding


def verify_workspace(project: Project, binding: WorkspaceTrust, *, repository_root: str,
                     origin_url: str, target_sha: str) -> tuple[DiagnosticStatus, list[str]]:
    """Compare current identity with a persisted binding; no optimistic fallback."""
    try:
        binding.validate()
        current = bind_workspace(project, repository_root=repository_root,
                                 origin_url=origin_url, target_sha=target_sha)
    except (ResilienceError, OSError):
        return DiagnosticStatus.UNKNOWN, ["workspace identity is unverifiable"]
    issues = []
    if current.project_id != binding.project_id:
        issues.append("workspace project binding differs")
    if current.repository_root != binding.repository_root:
        issues.append("workspace repository root differs")
    if current.origin_identity != binding.origin_identity:
        issues.append("workspace origin differs")
    if current.target_sha != binding.target_sha:
        issues.append("workspace target revision differs")
    return (DiagnosticStatus.PASS if not issues else DiagnosticStatus.FAIL), issues


@dataclass(frozen=True)
class DoctorFinding:
    check: str
    status: DiagnosticStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "status": self.status.value, "detail": self.detail}


@dataclass(frozen=True)
class Scorecard:
    session_id: str
    status: str
    event_count: int
    artifact_count: int
    evidence_count: int
    terminal: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def derive_scorecard(session: Session) -> Scorecard:
    """Derive bounded counts from state; model claims are not inputs."""
    evidence_count = sum(len(event.evidence_refs) for event in session.events)
    return Scorecard(session.session_id, session.status.value, len(session.events),
                     len(session.artifact_hashes), evidence_count,
                     session.status in TERMINAL_STATUSES)


def validate_recovery_lineage(session: Session) -> tuple[DiagnosticStatus, str]:
    """Validate the existing session event chain without creating new semantics."""
    if not session.session_id or not session.project_id or not session.events:
        return DiagnosticStatus.UNKNOWN, "session binding or event lineage is missing"
    seen: dict[str, tuple[SessionEvent, set[str]]] = {}
    previous: SessionStatus | None = None
    previous_time: datetime | None = None
    for event in session.events:
        if event.session_id != session.session_id:
            return DiagnosticStatus.FAIL, "event session binding differs"
        if not event.operation_id:
            return DiagnosticStatus.FAIL, "event operation identity is missing"
        assessment_refs = _assessment_event_refs(event, session)
        prior_operation = seen.get(event.operation_id)
        if prior_operation is not None:
            previous_event, previous_refs = prior_operation
            if (
                not _valid_assessment_event(previous_event, session)
                or not _valid_assessment_event(event, session)
                or assessment_refs & previous_refs
            ):
                return DiagnosticStatus.FAIL, "event operation identity is duplicated"
        else:
            seen[event.operation_id] = (event, set())
            previous_refs = seen[event.operation_id][1]
        previous_refs.update(assessment_refs)
        try:
            target = SessionStatus(event.to_status)
            source = SessionStatus(event.from_status) if event.from_status else None
            observed = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return DiagnosticStatus.FAIL, "event status or timestamp is invalid"
        if event.actor not in ACTOR_VALUES or not event.event_type or not event.summary:
            return DiagnosticStatus.FAIL, "event identity or content is invalid"
        if previous is not None and source is not previous:
            return DiagnosticStatus.FAIL, "event status continuity is broken"
        if previous_time is not None and observed < previous_time:
            return DiagnosticStatus.FAIL, "event ordering is not monotonic"
        if (source is not None and target not in ALLOWED_TRANSITIONS.get(source, set())
                and not _valid_recorded_observation(event, session)):
            return DiagnosticStatus.FAIL, "event transition is not legal"
        expected_type = f"{source.value if source else 'NONE'}_TO_{target.value}"
        if event.event_type != expected_type:
            return DiagnosticStatus.FAIL, "event transition label is inconsistent"
        previous, previous_time = target, observed
    if previous is not session.status:
        return DiagnosticStatus.FAIL, "session status does not match lineage tail"
    return DiagnosticStatus.PASS, "session event lineage is valid"


def _assessment_event_refs(event, session: Session) -> set[str]:
    references = set()
    for reference in event.evidence_refs:
        if not isinstance(reference, str):
            continue
        path = Path(reference)
        if (session.session_id in path.parts and path.name.startswith("assessment")
                and path.name.endswith(".json")):
            references.add(path.name)
    return references


def _valid_assessment_event(event, session: Session) -> bool:
    return (
        event.operation_id == f"assessment:{session.session_id}"
        and event.actor == "DIRECTOR"
        and event.from_status in {"READY", "RETRY_REQUIRED"}
        and event.to_status in {"READY", "BLOCKED"}
        and len(_assessment_event_refs(event, session)) == 1
    )


def _valid_recorded_observation(event, session: Session) -> bool:
    source, target = event.from_status, event.to_status
    if source == target == "READY":
        if _valid_assessment_event(event, session):
            return True
        if event.actor == "DIRECTOR" and event.operation_id.startswith("objective-plan:"):
            expected = event.operation_id.removeprefix("objective-plan:")
            return (len(expected) == 64 and all(char in "0123456789abcdef" for char in expected)
                    and any(Path(ref).name == f"objective-plan-{expected}.json"
                            for ref in event.evidence_refs if isinstance(ref, str)))
        return (
            event.actor == "RELEASE_MANAGER"
            and event.operation_id.startswith((
                "external-advance:", "external-plan-recovery:",
            ))
            and any(Path(ref).name == f"{event.operation_id.split(':', 1)[1]}.json"
                    for ref in event.evidence_refs if isinstance(ref, str))
        )
    if source == "BLOCKED" and target == "READY":
        return (
            event.actor == "RELEASE_MANAGER"
            and event.operation_id.startswith("external-advance:")
            and any(Path(ref).name == f"{event.operation_id.split(':', 1)[1]}.json"
                    for ref in event.evidence_refs if isinstance(ref, str))
        )
    return False


def doctor(session: Session, store: SessionStore, binding: WorkspaceTrust | None = None,
           *, repository_root: str | None = None, origin_url: str | None = None,
           target_sha: str | None = None, project: Project | None = None) -> list[DoctorFinding]:
    """Return observational diagnostics.  Findings never change session state."""
    findings: list[DoctorFinding] = []
    if (
        binding is None
        or project is None
        or repository_root is None
        or origin_url is None
        or target_sha is None
    ):
        findings.append(DoctorFinding("workspace-trust", DiagnosticStatus.UNKNOWN,
                                      "workspace binding evidence is missing"))
    else:
        status, issues = verify_workspace(project, binding, repository_root=repository_root,
                                          origin_url=origin_url, target_sha=target_sha)
        findings.append(
            DoctorFinding("workspace-trust", status, "; ".join(issues) or "binding matches")
        )
    for name, digest in sorted(session.artifact_hashes.items()):
        try:
            store._path(session.session_id)
            directory = store.ensure_safe_path(store.artifacts_dir / session.session_id)
            if directory.is_symlink() or not directory.is_dir():
                raise SessionStoreError("artifact directory is unsafe")
            candidates = []
            for path in directory.glob("*.json"):
                safe_path = store.ensure_safe_path(path)
                if safe_path.parent != directory or safe_path.is_symlink():
                    raise SessionStoreError("artifact path is unsafe")
                if store.artifact_hash(str(safe_path)) == digest:
                    candidates.append(safe_path)
        except (OSError, SessionStoreError):
            candidates = []
        verified_external = False
        verified_objective = False
        if project is not None and digest:
            if name.rsplit(":", 1)[-1] == "external_advancement":
                verified_external = _verify_external_advancement_hash(
                    session, store, project, digest,
                )
            elif name.rsplit(":", 1)[-1] == "objective_binding":
                verified_objective = _verify_objective_binding_hash(
                    session, store, project, digest,
                )
        matched = bool(candidates) or verified_external or verified_objective
        findings.append(
            DoctorFinding(
                f"artifact:{name}",
                DiagnosticStatus.PASS if matched else DiagnosticStatus.FAIL,
                "hash matches persisted artifact"
                if candidates
                else "hash matches verified external advancement"
                if verified_external
                else "hash matches signed Objective authority"
                if verified_objective
                else "artifact hash is missing or stale",
            )
        )
    if not session.events:
        findings.append(
            DoctorFinding(
                "recovery-lineage", DiagnosticStatus.UNKNOWN, "session event lineage is empty"
            )
        )
    else:
        status, detail = validate_recovery_lineage(session)
        findings.append(DoctorFinding("recovery-lineage", status, detail))
    return findings


def _verify_external_advancement_hash(session, store, project, digest: str) -> bool:
    """Resolve external-advance hashes through their signed canonical store."""
    from .external_advancement import (
        ExternalAdvancementStore,
        verify_external_advancement,
    )

    external_store = ExternalAdvancementStore(store.state_dir)
    directory = external_store.root / project.project_id
    if directory.is_symlink() or not directory.is_dir():
        return False
    matches = []
    try:
        paths = sorted(directory.glob("*.json"))
        if len(paths) > _MAX_ARCHIVE_FILES:
            return False
        for path in paths:
            if path.is_symlink():
                continue
            item = external_store.get(project.project_id, path.stem)
            if (item is not None and item.evidence_hash == digest
                    and item.session_id == session.session_id):
                verify_external_advancement(
                    item, project, project.repository_root, require_current_target=False,
                )
                matches.append(item)
    except (OSError, TypeError, ValueError):
        return False
    return len(matches) == 1


def _verify_objective_binding_hash(session, store, project, digest: str) -> bool:
    """Resolve historical Objective hashes only from signed generation artifacts."""
    from .authority_context import AuthorityContext, AuthorityContextError
    from .authority_generation import (
        AuthorityGenerationError,
        AuthorityGenerationStore,
        GenerationStatus,
    )

    generations = AuthorityGenerationStore(store.state_dir)
    directory = generations._directory(project.project_id)
    if directory.is_symlink() or not directory.is_dir():
        return False
    matches = 0
    try:
        paths = sorted(directory.glob("generation-*.json"))
        if len(paths) > _MAX_ARCHIVE_FILES:
            return False
        for path in paths:
            if (path.is_symlink()
                    or not re.fullmatch(r"generation-[1-9][0-9]*\.json", path.name)):
                continue
            generation_id = path.stem
            generation = generations.load(project.project_id, generation_id)
            if generation.status not in {GenerationStatus.ACTIVE, GenerationStatus.SUPERSEDED}:
                continue
            component = next((item for item in generation.components
                              if item.name == "objective_acceptance"), None)
            if component is None or component.artifact_hash != digest:
                continue
            artifacts = AuthorityContext._verify_artifacts(
                generation, artifact_root=generations.root, artifacts=None,
            )
            objective = artifacts.get("objective_acceptance")
            if (isinstance(objective, dict)
                    and objective.get("session_id") == session.session_id):
                matches += 1
    except (AuthorityContextError, AuthorityGenerationError, OSError, TypeError, ValueError):
        return False
    return matches > 0


def build_evidence_archive(session: Session, store: SessionStore) -> dict[str, Any]:
    """Build a deterministic, bounded, secret-safe archive manifest."""
    try:
        store._path(session.session_id)
        directory = store.ensure_safe_path(store.artifacts_dir / session.session_id)
    except (OSError, SessionStoreError, ValueError) as exc:
        raise ResilienceError("evidence archive session path is unsafe") from exc
    if directory.is_symlink() or not directory.is_dir():
        raise ResilienceError("evidence archive directory is unsafe or missing")
    try:
        files = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise ResilienceError("evidence archive directory is unavailable") from exc
    if len(files) > _MAX_ARCHIVE_FILES:
        raise ResilienceError("evidence archive exceeds file limit")
    entries = []
    total = 0
    for path in files:
        try:
            safe_path = store.ensure_safe_path(path)
            if safe_path.parent != directory or safe_path.is_symlink():
                raise ResilienceError("evidence archive artifact path is unsafe")
            descriptor = os.open(safe_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            stat = os.fstat(descriptor)
            if not os.path.isfile(safe_path) or stat.st_size > _MAX_ARCHIVE_BYTES - total:
                raise ResilienceError("evidence archive exceeds byte limit")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(_MAX_ARCHIVE_BYTES - total + 1)
        except (OSError, SessionStoreError) as exc:
            raise ResilienceError("evidence archive artifact path is unsafe") from exc
        finally:
            if "descriptor" in locals() and descriptor >= 0:
                os.close(descriptor)
        total += len(raw)
        if len(raw) > _MAX_ARCHIVE_BYTES - (total - len(raw)):
            raise ResilienceError("evidence archive exceeds byte limit")
        if _SECRET_SHAPED.search(raw.decode("utf-8", errors="replace")):
            raise ResilienceError("secret-shaped evidence cannot be archived")
        entries.append(
            {"name": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        )
    payload = {
        "schema_version": "1.0",
        "session_id": session.session_id,
        "project_id": session.project_id,
        "target_sha": session.base_sha,
        "scorecard": derive_scorecard(session).to_dict(),
        "artifacts": entries,
    }
    payload["archive_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload
