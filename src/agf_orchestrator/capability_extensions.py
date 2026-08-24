"""Strict evidence schemas for governed procedures, catalogs, and knowledge providers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .risk_models import RiskLevel


class CapabilityExtensionError(ValueError):
    """Raised when extension evidence is invalid or unsafe to consume."""


class InvocationPolicy(StrEnum):
    EXPLICIT_ONLY = "EXPLICIT_ONLY"
    AGF_SELECTABLE = "AGF_SELECTABLE"


class CandidateStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class KnowledgeTransport(StrEnum):
    STDIO = "STDIO"
    HTTP = "HTTP"
    SSE = "SSE"
    UNKNOWN = "UNKNOWN"


class KnowledgeMutability(StrEnum):
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class IntegrationStability(StrEnum):
    OFFICIAL = "OFFICIAL"
    UNOFFICIAL = "UNOFFICIAL"
    UNKNOWN = "UNKNOWN"


class PrivacyClassification(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    EXTERNAL_PUBLIC = "EXTERNAL_PUBLIC"
    EXTERNAL_PRIVATE = "EXTERNAL_PRIVATE"
    UNKNOWN = "UNKNOWN"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT_ID = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_SESSION_ID = re.compile(r"^session-[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_TEXT = 4000
_MAX_ITEMS = 200
_REQUIRED_TOOL_CHECKS = frozenset(
    {
        "official_documentation",
        "authentication",
        "limits",
        "license",
        "privacy",
        "stability",
        "policy",
    }
)


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise CapabilityExtensionError("timestamp is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CapabilityExtensionError("timestamp is not a real UTC instant") from exc


def _text(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise CapabilityExtensionError(f"{label} is invalid")
    if _SECRET.search(value):
        raise CapabilityExtensionError(f"{label} contains secret-shaped data")


def _id(label: str, value: Any, prefix: str | None = None) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CapabilityExtensionError(f"{label} is invalid")
    if prefix is not None and not value.startswith(prefix):
        raise CapabilityExtensionError(f"{label} is invalid")


def _texts(label: str, values: Any, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise CapabilityExtensionError(f"{label} is invalid")
    if nonempty and not values:
        raise CapabilityExtensionError(f"{label} must not be empty")
    if len(values) > _MAX_ITEMS:
        raise CapabilityExtensionError(f"{label} exceeds the bound")
    result = tuple(values)
    if len(set(result)) != len(result):
        raise CapabilityExtensionError(f"{label} must be unique")
    for value in result:
        _text(label, value)
    return result


def _path(value: str) -> None:
    _text("allowed path", value)
    if value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise CapabilityExtensionError("allowed path must be repository-relative")
    if ".." in value.split("/"):
        raise CapabilityExtensionError("allowed path must not traverse parents")


def _hash_payload(payload: dict[str, Any], hash_field: str) -> str:
    clean = dict(payload)
    clean[hash_field] = ""
    canonical = json.dumps(
        clean,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_hash(payload: dict[str, Any], field: str) -> None:
    value = payload.get(field)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CapabilityExtensionError(f"{field} is invalid")
    if value != _hash_payload(payload, field):
        raise CapabilityExtensionError(f"{field} does not match content")


def _validate_version(value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CapabilityExtensionError("profile_version is invalid")


def _validate_expiry(observed_at: str, expires_at: str | None, now: str | None) -> None:
    observed = _timestamp(observed_at)
    if expires_at is None:
        return
    expires = _timestamp(expires_at)
    if expires <= observed:
        raise CapabilityExtensionError("expires_at must be after observed_at")
    if now is not None and _timestamp(now) >= expires:
        raise CapabilityExtensionError("profile evidence is stale")


@dataclass(frozen=True)
class ProcedureProfile:
    schema_version: str
    procedure_id: str
    project_id: str
    profile_version: int
    capabilities: tuple[str, ...]
    max_risk: RiskLevel
    allowed_paths: tuple[str, ...]
    provider_requirements: tuple[str, ...]
    required_evidence: tuple[str, ...]
    invocation_policy: InvocationPolicy
    provenance_source: str
    observed_at: str
    expires_at: str | None
    profile_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "procedure_id": self.procedure_id,
            "project_id": self.project_id,
            "profile_version": self.profile_version,
            "capabilities": list(self.capabilities),
            "max_risk": self.max_risk.name,
            "allowed_paths": list(self.allowed_paths),
            "provider_requirements": list(self.provider_requirements),
            "required_evidence": list(self.required_evidence),
            "invocation_policy": self.invocation_policy.value,
            "provenance_source": self.provenance_source,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "profile_sha256": self.profile_sha256,
        }

    def validate(self, *, now: str | None = None) -> None:
        if self.schema_version != "1.0":
            raise CapabilityExtensionError("schema_version must be 1.0")
        _id("procedure_id", self.procedure_id, "procedure-")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise CapabilityExtensionError("project_id is invalid")
        _validate_version(self.profile_version)
        _texts("capabilities", self.capabilities, nonempty=True)
        if not isinstance(self.max_risk, RiskLevel):
            raise CapabilityExtensionError("max_risk is invalid")
        for value in _texts("allowed_paths", self.allowed_paths, nonempty=True):
            _path(value)
        _texts("provider_requirements", self.provider_requirements)
        _texts("required_evidence", self.required_evidence, nonempty=True)
        if not isinstance(self.invocation_policy, InvocationPolicy):
            raise CapabilityExtensionError("invocation_policy is invalid")
        _text("provenance_source", self.provenance_source)
        _validate_expiry(self.observed_at, self.expires_at, now)
        _validate_hash(self.to_dict(), "profile_sha256")


@dataclass(frozen=True)
class ProcedureSelection:
    schema_version: str
    selection_id: str
    project_id: str
    session_id: str
    procedure_id: str
    procedure_profile_sha256: str
    required_capabilities: tuple[str, ...]
    selected_at: str
    selection_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selection_id": self.selection_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "procedure_id": self.procedure_id,
            "procedure_profile_sha256": self.procedure_profile_sha256,
            "required_capabilities": list(self.required_capabilities),
            "selected_at": self.selected_at,
            "selection_sha256": self.selection_sha256,
        }

    def validate(self, profile: ProcedureProfile | None = None) -> None:
        if self.schema_version != "1.0":
            raise CapabilityExtensionError("schema_version must be 1.0")
        _id("selection_id", self.selection_id, "selection-")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise CapabilityExtensionError("project_id is invalid")
        if not _SESSION_ID.fullmatch(self.session_id):
            raise CapabilityExtensionError("session_id is invalid")
        _id("procedure_id", self.procedure_id, "procedure-")
        if not _SHA256.fullmatch(self.procedure_profile_sha256):
            raise CapabilityExtensionError("procedure_profile_sha256 is invalid")
        _texts("required_capabilities", self.required_capabilities, nonempty=True)
        _timestamp(self.selected_at)
        _validate_hash(self.to_dict(), "selection_sha256")
        if profile is None:
            return
        profile.validate()
        binding = (profile.project_id, profile.procedure_id)
        if binding != (self.project_id, self.procedure_id):
            raise CapabilityExtensionError("procedure selection binding does not match profile")
        if profile.profile_sha256 != self.procedure_profile_sha256:
            raise CapabilityExtensionError("procedure selection profile hash does not match")
        if not set(self.required_capabilities).issubset(profile.capabilities):
            raise CapabilityExtensionError("procedure does not satisfy required capabilities")


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    status: CheckStatus
    evidence_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence_ref": self.evidence_ref,
        }

    def validate(self) -> None:
        _text("verification check name", self.name)
        if not isinstance(self.status, CheckStatus):
            raise CapabilityExtensionError("verification check status is invalid")
        _text("verification evidence_ref", self.evidence_ref)


@dataclass(frozen=True)
class ToolCandidate:
    schema_version: str
    candidate_id: str
    project_id: str
    capability: str
    endpoint_label: str
    catalog_source: str
    status: CandidateStatus
    checks: tuple[VerificationCheck, ...]
    observed_at: str
    candidate_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "capability": self.capability,
            "endpoint_label": self.endpoint_label,
            "catalog_source": self.catalog_source,
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
            "observed_at": self.observed_at,
            "candidate_sha256": self.candidate_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise CapabilityExtensionError("schema_version must be 1.0")
        _id("candidate_id", self.candidate_id, "candidate-")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise CapabilityExtensionError("project_id is invalid")
        _text("capability", self.capability)
        _text("endpoint_label", self.endpoint_label)
        _text("catalog_source", self.catalog_source)
        if not isinstance(self.status, CandidateStatus):
            raise CapabilityExtensionError("candidate status is invalid")
        if not isinstance(self.checks, (tuple, list)) or len(self.checks) > _MAX_ITEMS:
            raise CapabilityExtensionError("checks are invalid")
        names: set[str] = set()
        for check in self.checks:
            if not isinstance(check, VerificationCheck):
                raise CapabilityExtensionError("verification check is invalid")
            check.validate()
            if check.name in names:
                raise CapabilityExtensionError("verification check names must be unique")
            names.add(check.name)
        all_pass = all(item.status is CheckStatus.PASS for item in self.checks)
        if self.status is CandidateStatus.VERIFIED:
            if names != _REQUIRED_TOOL_CHECKS or not all_pass:
                raise CapabilityExtensionError(
                    "verified candidate requires all mandatory checks to pass"
                )
        _timestamp(self.observed_at)
        _validate_hash(self.to_dict(), "candidate_sha256")

    def require_verified(self) -> None:
        self.validate()
        if self.status is not CandidateStatus.VERIFIED:
            raise CapabilityExtensionError("tool candidate is not verified")


@dataclass(frozen=True)
class KnowledgeProviderProfile:
    schema_version: str
    knowledge_provider_id: str
    project_id: str
    profile_version: int
    transport: KnowledgeTransport
    capabilities: tuple[str, ...]
    requires_credentials: bool
    requires_authenticated_session: bool
    network_required: bool
    browser_automation: bool
    privacy_classification: PrivacyClassification
    privacy_review_required: bool
    mutability: KnowledgeMutability
    stability: IntegrationStability
    provenance_source: str
    observed_at: str
    expires_at: str | None
    profile_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "knowledge_provider_id": self.knowledge_provider_id,
            "project_id": self.project_id,
            "profile_version": self.profile_version,
            "transport": self.transport.value,
            "capabilities": list(self.capabilities),
            "requires_credentials": self.requires_credentials,
            "requires_authenticated_session": self.requires_authenticated_session,
            "network_required": self.network_required,
            "browser_automation": self.browser_automation,
            "privacy_classification": self.privacy_classification.value,
            "privacy_review_required": self.privacy_review_required,
            "mutability": self.mutability.value,
            "stability": self.stability.value,
            "provenance_source": self.provenance_source,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "profile_sha256": self.profile_sha256,
        }

    def validate(self, *, now: str | None = None) -> None:
        if self.schema_version != "1.0":
            raise CapabilityExtensionError("schema_version must be 1.0")
        _id("knowledge_provider_id", self.knowledge_provider_id, "knowledge-")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise CapabilityExtensionError("project_id is invalid")
        _validate_version(self.profile_version)
        if (
            not isinstance(self.transport, KnowledgeTransport)
            or self.transport is KnowledgeTransport.UNKNOWN
        ):
            raise CapabilityExtensionError("knowledge transport must be known")
        _texts("capabilities", self.capabilities, nonempty=True)
        bools = (
            ("requires_credentials", self.requires_credentials),
            ("requires_authenticated_session", self.requires_authenticated_session),
            ("network_required", self.network_required),
            ("browser_automation", self.browser_automation),
            ("privacy_review_required", self.privacy_review_required),
        )
        for name, value in bools:
            if not isinstance(value, bool):
                raise CapabilityExtensionError(f"{name} is invalid")
        if not isinstance(self.privacy_classification, PrivacyClassification):
            raise CapabilityExtensionError("privacy classification is invalid")
        if (
            not isinstance(self.mutability, KnowledgeMutability)
            or self.mutability is KnowledgeMutability.UNKNOWN
        ):
            raise CapabilityExtensionError("knowledge mutability must be known")
        if not isinstance(self.stability, IntegrationStability):
            raise CapabilityExtensionError("integration stability is invalid")
        _text("provenance_source", self.provenance_source)
        _validate_expiry(self.observed_at, self.expires_at, now)
        _validate_hash(self.to_dict(), "profile_sha256")


ExtensionRecord = (
    ProcedureProfile | ProcedureSelection | ToolCandidate | KnowledgeProviderProfile
)


def seal(record: ExtensionRecord) -> ExtensionRecord:
    """Return an immutable record with its content hash populated."""
    if isinstance(record, ProcedureProfile):
        digest = _hash_payload(record.to_dict(), "profile_sha256")
        return replace(record, profile_sha256=digest)
    if isinstance(record, ProcedureSelection):
        digest = _hash_payload(record.to_dict(), "selection_sha256")
        return replace(record, selection_sha256=digest)
    if isinstance(record, ToolCandidate):
        digest = _hash_payload(record.to_dict(), "candidate_sha256")
        return replace(record, candidate_sha256=digest)
    if isinstance(record, KnowledgeProviderProfile):
        digest = _hash_payload(record.to_dict(), "profile_sha256")
        return replace(record, profile_sha256=digest)
    raise CapabilityExtensionError("unsupported record type")


def _exact_schema(payload: Any, expected: set[str], label: str) -> None:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise CapabilityExtensionError(
            f"{label} schema is missing or contains unknown fields"
        )


def procedure_profile_from_dict(payload: dict[str, Any]) -> ProcedureProfile:
    _exact_schema(payload, set(ProcedureProfile.__dataclass_fields__), "procedure profile")
    try:
        values = dict(payload)
        values["capabilities"] = tuple(payload["capabilities"])
        values["allowed_paths"] = tuple(payload["allowed_paths"])
        values["provider_requirements"] = tuple(payload["provider_requirements"])
        values["required_evidence"] = tuple(payload["required_evidence"])
        values["max_risk"] = RiskLevel[payload["max_risk"]]
        values["invocation_policy"] = InvocationPolicy(payload["invocation_policy"])
        record = ProcedureProfile(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityExtensionError(
            f"invalid procedure profile structure: {exc}"
        ) from exc
    record.validate()
    return record


def procedure_selection_from_dict(payload: dict[str, Any]) -> ProcedureSelection:
    _exact_schema(
        payload,
        set(ProcedureSelection.__dataclass_fields__),
        "procedure selection",
    )
    try:
        values = dict(payload)
        values["required_capabilities"] = tuple(payload["required_capabilities"])
        record = ProcedureSelection(**values)
    except (TypeError, ValueError) as exc:
        raise CapabilityExtensionError(
            f"invalid procedure selection structure: {exc}"
        ) from exc
    record.validate()
    return record


def tool_candidate_from_dict(payload: dict[str, Any]) -> ToolCandidate:
    _exact_schema(payload, set(ToolCandidate.__dataclass_fields__), "tool candidate")
    try:
        checks = tuple(
            VerificationCheck(
                item["name"],
                CheckStatus(item["status"]),
                item["evidence_ref"],
            )
            for item in payload["checks"]
        )
        values = dict(payload)
        values["status"] = CandidateStatus(payload["status"])
        values["checks"] = checks
        record = ToolCandidate(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityExtensionError(
            f"invalid tool candidate structure: {exc}"
        ) from exc
    record.validate()
    return record


def knowledge_provider_profile_from_dict(
    payload: dict[str, Any],
) -> KnowledgeProviderProfile:
    _exact_schema(
        payload,
        set(KnowledgeProviderProfile.__dataclass_fields__),
        "knowledge provider",
    )
    try:
        values = dict(payload)
        values["transport"] = KnowledgeTransport(payload["transport"])
        values["capabilities"] = tuple(payload["capabilities"])
        values["privacy_classification"] = PrivacyClassification(
            payload["privacy_classification"]
        )
        values["mutability"] = KnowledgeMutability(payload["mutability"])
        values["stability"] = IntegrationStability(payload["stability"])
        record = KnowledgeProviderProfile(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityExtensionError(
            f"invalid knowledge provider structure: {exc}"
        ) from exc
    record.validate()
    return record
