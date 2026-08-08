"""Immutable, deterministic merge-gate decision records.

E6-T1 defines the evidence contract only.  Delivery code must not infer
authorization from an unvalidated mapping or from a reviewer opinion.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from .risk_models import risk_from_dict


class MergeValidationError(ValueError):
    """Raised when merge evidence or a decision is invalid."""


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    STALE = "STALE"
    CONTRADICTORY = "CONTRADICTORY"
    UNKNOWN = "UNKNOWN"


class RiskClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class DecisionStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"


class AuthorizationStatus(StrEnum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZED = "AUTHORIZED"


_ID = re.compile(r"^[a-z][a-z0-9-]{2,127}$")
_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT = 4000
_MAX_ITEMS = 200
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]")


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Return one stable, strict JSON representation."""
    if not isinstance(value, Mapping):
        raise MergeValidationError("canonical value must be an object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MergeValidationError("canonical value is not serializable") from exc


def canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class GateEvidence:
    """One bounded observation consumed by the merge policy engine."""

    name: str
    status: GateStatus
    evidence_refs: tuple[str, ...]
    observed_at: str = ""
    freshness: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "observed_at": self.observed_at,
            "freshness": self.freshness,
            "detail": self.detail,
        }

    def validate(self) -> None:
        _text("gate name", self.name, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
        if not isinstance(self.status, GateStatus):
            raise MergeValidationError(f"gate {self.name} status is invalid")
        _strings("evidence_refs", self.evidence_refs, allow_empty=False)
        _text("observed_at", self.observed_at, allow_empty=True)
        if self.observed_at:
            try:
                observed = datetime.fromisoformat(self.observed_at)
            except ValueError as exc:
                raise MergeValidationError("observed_at is invalid") from exc
            if observed.tzinfo is None or observed > datetime.now(UTC):
                raise MergeValidationError("observed_at is invalid")
        _text("freshness", self.freshness, allow_empty=True)
        _text("detail", self.detail, allow_empty=True)


@dataclass(frozen=True)
class MergeDecision:
    """Integrity-bound result of one deterministic gate aggregation."""

    schema_version: str
    decision_id: str
    project_id: str
    task_id: str
    base_sha: str
    delivery_sha: str
    risk_class: RiskClass
    gates: tuple[GateEvidence, ...]
    policy_id: str
    policy_version: str
    constitution_id: str
    decision_status: DecisionStatus
    authorization_status: AuthorizationStatus
    blocking_reasons: tuple[str, ...]
    evidence_hash: str
    integrity_hash: str
    expiry: str = ""
    policy_hash: str = ""
    risk_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "base_sha": self.base_sha,
            "delivery_sha": self.delivery_sha,
            "risk_class": self.risk_class.value,
            "gates": [gate.to_dict() for gate in self.gates],
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "constitution_id": self.constitution_id,
            "decision_status": self.decision_status.value,
            "authorization_status": self.authorization_status.value,
            "blocking_reasons": list(self.blocking_reasons),
            "evidence_hash": self.evidence_hash,
            "integrity_hash": self.integrity_hash,
            "expiry": self.expiry,
            "policy_hash": self.policy_hash,
            "risk_assessment": self.risk_assessment,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise MergeValidationError("schema_version must be 1.0")
        for label, value in (
            ("decision_id", self.decision_id),
            ("project_id", self.project_id),
            ("task_id", self.task_id),
            ("policy_id", self.policy_id),
            ("policy_version", self.policy_version),
            ("constitution_id", self.constitution_id),
        ):
            _text(label, value)
        if not _ID.fullmatch(self.decision_id):
            raise MergeValidationError("decision_id is invalid")
        if not _SHA.fullmatch(self.base_sha) or not _SHA.fullmatch(self.delivery_sha):
            raise MergeValidationError("revision identity is invalid")
        if not isinstance(self.risk_class, RiskClass):
            raise MergeValidationError("risk_class is invalid")
        if not isinstance(self.decision_status, DecisionStatus):
            raise MergeValidationError("decision_status is invalid")
        if not isinstance(self.authorization_status, AuthorizationStatus):
            raise MergeValidationError("authorization_status is invalid")
        if not self.gates or len(self.gates) > _MAX_ITEMS:
            raise MergeValidationError("gates must contain 1 to 200 items")
        names: set[str] = set()
        for gate in self.gates:
            if not isinstance(gate, GateEvidence):
                raise MergeValidationError("gate is invalid")
            gate.validate()
            if gate.name in names:
                raise MergeValidationError("gate names must be unique")
            names.add(gate.name)
        _strings("blocking_reasons", self.blocking_reasons, allow_empty=True)
        if not _HEX.fullmatch(self.evidence_hash) or not _HEX.fullmatch(self.integrity_hash):
            raise MergeValidationError("decision hashes are invalid")
        _text("expiry", self.expiry, allow_empty=True)
        if self.policy_hash and not _HEX.fullmatch(self.policy_hash):
            raise MergeValidationError("policy hash is invalid")
        if self.risk_assessment is not None:
            try:
                assessment = risk_from_dict(self.risk_assessment)
            except (TypeError, ValueError) as exc:
                raise MergeValidationError("risk assessment is invalid") from exc
            if assessment.project_id != self.project_id or assessment.task_id != self.task_id:
                raise MergeValidationError("risk assessment identity does not match decision")
            if RiskClass(assessment.level.name) is not self.risk_class:
                raise MergeValidationError("risk assessment does not match decision risk")
        if self.decision_status is DecisionStatus.BLOCKED:
            if self.authorization_status is AuthorizationStatus.AUTHORIZED:
                raise MergeValidationError("blocked decisions cannot authorize")
            if not self.blocking_reasons:
                raise MergeValidationError("blocked decisions need reasons")
        if self.authorization_status is AuthorizationStatus.AUTHORIZED:
            if self.decision_status is not DecisionStatus.ELIGIBLE:
                raise MergeValidationError("only eligible decisions can authorize")
            if self.blocking_reasons:
                raise MergeValidationError("authorized decisions cannot have blockers")

    def integrity_payload(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("integrity_hash", None)
        return value

    def verify_integrity(self) -> bool:
        return canonical_hash(self.integrity_payload()) == self.integrity_hash


def decision_from_dict(payload: Mapping[str, Any]) -> MergeDecision:
    """Parse an exact serialized decision and verify its shape and integrity."""
    required = {
        "schema_version", "decision_id", "project_id", "task_id", "base_sha",
        "delivery_sha", "risk_class", "gates", "policy_id", "policy_version",
        "constitution_id", "decision_status", "authorization_status",
        "blocking_reasons", "evidence_hash", "integrity_hash", "expiry",
        "policy_hash",
        "risk_assessment",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise MergeValidationError("decision schema is missing or contains unknown fields")
    try:
        decision = MergeDecision(
            schema_version=payload["schema_version"],
            decision_id=payload["decision_id"],
            project_id=payload["project_id"],
            task_id=payload["task_id"],
            base_sha=payload["base_sha"],
            delivery_sha=payload["delivery_sha"],
            risk_class=RiskClass(payload["risk_class"]),
            gates=tuple(
                GateEvidence(
                    name=item["name"],
                    status=GateStatus(item["status"]),
                    evidence_refs=tuple(item["evidence_refs"]),
                    observed_at=item.get("observed_at", ""),
                    freshness=item.get("freshness", ""),
                    detail=item.get("detail", ""),
                )
                for item in payload["gates"]
            ),
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            constitution_id=payload["constitution_id"],
            decision_status=DecisionStatus(payload["decision_status"]),
            authorization_status=AuthorizationStatus(payload["authorization_status"]),
            blocking_reasons=tuple(payload["blocking_reasons"]),
            evidence_hash=payload["evidence_hash"],
            integrity_hash=payload["integrity_hash"],
            expiry=payload["expiry"],
            policy_hash=payload["policy_hash"],
            risk_assessment=payload["risk_assessment"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MergeValidationError(f"invalid decision structure: {exc}") from exc
    decision.validate()
    if not decision.verify_integrity():
        raise MergeValidationError("decision integrity hash does not match content")
    return decision


def _text(label: str, value: Any, *, allow_empty: bool = False, pattern: str | None = None) -> None:
    if not isinstance(value, str) or len(value) > _MAX_TEXT or _SECRET.search(value):
        raise MergeValidationError(f"{label} is invalid")
    if not allow_empty and not value.strip():
        raise MergeValidationError(f"{label} is invalid")
    if pattern and not re.fullmatch(pattern, value):
        raise MergeValidationError(f"{label} is invalid")


def _strings(label: str, values: Any, *, allow_empty: bool) -> None:
    if not isinstance(values, (tuple, list)) or len(values) > _MAX_ITEMS:
        raise MergeValidationError(f"{label} is invalid")
    if not allow_empty and not values:
        raise MergeValidationError(f"{label} is invalid")
    for value in values:
        _text(label, value)
