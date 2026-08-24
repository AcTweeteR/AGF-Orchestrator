"""Provider-neutral, version-bound technical documentation evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .capability_extensions import (
    CapabilityExtensionError,
    KnowledgeProviderProfile,
)
from .mcp_profiles import knowledge_provider_eligibility
from .remote_identity import RemoteIdentityError, canonical_remote_identity
from .session_store import SessionStore, SessionStoreError


class DocumentationError(ValueError):
    """Raised when documentation evidence is malformed or unsafe."""


class DocumentationStatus(StrEnum):
    VALID = "VALID"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    AMBIGUOUS_VERSION = "AMBIGUOUS_VERSION"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    FUTURE_DATED = "FUTURE_DATED"
    FRESHNESS_UNKNOWN = "FRESHNESS_UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    MALFORMED = "MALFORMED"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    DEPENDENCY_MISMATCH = "DEPENDENCY_MISMATCH"
    TOPIC_MISMATCH = "TOPIC_MISMATCH"
    CONTRADICTORY = "CONTRADICTORY"
    PROVIDER_INELIGIBLE = "PROVIDER_INELIGIBLE"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"


class DocumentationFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class DocumentationOperation(StrEnum):
    RESOLVE_LIBRARY = "RESOLVE_LIBRARY"
    RETRIEVE_VERSIONED = "RETRIEVE_VERSIONED"
    RETRIEVE_TOPIC = "RETRIEVE_TOPIC"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(
    r"^\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET = re.compile(
    r"(?i)(api(?:[_-]|\s)?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_TEXT = 4000
_MAX_CITATIONS = 32
_MAX_EXCERPT = 2400
_MAX_CLAIMS = 32
_MAX_CLAIM_TEXT = 512
_CLOCK_SKEW_SECONDS = 0


def _text(label: str, value: Any, *, limit: int = _MAX_TEXT) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DocumentationError(f"{label} is invalid")
    if _SECRET.search(value):
        raise DocumentationError(f"{label} contains secret-shaped data")


def _optional_text(label: str, value: Any, *, limit: int = _MAX_TEXT) -> None:
    if value is not None:
        _text(label, value, limit=limit)


def _timestamp(label: str, value: Any) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise DocumentationError(f"{label} is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise DocumentationError(f"{label} is not a real UTC instant") from exc


def _version(label: str, value: Any) -> None:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise DocumentationError(f"{label} is invalid")


def _version_key(value: str) -> tuple[Any, ...]:
    match = re.fullmatch(
        r"(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?",
        value,
    )
    if not match:
        raise DocumentationError("version is invalid")
    numbers = tuple(int(item) for item in match.group(1).split("."))
    prerelease = match.group(2)
    if not prerelease:
        return numbers + (0,) * (4 - len(numbers)) + (1, ())
    identifiers = []
    for identifier in prerelease.split("."):
        if not identifier:
            raise DocumentationError("version prerelease is invalid")
        identifiers.append((0, int(identifier)) if identifier.isdigit() else (1, identifier))
    return numbers + (0,) * (4 - len(numbers)) + (0, tuple(identifiers))


def _version_identity(value: str) -> tuple[Any, str]:
    _version_key(value)
    match = re.fullmatch(
        r"\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?(?:\+([0-9A-Za-z.-]+))?",
        value,
    )
    if match is None:
        raise DocumentationError("version is invalid")
    return _version_key(value), match.group(1) or ""


def _canonical_package(value: Any) -> None:
    if not isinstance(value, str) or not _PACKAGE.fullmatch(value) or ".." in value:
        raise DocumentationError("package identity is invalid")


def _repository_identity(value: Any) -> None:
    if value is None:
        return
    _text("repository_id", value)
    try:
        candidate = value if "://" in value else f"https://{value}"
        if canonical_remote_identity(candidate) != value:
            raise DocumentationError("repository_id must be canonical")
    except (RemoteIdentityError, DocumentationError) as exc:
        if isinstance(exc, DocumentationError):
            raise
        raise DocumentationError("repository_id is malformed") from exc


def _sha(label: str, value: Any, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DocumentationError(f"{label} is invalid")


def _hash_payload(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned["evidence_sha256"] = ""
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _constraint_allows(constraint: str, version: str) -> bool | None:
    """Evaluate the small, deterministic constraint subset used by fixtures."""
    normalized = constraint.strip()
    version_value = _version_key(version)
    if normalized in {"", "*"}:
        return True
    if _VERSION.fullmatch(normalized):
        return _version_identity(version) == _version_identity(normalized)
    if normalized.startswith("^"):
        base = normalized[1:]
        _version("constraint version", base)
        base_value = _version_key(base)
        if len(base.split("-", 1)[0].split(".") ) > 3:
            return None
        numbers = base_value[:4]
        if numbers[0] > 0:
            upper = (numbers[0] + 1, 0, 0, 0, 1, ())
        elif numbers[1] > 0:
            upper = (0, numbers[1] + 1, 0, 0, 1, ())
        else:
            upper = (0, 0, numbers[2] + 1, 0, 1, ())
        return version_value >= base_value and version_value < upper
    if normalized.startswith("~"):
        base = normalized[1:]
        _version("constraint version", base)
        if len(base.split("-", 1)[0].split(".")) > 3:
            return None
        base_value = _version_key(base)
        return version_value >= base_value and version_value[:2] == base_value[:2]
    terms = tuple(term.strip() for term in normalized.split(","))
    for term in terms:
        match = re.fullmatch(r"(==|=|>=|<=|>|<)(.+)", term)
        if not match:
            return None
        operator, operand = match.groups()
        _version("constraint version", operand)
        operand_value = _version_key(operand)
        if operator in {"=", "=="} and _version_identity(version) != _version_identity(operand):
            return False
        if operator == ">=" and version_value < operand_value:
            return False
        if operator == "<=" and version_value > operand_value:
            return False
        if operator == ">" and version_value <= operand_value:
            return False
        if operator == "<" and version_value >= operand_value:
            return False
    return True


@dataclass(frozen=True)
class DependencyVersionEvidence:
    registry: str
    package_id: str
    declared_constraint: str
    locked_version: str | None
    resolved_version: str | None
    runtime_observed_version: str | None
    source: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,31}", self.registry):
            raise DocumentationError("dependency registry is invalid")
        _canonical_package(self.package_id)
        _text("declared_constraint", self.declared_constraint, limit=256)
        for label, value in (
            ("locked_version", self.locked_version),
            ("resolved_version", self.resolved_version),
            ("runtime_observed_version", self.runtime_observed_version),
        ):
            if value is not None:
                _version(label, value)
        _text("dependency source", self.source)
        _timestamp("dependency observed_at", self.observed_at)

    def demonstrated_version(self) -> str | None:
        self.validate()
        values = tuple(
            value
            for value in (
                self.locked_version,
                self.resolved_version,
                self.runtime_observed_version,
            )
            if value is not None
        )
        if not values:
            return None
        if len({_version_identity(value) for value in values}) != 1:
            raise DocumentationError("dependency version sources contradict")
        allowed = _constraint_allows(self.declared_constraint, values[0])
        if allowed is not True:
            raise DocumentationError("resolved dependency does not satisfy declared constraint")
        return values[0]

    def has_exact_declaration(self) -> bool:
        return bool(re.fullmatch(r"(?:==|=)?\d+(?:\.\d+){0,3}", self.declared_constraint))


@dataclass(frozen=True)
class DocumentationRequest:
    project_id: str
    repository_id: str | None
    revision_sha: str | None
    operation: DocumentationOperation
    dependency: DependencyVersionEvidence
    topic: str
    max_age_seconds: int

    def validate(self) -> None:
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise DocumentationError("project_id is invalid")
        _repository_identity(self.repository_id)
        if self.revision_sha is not None:
            _sha("revision_sha", self.revision_sha, _SHA1)
        if not isinstance(self.operation, DocumentationOperation):
            raise DocumentationError("operation is invalid")
        self.dependency.validate()
        _text("topic", self.topic, limit=512)
        if (
            not isinstance(self.max_age_seconds, int)
            or isinstance(self.max_age_seconds, bool)
            or self.max_age_seconds < 0
            or self.max_age_seconds > 31_536_000
        ):
            raise DocumentationError("max_age_seconds is invalid")
        if self.repository_id is None and self.revision_sha is not None:
            raise DocumentationError("revision requires repository binding")
        if self.operation in {
            DocumentationOperation.RETRIEVE_VERSIONED,
            DocumentationOperation.RETRIEVE_TOPIC,
        } and (self.repository_id is None or self.revision_sha is None):
            raise DocumentationError("retrieval requires repository and revision binding")


@dataclass(frozen=True)
class DocumentationCitation:
    source: str
    locator: str
    excerpt: str

    def to_dict(self) -> dict[str, str]:
        return self.__dict__.copy()

    def validate(self) -> None:
        _text("citation source", self.source)
        _text("citation locator", self.locator, limit=512)
        _text("citation excerpt", self.excerpt, limit=_MAX_EXCERPT)


@dataclass(frozen=True)
class DocumentationClaim:
    """Bounded normalized assertion; prose is never interpreted as a claim."""

    assertion_key: str
    assertion_value: str
    claim_sha256: str

    def to_dict(self) -> dict[str, str]:
        return self.__dict__.copy()

    def validate(self) -> None:
        _text("claim assertion_key", self.assertion_key, limit=_MAX_CLAIM_TEXT)
        _text("claim assertion_value", self.assertion_value, limit=_MAX_CLAIM_TEXT)
        _sha("claim_sha256", self.claim_sha256, _SHA256)
        unsigned = {
            "assertion_key": self.assertion_key,
            "assertion_value": self.assertion_value,
            "claim_sha256": "",
        }
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.claim_sha256 != expected:
            raise DocumentationError("claim_sha256 does not match content")


def seal_claim(assertion_key: str, assertion_value: str) -> DocumentationClaim:
    unsigned = {
        "assertion_key": assertion_key,
        "assertion_value": assertion_value,
        "claim_sha256": "",
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    claim = DocumentationClaim(assertion_key, assertion_value, digest)
    claim.validate()
    return claim


class DocumentationProvider(Protocol):
    """Provider contract: return bounded evidence, never authorize or mutate."""

    provider_id: str

    def retrieve(self, request: DocumentationRequest) -> "DocumentationEvidence":
        ...


@dataclass(frozen=True)
class DocumentationEvidence:
    schema_version: str
    evidence_id: str
    provider_id: str
    project_id: str
    repository_id: str | None
    revision_sha: str | None
    operation: DocumentationOperation
    dependency: DependencyVersionEvidence
    requested_topic: str
    returned_topic: str | None
    documentation_version: str | None
    documentation_source: str
    citations: tuple[DocumentationCitation, ...]
    claims: tuple[DocumentationClaim, ...]
    observed_at: str
    freshness: DocumentationFreshness
    status: DocumentationStatus
    evidence_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "provider_id": self.provider_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "revision_sha": self.revision_sha,
            "operation": self.operation.value,
            "dependency": self.dependency.to_dict(),
            "requested_topic": self.requested_topic,
            "returned_topic": self.returned_topic,
            "documentation_version": self.documentation_version,
            "documentation_source": self.documentation_source,
            "citations": [item.to_dict() for item in self.citations],
            "claims": [item.to_dict() for item in self.claims],
            "observed_at": self.observed_at,
            "freshness": self.freshness.value,
            "status": self.status.value,
            "evidence_sha256": self.evidence_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.evidence_id):
            raise DocumentationError("evidence identity is invalid")
        if not _ID.fullmatch(self.provider_id):
            raise DocumentationError("provider_id is invalid")
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise DocumentationError("project_id is invalid")
        _repository_identity(self.repository_id)
        if self.revision_sha is not None:
            _sha("revision_sha", self.revision_sha, _SHA1)
        if self.repository_id is None and self.revision_sha is not None:
            raise DocumentationError("revision requires repository binding")
        if self.operation in {
            DocumentationOperation.RETRIEVE_VERSIONED,
            DocumentationOperation.RETRIEVE_TOPIC,
        } and (self.repository_id is None or self.revision_sha is None):
            raise DocumentationError("retrieval requires repository and revision binding")
        if not isinstance(self.operation, DocumentationOperation):
            raise DocumentationError("operation is invalid")
        self.dependency.validate()
        _text("requested_topic", self.requested_topic, limit=512)
        _optional_text("returned_topic", self.returned_topic, limit=512)
        if self.documentation_version is not None:
            _version("documentation_version", self.documentation_version)
        _text("documentation_source", self.documentation_source)
        if len(self.citations) > _MAX_CITATIONS:
            raise DocumentationError("citations exceed the bound")
        for citation in self.citations:
            citation.validate()
        if sum(len(item.excerpt) for item in self.citations) > 16_000:
            raise DocumentationError("citation excerpts exceed the aggregate bound")
        if len(self.claims) > _MAX_CLAIMS:
            raise DocumentationError("claims exceed the bound")
        claim_keys = set()
        for claim in self.claims:
            claim.validate()
            if claim.assertion_key in claim_keys:
                raise DocumentationError("claim assertion keys must be unique")
            claim_keys.add(claim.assertion_key)
        _timestamp("observed_at", self.observed_at)
        if not isinstance(self.freshness, DocumentationFreshness):
            raise DocumentationError("freshness is invalid")
        if not isinstance(self.status, DocumentationStatus):
            raise DocumentationError("status is invalid")
        if self.status is DocumentationStatus.VALID:
            if (
                self.documentation_version is None
                or not self.returned_topic
                or not self.citations
                or not self.claims
            ):
                raise DocumentationError("valid evidence requires versioned citations")
            if self.freshness is not DocumentationFreshness.FRESH:
                raise DocumentationError("valid evidence requires fresh evidence")
        _sha("evidence_sha256", self.evidence_sha256, _SHA256)
        if self.evidence_sha256 != _hash_payload(self.to_dict()):
            raise DocumentationError("evidence_sha256 does not match content")

    def assess(self, request: DocumentationRequest, *, now: str) -> DocumentationStatus:
        self.validate()
        request.validate()
        _timestamp("assessment now", now)
        if self.project_id != request.project_id:
            return DocumentationStatus.PROJECT_MISMATCH
        if self.repository_id != request.repository_id:
            return DocumentationStatus.REPOSITORY_MISMATCH
        if self.revision_sha != request.revision_sha:
            return DocumentationStatus.REVISION_MISMATCH
        if self.operation is not request.operation:
            return DocumentationStatus.TOPIC_MISMATCH
        if self.dependency != request.dependency:
            return DocumentationStatus.DEPENDENCY_MISMATCH
        if (
            self.requested_topic != request.topic
            or self.returned_topic not in {None, request.topic}
        ):
            return DocumentationStatus.TOPIC_MISMATCH
        if self.status is not DocumentationStatus.VALID:
            return self.status
        try:
            project_version = request.dependency.demonstrated_version()
        except DocumentationError:
            return DocumentationStatus.CONTRADICTORY
        if project_version is None:
            return DocumentationStatus.AMBIGUOUS_VERSION
        if self.documentation_version is None:
            return DocumentationStatus.AMBIGUOUS_VERSION
        if _version_identity(self.documentation_version) != _version_identity(project_version):
            return DocumentationStatus.VERSION_MISMATCH
        if self.freshness is DocumentationFreshness.STALE:
            return DocumentationStatus.STALE
        if self.freshness is DocumentationFreshness.UNKNOWN:
            return DocumentationStatus.FRESHNESS_UNKNOWN
        observed = _timestamp("observed_at", self.observed_at)
        dependency_observed = _timestamp(
            "dependency observed_at", request.dependency.observed_at
        )
        current = _timestamp("assessment now", now)
        observed_age = (current - observed).total_seconds()
        dependency_age = (current - dependency_observed).total_seconds()
        if observed_age < -_CLOCK_SKEW_SECONDS or dependency_age < -_CLOCK_SKEW_SECONDS:
            return DocumentationStatus.FUTURE_DATED
        if observed_age > request.max_age_seconds:
            return DocumentationStatus.STALE
        if dependency_age > request.max_age_seconds:
            return DocumentationStatus.STALE
        return DocumentationStatus.VALID


def seal(evidence: DocumentationEvidence) -> DocumentationEvidence:
    value = DocumentationEvidence(**{**evidence.__dict__, "evidence_sha256": ""})
    value = DocumentationEvidence(
        **{**value.__dict__, "evidence_sha256": _hash_payload(value.to_dict())}
    )
    value.validate()
    return value


def evidence_from_dict(payload: dict[str, Any]) -> DocumentationEvidence:
    expected = set(DocumentationEvidence.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DocumentationError("documentation schema is missing or contains unknown fields")
    try:
        dependency = DependencyVersionEvidence(**payload["dependency"])
        evidence = DocumentationEvidence(
            payload["schema_version"], payload["evidence_id"], payload["provider_id"],
            payload["project_id"], payload["repository_id"], payload["revision_sha"],
            DocumentationOperation(payload["operation"]), dependency,
            payload["requested_topic"], payload["returned_topic"],
            payload["documentation_version"], payload["documentation_source"],
            tuple(DocumentationCitation(**item) for item in payload["citations"]),
            tuple(DocumentationClaim(**item) for item in payload["claims"]),
            payload["observed_at"], DocumentationFreshness(payload["freshness"]),
            DocumentationStatus(payload["status"]), payload["evidence_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DocumentationError("documentation structure is invalid") from exc
    evidence.validate()
    return evidence


def persist_evidence(
    store: SessionStore, session_id: str, evidence: DocumentationEvidence
) -> tuple[str, str]:
    evidence.validate()
    return store.write_artifact(
        session_id,
        f"documentation-{evidence.evidence_id}.json",
        json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
    )


def load_evidence(
    store: SessionStore, session_id: str, evidence_id: str
) -> DocumentationEvidence:
    if not _ID.fullmatch(evidence_id):
        raise DocumentationError("evidence_id is invalid")
    path = (
        store._path(session_id).parent.parent
        / "artifacts"
        / session_id
        / f"documentation-{evidence_id}.json"
    )
    try:
        path = store.ensure_safe_path(path)
        return evidence_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, SessionStoreError, json.JSONDecodeError, TypeError) as exc:
        raise DocumentationError("documentation evidence is unavailable") from exc


@dataclass(frozen=True)
class ProviderResolution:
    status: DocumentationStatus
    reason: str


def resolve_provider(
    profile: KnowledgeProviderProfile,
    *,
    project_id: str,
    now: str,
    available: bool | None,
    authenticated: bool | None,
    policy_authorized: bool | None,
    privacy_eligible: bool | None,
    network_allowed: bool | None,
    required: bool,
) -> ProviderResolution:
    try:
        profile.validate(now=now)
    except CapabilityExtensionError as exc:
        return ProviderResolution(DocumentationStatus.PROVIDER_INELIGIBLE, str(exc))
    if profile.project_id != project_id:
        return ProviderResolution(DocumentationStatus.PROJECT_MISMATCH, "profile project mismatch")
    if "documentation" not in profile.capabilities:
        return ProviderResolution(
            DocumentationStatus.PROVIDER_INELIGIBLE, "documentation capability is unsupported"
        )
    if profile.network_required and network_allowed is not True:
        return ProviderResolution(DocumentationStatus.NETWORK_BLOCKED, "network is not eligible")
    if profile.privacy_review_required and privacy_eligible is not True:
        return ProviderResolution(DocumentationStatus.PRIVACY_BLOCKED, "privacy is not eligible")
    eligibility = knowledge_provider_eligibility(
        profile,
        now=now,
        available=available,
        authenticated=authenticated,
        policy_authorized=policy_authorized,
        privacy_eligible=privacy_eligible,
    )
    if not eligibility.eligible:
        reason = eligibility.reason
        if available is not True:
            return ProviderResolution(DocumentationStatus.UNAVAILABLE, reason)
        return ProviderResolution(DocumentationStatus.PROVIDER_INELIGIBLE, reason)
    reason = "eligible" if required else "optional-eligible"
    return ProviderResolution(DocumentationStatus.VALID, reason)


def reconcile_evidence(
    evidence: tuple[DocumentationEvidence, ...],
    request: DocumentationRequest,
    *,
    now: str,
) -> DocumentationStatus:
    if not evidence:
        return DocumentationStatus.UNAVAILABLE
    for item in evidence:
        item.validate()
    statuses = {item.assess(request, now=now) for item in evidence}
    if statuses != {DocumentationStatus.VALID}:
        return next(iter(statuses)) if len(statuses) == 1 else DocumentationStatus.CONTRADICTORY
    first = evidence[0]
    identity = (
        first.project_id, first.repository_id, first.revision_sha,
        first.dependency, first.requested_topic,
    )
    for item in evidence[1:]:
        if (
            item.project_id, item.repository_id, item.revision_sha,
            item.dependency, item.requested_topic,
        ) != identity:
            return DocumentationStatus.CONTRADICTORY
        if item.documentation_version != first.documentation_version:
            return DocumentationStatus.CONTRADICTORY
        if item.returned_topic != first.returned_topic:
            return DocumentationStatus.CONTRADICTORY
        first_claims = {
            claim.assertion_key: (claim.assertion_value, claim.claim_sha256)
            for claim in first.claims
        }
        item_claims = {
            claim.assertion_key: (claim.assertion_value, claim.claim_sha256)
            for claim in item.claims
        }
        if item_claims != first_claims:
            return DocumentationStatus.CONTRADICTORY
    return DocumentationStatus.VALID


def latest_is_unsafe_for_project(
    latest: DocumentationEvidence, request: DocumentationRequest, *, now: str
) -> bool:
    """Deterministic pilot predicate: latest docs are unsafe when version-bound assessment fails."""
    return latest.assess(request, now=now) is not DocumentationStatus.VALID
