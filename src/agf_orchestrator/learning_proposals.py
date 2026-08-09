"""Non-authoritative, reversible learning proposals for owner review."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class LearningProposalError(ValueError):
    """Raised when a proposal is invalid, protected, or out of scope."""


class ProposalStatus(StrEnum):
    OPEN = "OPEN"
    WITHDRAWN = "WITHDRAWN"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TARGET = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_PROTECTED = frozenset({
    "constitution", "owner_authority", "owner_key", "root_of_trust", "permissions",
    "risk_thresholds", "merge_policy", "objective", "provider_authority",
    "critical_protection",
})


@dataclass(frozen=True)
class LearningProposal:
    schema_version: str
    proposal_id: str
    project_id: str
    summary_id: str
    target: str
    rationale: str
    proposed_value: str
    reversible: bool
    status: ProposalStatus
    created_at: str
    content_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "project_id": self.project_id,
            "summary_id": self.summary_id,
            "target": self.target,
            "rationale": self.rationale,
            "proposed_value": self.proposed_value,
            "reversible": self.reversible,
            "status": self.status.value,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise LearningProposalError("schema_version must be 1.0")
        if not isinstance(self.proposal_id, str) or not _ID.fullmatch(self.proposal_id):
            raise LearningProposalError("proposal_id is invalid")
        if not isinstance(self.project_id, str) or not _PROJECT.fullmatch(self.project_id):
            raise LearningProposalError("project_id is invalid")
        if not isinstance(self.summary_id, str) or not self.summary_id.startswith("summary-"):
            raise LearningProposalError("summary_id is invalid")
        _bounded_text("target", self.target)
        target = self.target.strip().lower()
        if not _TARGET.fullmatch(target) or target in _PROTECTED:
            raise LearningProposalError("protected target cannot be proposed")
        _bounded_text("rationale", self.rationale)
        _bounded_text("proposed_value", self.proposed_value)
        if not isinstance(self.reversible, bool) or not self.reversible:
            raise LearningProposalError("proposal must be reversible")
        if not isinstance(self.status, ProposalStatus):
            raise LearningProposalError("status is invalid")
        if not isinstance(self.created_at, str) or not _TIMESTAMP.fullmatch(self.created_at):
            raise LearningProposalError("created_at is invalid")
        try:
            datetime.strptime(self.created_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise LearningProposalError("created_at is not a real UTC instant") from exc
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise LearningProposalError("content_sha256 is invalid")
        if self.content_sha256 != proposal_hash(self):
            raise LearningProposalError("content_sha256 does not match proposal")


class LearningProposalLedger:
    """Project-isolated storage with no method that applies a proposal."""

    def __init__(self, project_id: str) -> None:
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise LearningProposalError("project_id is invalid")
        self.project_id = project_id
        self._records: dict[str, LearningProposal] = {}

    def record(self, proposal: LearningProposal) -> bool:
        proposal.validate()
        if proposal.project_id != self.project_id:
            raise LearningProposalError("proposal project binding does not match ledger")
        previous = self._records.get(proposal.proposal_id)
        if previous is not None:
            if previous.content_sha256 == proposal.content_sha256:
                return False
            raise LearningProposalError("conflicting proposal is rejected")
        self._records[proposal.proposal_id] = proposal
        return True

    def withdraw(self, proposal_id: str) -> LearningProposal:
        current = self._records.get(proposal_id)
        if current is None:
            raise LearningProposalError("proposal is not recorded")
        withdrawn = LearningProposal(
            **{**current.__dict__, "status": ProposalStatus.WITHDRAWN, "content_sha256": "0" * 64}
        )
        withdrawn = LearningProposal(
            **{**withdrawn.__dict__, "content_sha256": proposal_hash(withdrawn)}
        )
        self._records[proposal_id] = withdrawn
        return withdrawn

    def get(self, proposal_id: str) -> LearningProposal:
        try:
            return self._records[proposal_id]
        except KeyError as exc:
            raise LearningProposalError("proposal is not recorded") from exc

    def export_state(self) -> str:
        payload = {
            "schema_version": "1.0", "project_id": self.project_id,
            "records": [item.to_dict() for _, item in sorted(self._records.items())],
        }
        canonical = _canonical(payload)
        return json.dumps(
            {**payload, "state_sha256": _sha256(canonical)},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    @classmethod
    def from_state(cls, serialized: str) -> "LearningProposalLedger":
        try:
            payload = json.loads(serialized)
            state_hash = payload.pop("state_sha256")
            if _sha256(_canonical(payload)) != state_hash:
                raise LearningProposalError("proposal state hash is invalid")
            ledger = cls(payload["project_id"])
            for item in payload["records"]:
                ledger.record(proposal_from_dict(item))
            return ledger
        except LearningProposalError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LearningProposalError("proposal state is invalid") from exc


def proposal_hash(proposal: LearningProposal) -> str:
    payload = proposal.to_dict()
    payload["content_sha256"] = ""
    return _sha256(_canonical(payload))


def proposal_from_dict(payload: dict[str, Any]) -> LearningProposal:
    required = {
        "schema_version", "proposal_id", "project_id", "summary_id", "target", "rationale",
        "proposed_value", "reversible", "status", "created_at", "content_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise LearningProposalError("proposal schema is invalid")
    try:
        proposal = LearningProposal(
            payload["schema_version"], payload["proposal_id"], payload["project_id"],
            payload["summary_id"], payload["target"], payload["rationale"],
            payload["proposed_value"], payload["reversible"], ProposalStatus(payload["status"]),
            payload["created_at"], payload["content_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningProposalError("proposal structure is invalid") from exc
    proposal.validate()
    return proposal


def _bounded_text(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise LearningProposalError(f"{label} is invalid")
    if _SECRET.search(value):
        raise LearningProposalError(f"{label} contains secret-shaped data")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
