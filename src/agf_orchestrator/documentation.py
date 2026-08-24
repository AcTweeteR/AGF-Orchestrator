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
    r"^[0-9]{1,32}(?:\.[0-9]{1,32}){0,3}"
    r"(?:-[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?"
    r"(?:\+[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?$"
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_URI_USERINFO = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]{1,31}://[^/\s:@]+:[^/\s@]+@"
)
_SECRET = re.compile(
    r"(?i)(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?access[_-]?key[_-]?id|"
    r"secret\s+access\s+key|client\s+secret|private\s+key|"
    r"api(?:[_-]|\s)?key|token|secret|password|authorization)\s*[:=]|"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_TEXT = 4000
_MAX_CITATIONS = 32
_MAX_EXCERPT = 2400
_MAX_CLAIMS = 32
_MAX_CLAIM_TEXT = 512
_CLOCK_SKEW_SECONDS = 0
_MAX_VERSION_LENGTH = 256


def _text(label: str, value: Any, *, limit: int = _MAX_TEXT) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DocumentationError(f"{label} is invalid")
    if _SECRET.search(value) or _URI_USERINFO.search(value):
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
    if (
        not isinstance(value, str)
        or len(value) > _MAX_VERSION_LENGTH
        or not _VERSION.fullmatch(value)
    ):
        raise DocumentationError(f"{label} is invalid")
    _version_key(value)


def _version_key(value: str) -> tuple[Any, ...]:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_LENGTH:
        raise DocumentationError("version is invalid")
    match = re.fullmatch(
        r"([0-9]{1,32}(?:\.[0-9]{1,32}){0,3})"
        r"(?:-([0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*))?"
        r"(?:\+([0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*))?",
        value,
    )
    if not match:
        raise DocumentationError("version is invalid")
    try:
        numbers = tuple(int(item) for item in match.group(1).split("."))
    except (TypeError, ValueError) as exc:
        raise DocumentationError("version numeric component is invalid") from exc
    prerelease = match.group(2)
    if not prerelease:
        return numbers + (0,) * (4 - len(numbers)) + (1, ())
    identifiers = []
    for identifier in prerelease.split("."):
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise DocumentationError("version prerelease numeric identifier is invalid")
        if identifier.isdigit():
            try:
                numeric_identifier = int(identifier)
            except ValueError as exc:
                raise DocumentationError("version prerelease identifier is invalid") from exc
            identifiers.append((0, numeric_identifier))
        else:
            identifiers.append((1, identifier))
    return numbers + (0,) * (4 - len(numbers)) + (0, tuple(identifiers))


def _version_identity(value: str) -> tuple[Any, str]:
    _version_key(value)
    build = value.partition("+")[2]
    return _version_key(value), build


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


def _explicit_prerelease_cores(constraint: str) -> set[tuple[Any, ...]]:
    if constraint.startswith(("^", "~")):
        operands = (constraint[1:],)
    else:
        operands = tuple(
            match.group(2)
            for term in constraint.split(",")
            if (match := re.fullmatch(r"(==|=|>=|<=|>|<)(.+)", term.strip()))
        )
    cores = set()
    for operand in operands:
        _version("constraint version", operand)
        if "-" in operand.partition("+")[0]:
            cores.add(_version_key(operand)[:4])
    return cores


def _constraint_allows(constraint: str, version: str) -> bool | None:
    """Evaluate the small, deterministic constraint subset used by fixtures."""
    normalized = constraint.strip()
    version_value = _version_key(version)
    if normalized in {"", "*"}:
        return True
    if _VERSION.fullmatch(normalized):
        return _version_identity(version) == _version_identity(normalized)
    if "-" in version.partition("+")[0]:
        if _version_key(version)[:4] not in _explicit_prerelease_cores(normalized):
            return False
    if normalized.startswith("^"):
        base = normalized[1:]
        _version("constraint version", base)
        base_value = _version_key(base)
        components = len(base.split("-", 1)[0].split("."))
        if components > 3:
            return None
        numbers = base_value[:4]
        if numbers[0] > 0:
            upper = (numbers[0] + 1, 0, 0, 0, 1, ())
        elif components == 1:
            upper = (1, 0, 0, 0, 1, ())
        elif numbers[1] > 0:
            upper = (0, numbers[1] + 1, 0, 0, 1, ())
        elif components == 2:
            upper = (0, 1, 0, 0, 1, ())
        else:
            upper = (0, 0, numbers[2] + 1, 0, 1, ())
        return version_value >= base_value and version_value < upper
    if normalized.startswith("~"):
        base = normalized[1:]
        _version("constraint version", base)
        components = len(base.split("-", 1)[0].split("."))
        if components > 3:
            return None
        base_value = _version_key(base)
        numbers = base_value[:4]
        if components == 1:
            upper = (numbers[0] + 1, 0, 0, 0, 1, ())
        else:
            upper = (numbers[0], numbers[1] + 1, 0, 0, 1, ())
        return version_value >= base_value and version_value < upper
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
    provider_binding: "ProviderBinding | None" = None

    def validate(self) -> None:
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise DocumentationError("project_id is invalid")
        _repository_identity(self.repository_id)
        if self.revision_sha is not None:
            _sha("revision_sha", self.revision_sha, _SHA1)
        if not isinstance(self.operation, DocumentationOperation):
            raise DocumentationError("operation is invalid")
        self.dependency.validate()
        if self.provider_binding is not None:
            self.provider_binding.validate()
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


def citation_sha256(citation: DocumentationCitation) -> str:
    citation.validate()
    return hashlib.sha256(
        json.dumps(citation.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DocumentationClaim:
    """Bounded normalized assertion; prose is never interpreted as a claim."""

    assertion_key: str
    assertion_value: str
    claim_sha256: str
    citation_sha256s: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, str]:
        return {
            "assertion_key": self.assertion_key,
            "assertion_value": self.assertion_value,
            "claim_sha256": self.claim_sha256,
            "citation_sha256s": list(self.citation_sha256s),
        }

    def validate(self) -> None:
        _text("claim assertion_key", self.assertion_key, limit=_MAX_CLAIM_TEXT)
        _text("claim assertion_value", self.assertion_value, limit=_MAX_CLAIM_TEXT)
        _sha("claim_sha256", self.claim_sha256, _SHA256)
        if not self.citation_sha256s:
            raise DocumentationError("claim must cite supporting evidence")
        for citation_sha256 in self.citation_sha256s:
            _sha("claim citation_sha256", citation_sha256, _SHA256)
        unsigned = {
            "assertion_key": self.assertion_key,
            "assertion_value": self.assertion_value,
            "claim_sha256": "",
            "citation_sha256s": list(self.citation_sha256s),
        }
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.claim_sha256 != expected:
            raise DocumentationError("claim_sha256 does not match content")


def seal_claim(
    assertion_key: str,
    assertion_value: str,
    *,
    citation_sha256s: tuple[str, ...],
) -> DocumentationClaim:
    unsigned = {
        "assertion_key": assertion_key,
        "assertion_value": assertion_value,
        "claim_sha256": "",
        "citation_sha256s": list(citation_sha256s),
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    claim = DocumentationClaim(assertion_key, assertion_value, digest, citation_sha256s)
    claim.validate()
    return claim


class DocumentationProvider(Protocol):
    """Provider contract: return bounded evidence, never authorize or mutate."""

    provider_id: str

    def retrieve(self, request: DocumentationRequest) -> "DocumentationEvidence":
        ...


@dataclass(frozen=True)
class ProviderBinding:
    """Sealed identity of the provider eligibility decision used by evidence."""

    provider_id: str
    project_id: str
    profile_sha256: str
    decision_at: str
    expires_at: str | None
    available: bool
    authenticated: bool
    policy_authorized: bool
    privacy_eligible: bool | None
    network_allowed: bool | None
    binding_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "project_id": self.project_id,
            "profile_sha256": self.profile_sha256,
            "decision_at": self.decision_at,
            "expires_at": self.expires_at,
            "available": self.available,
            "authenticated": self.authenticated,
            "policy_authorized": self.policy_authorized,
            "privacy_eligible": self.privacy_eligible,
            "network_allowed": self.network_allowed,
            "binding_sha256": self.binding_sha256,
        }

    def validate(self, *, now: str | None = None) -> None:
        if not _ID.fullmatch(self.provider_id):
            raise DocumentationError("provider binding provider_id is invalid")
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise DocumentationError("provider binding project_id is invalid")
        _sha("provider binding profile_sha256", self.profile_sha256, _SHA256)
        decision = _timestamp("provider binding decision_at", self.decision_at)
        if now is not None and decision > _timestamp("provider binding now", now):
            raise DocumentationError("provider binding is future-dated")
        if self.expires_at is not None:
            expiry = _timestamp("provider binding expires_at", self.expires_at)
            if expiry <= _timestamp("provider binding decision_at", self.decision_at):
                raise DocumentationError("provider binding expiry is invalid")
            if now is not None and _timestamp("provider binding now", now) >= expiry:
                raise DocumentationError("provider binding has expired")
        for label, value in (
            ("available", self.available),
            ("authenticated", self.authenticated),
            ("policy_authorized", self.policy_authorized),
        ):
            if value is not True:
                raise DocumentationError(f"provider binding {label} is not eligible")
        unsigned = {**self.to_dict(), "binding_sha256": ""}
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.binding_sha256 != expected:
            raise DocumentationError("provider binding hash does not match content")


def _seal_provider_binding(
    profile: KnowledgeProviderProfile,
    *,
    now: str,
    available: bool,
    authenticated: bool,
    policy_authorized: bool,
    privacy_eligible: bool | None,
    network_allowed: bool | None,
) -> ProviderBinding:
    unsigned = {
        "provider_id": profile.knowledge_provider_id,
        "project_id": profile.project_id,
        "profile_sha256": profile.profile_sha256,
        "decision_at": now,
        "expires_at": profile.expires_at,
        "available": available,
        "authenticated": authenticated,
        "policy_authorized": policy_authorized,
        "privacy_eligible": privacy_eligible,
        "network_allowed": network_allowed,
        "binding_sha256": "",
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    binding = ProviderBinding(
        profile.knowledge_provider_id, profile.project_id, profile.profile_sha256,
        now, profile.expires_at,
        available, authenticated, policy_authorized, privacy_eligible, network_allowed,
        digest,
    )
    binding.validate()
    return binding


@dataclass(frozen=True)
class DocumentationEvidence:
    schema_version: str
    evidence_id: str
    provider_id: str
    provider_binding_sha256: str
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
            "provider_binding_sha256": self.provider_binding_sha256,
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
        _sha("provider_binding_sha256", self.provider_binding_sha256, _SHA256)
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
        citation_hashes = {
            citation_sha256(citation)
            for citation in self.citations
        }
        if sum(len(item.excerpt) for item in self.citations) > 16_000:
            raise DocumentationError("citation excerpts exceed the aggregate bound")
        if len(self.claims) > _MAX_CLAIMS:
            raise DocumentationError("claims exceed the bound")
        claim_keys = set()
        for claim in self.claims:
            claim.validate()
            if not set(claim.citation_sha256s).issubset(citation_hashes):
                raise DocumentationError("claim cites unknown evidence")
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

    def assess(
        self,
        request: DocumentationRequest,
        *,
        now: str,
        provider_binding: ProviderBinding | None = None,
    ) -> DocumentationStatus:
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
        selected_binding = provider_binding or request.provider_binding
        if selected_binding is None:
            return DocumentationStatus.PROVIDER_INELIGIBLE
        try:
            selected_binding.validate(now=now)
        except DocumentationError:
            return DocumentationStatus.PROVIDER_INELIGIBLE
        if (
            self.project_id != selected_binding.project_id
            or self.project_id != request.project_id
            or self.provider_id != selected_binding.provider_id
            or self.provider_binding_sha256 != selected_binding.binding_sha256
        ):
            return DocumentationStatus.PROVIDER_INELIGIBLE
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
            schema_version=payload["schema_version"],
            evidence_id=payload["evidence_id"],
            provider_id=payload["provider_id"],
            provider_binding_sha256=payload["provider_binding_sha256"],
            project_id=payload["project_id"],
            repository_id=payload["repository_id"],
            revision_sha=payload["revision_sha"],
            operation=DocumentationOperation(payload["operation"]),
            dependency=dependency,
            requested_topic=payload["requested_topic"],
            returned_topic=payload["returned_topic"],
            documentation_version=payload["documentation_version"],
            documentation_source=payload["documentation_source"],
            citations=tuple(DocumentationCitation(**item) for item in payload["citations"]),
            claims=tuple(
                DocumentationClaim(
                    item["assertion_key"], item["assertion_value"],
                    item["claim_sha256"], tuple(item["citation_sha256s"]),
                )
                for item in payload["claims"]
            ),
            observed_at=payload["observed_at"],
            freshness=DocumentationFreshness(payload["freshness"]),
            status=DocumentationStatus(payload["status"]),
            evidence_sha256=payload["evidence_sha256"],
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
    binding: ProviderBinding | None = None


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
    return ProviderResolution(
        DocumentationStatus.VALID,
        reason,
        _seal_provider_binding(
            profile,
            now=now,
            available=available is True,
            authenticated=authenticated is True,
            policy_authorized=policy_authorized is True,
            privacy_eligible=privacy_eligible,
            network_allowed=network_allowed,
        ),
    )


def reconcile_evidence(
    evidence: tuple[DocumentationEvidence, ...],
    request: DocumentationRequest,
    *,
    now: str,
    provider_bindings: tuple[ProviderBinding, ...] = (),
) -> DocumentationStatus:
    if not evidence:
        return DocumentationStatus.UNAVAILABLE
    for item in evidence:
        item.validate()
    bindings = {binding.binding_sha256: binding for binding in provider_bindings}
    statuses = {
        (
            DocumentationStatus.PROVIDER_INELIGIBLE
            if provider_bindings and item.provider_binding_sha256 not in bindings
            else item.assess(
                request,
                now=now,
                provider_binding=bindings.get(item.provider_binding_sha256),
            )
        )
        for item in evidence
    }
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
        if (
            item.documentation_version is None
            or first.documentation_version is None
            or _version_identity(item.documentation_version)
            != _version_identity(first.documentation_version)
        ):
            return DocumentationStatus.CONTRADICTORY
        if item.returned_topic != first.returned_topic:
            return DocumentationStatus.CONTRADICTORY
        first_claims = {
            claim.assertion_key: claim.assertion_value
            for claim in first.claims
        }
        item_claims = {
            claim.assertion_key: claim.assertion_value
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
