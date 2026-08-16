"""Durable, restart-safe causal findings for previously reproduced defects."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CausalFindingError(ValueError):
    """Raised when causal-finding evidence or a state transition is invalid."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def finding_identity(
    project_id: str, baseline_sha: str, symptom: str, reproduction: str
) -> str:
    """Return an id stable across retries, restarts, and provider outputs."""
    value = "\n".join((project_id, baseline_sha, symptom.strip(), reproduction.strip()))
    return "finding-" + hashlib.sha256(value.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class CausalFinding:
    schema_version: str
    finding_id: str
    project_id: str
    baseline_sha: str
    target_identity: str
    severity: str
    symptom: str
    reproduction: str
    observed_error: str
    evidence_refs: tuple[str, ...]
    proposal: dict[str, Any]
    reproduced_sha: str
    status: str
    created_at: str
    updated_at: str
    closure_reason: str | None = None
    closure_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "evidence_refs": list(self.evidence_refs),
            "closure_evidence": list(self.closure_evidence),
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise CausalFindingError("causal finding schema is invalid")
        if not self.finding_id.startswith("finding-"):
            raise CausalFindingError("causal finding identity is invalid")
        if not self.project_id.startswith("project-"):
            raise CausalFindingError("causal finding project binding is invalid")
        if len(self.baseline_sha) != 40 or len(self.reproduced_sha) != 40:
            raise CausalFindingError("causal finding SHA binding is invalid")
        if self.severity not in {"REQUIRED", "OPTIONAL"}:
            raise CausalFindingError("causal finding severity is invalid")
        if self.status not in {"ACTIVE", "CLOSED"}:
            raise CausalFindingError("causal finding status is invalid")
        if not self.symptom.strip() or not self.reproduction.strip():
            raise CausalFindingError("causal finding reproduction evidence is incomplete")
        if not self.evidence_refs:
            raise CausalFindingError("causal finding evidence references are required")
        if not isinstance(self.proposal, dict):
            raise CausalFindingError("causal finding proposal is invalid")
        if self.status == "CLOSED":
            if self.closure_reason not in {"DELIVERED", "NO_LONGER_REPRODUCIBLE"}:
                raise CausalFindingError("causal finding closure reason is invalid")
            if not self.closure_evidence:
                raise CausalFindingError("causal finding closure evidence is required")


def finding_from_dict(payload: dict[str, Any]) -> CausalFinding:
    required = set(CausalFinding.__dataclass_fields__)
    if set(payload) != required:
        raise CausalFindingError("causal finding schema is missing or contains unknown fields")
    values = dict(payload)
    values["evidence_refs"] = tuple(payload["evidence_refs"])
    values["closure_evidence"] = tuple(payload["closure_evidence"])
    finding = CausalFinding(**values)
    finding.validate()
    expected = finding_identity(
        finding.project_id, finding.baseline_sha, finding.symptom, finding.reproduction
    )
    if finding.finding_id != expected:
        raise CausalFindingError("causal finding identity does not match its evidence")
    return finding


class CausalFindingStore:
    """Project-namespaced atomic store with idempotent record and close operations."""

    def __init__(self, state_dir: str | Path | None = None):
        self.root = Path(state_dir or (Path.home() / ".agf-orchestrator")) / "causal-findings"

    def _path(self, project_id: str, finding_id: str) -> Path:
        if not project_id.startswith("project-") or not finding_id.startswith("finding-"):
            raise CausalFindingError("causal finding path identity is invalid")
        return self.root / project_id / f"{finding_id}.json"

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, project_id: str, finding_id: str) -> CausalFinding | None:
        path = self._path(project_id, finding_id)
        if not path.exists():
            return None
        return finding_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def active(self, project_id: str, *, baseline_sha: str | None = None) -> list[CausalFinding]:
        directory = self.root / project_id
        if not directory.exists():
            return []
        findings = []
        for path in sorted(directory.glob("finding-*.json")):
            finding = finding_from_dict(json.loads(path.read_text(encoding="utf-8")))
            if finding.status == "ACTIVE" and (
                baseline_sha is None or finding.reproduced_sha == baseline_sha
            ):
                findings.append(finding)
        return findings

    def record(
        self,
        *,
        project_id: str,
        baseline_sha: str,
        target_identity: str,
        symptom: str,
        reproduction: str,
        observed_error: str,
        evidence_refs: tuple[str, ...],
        proposal: dict[str, Any],
        severity: str = "REQUIRED",
        reproduced_sha: str | None = None,
    ) -> CausalFinding:
        reproduced_sha = reproduced_sha or baseline_sha
        finding = CausalFinding(
            "1.0",
            finding_identity(project_id, baseline_sha, symptom, reproduction),
            project_id,
            baseline_sha,
            target_identity,
            severity,
            symptom,
            reproduction,
            observed_error,
            tuple(sorted(set(evidence_refs))),
            proposal,
            reproduced_sha,
            "ACTIVE",
            _now(),
            _now(),
        )
        finding.validate()
        existing = self.get(project_id, finding.finding_id)
        if existing is not None:
            if existing.status == "CLOSED":
                raise CausalFindingError("closed causal finding cannot be reopened implicitly")
            if existing.proposal != finding.proposal:
                raise CausalFindingError("causal finding proposal changed for the same identity")
            return existing
        self._write(self._path(project_id, finding.finding_id), finding.to_dict())
        return finding

    def close(
        self,
        project_id: str,
        finding_id: str,
        *,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> CausalFinding:
        finding = self.get(project_id, finding_id)
        if finding is None:
            raise CausalFindingError("causal finding does not exist")
        if finding.status == "CLOSED":
            if finding.closure_reason != reason or finding.closure_evidence != evidence_refs:
                raise CausalFindingError("causal finding closure is not idempotent")
            return finding
        if reason not in {"DELIVERED", "NO_LONGER_REPRODUCIBLE"} or not evidence_refs:
            raise CausalFindingError("causal finding closure requires governed evidence")
        closed = replace(
            finding,
            status="CLOSED",
            updated_at=_now(),
            closure_reason=reason,
            closure_evidence=tuple(sorted(set(evidence_refs))),
        )
        closed.validate()
        self._write(self._path(project_id, finding_id), closed.to_dict())
        return closed
