"""Provider-neutral, version-bound technical documentation evidence."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .capability_extensions import (
    CapabilityExtensionError,
    KnowledgeProviderProfile,
)
from .provider_eligibility import ProviderEligibilityAuthority, ProviderEligibilityError
from .provider_intelligence import ProviderIntelligenceStore
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


def _canonical_authority(
    authority: ProviderEligibilityAuthority | None,
) -> ProviderEligibilityAuthority:
    """Accept only the sealed canonical authority at this trust boundary."""
    if authority is None:
        return ProviderEligibilityAuthority(ProviderIntelligenceStore())
    if type(authority) is not ProviderEligibilityAuthority:
        raise DocumentationError("canonical provider eligibility authority is required")
    return authority


class DocumentationOperation(StrEnum):
    RESOLVE_LIBRARY = "RESOLVE_LIBRARY"
    RETRIEVE_VERSIONED = "RETRIEVE_VERSIONED"
    RETRIEVE_TOPIC = "RETRIEVE_TOPIC"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_NPM_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,63}/)?[a-z0-9][a-z0-9._-]{0,127}$")
_PYPI_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GO_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._~-]{0,63})+$")
_MAVEN_COORDINATE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
    r"(?::[A-Za-z0-9][A-Za-z0-9_.-]{0,127})?$"
)
_SUPPORTED_REGISTRIES = frozenset({"npm", "pypi", "go", "maven"})
_MAVEN_VERSION = re.compile(
    r"^(?P<core>[0-9]{1,32}(?:\.[0-9]{1,32}){0,15})"
    r"(?:[.-](?P<qualifier>[A-Za-z]+(?:[-.]?[0-9]+)?))?$"
)
_MAVEN_QUALIFIER_RANK = {
    "alpha": 0,
    "a": 0,
    "beta": 1,
    "b": 1,
    "milestone": 2,
    "m": 2,
    "rc": 3,
    "snapshot": 4,
    "final": 5,
    "ga": 5,
    "release": 5,
    "sp": 6,
}
_MAVEN_QUALIFIER_ALIASES = {
    "a": "alpha",
    "b": "beta",
    "m": "milestone",
}
_VERSION = re.compile(
    r"^[0-9]{1,32}(?:\.[0-9]{1,32}){0,3}"
    r"(?:-[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?"
    r"(?:\+[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?$"
)
_STRICT_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]{0,31})\."
    r"(?:0|[1-9][0-9]{0,31})\."
    r"(?:0|[1-9][0-9]{0,31})"
    r"(?:-[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?"
    r"(?:\+[0-9A-Za-z-]{1,64}(?:\.[0-9A-Za-z-]{1,64})*)?$"
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_URI_USERINFO = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]{1,31}://[^/\s:@]+:[^/\s@]+@"
)
_URI_CANDIDATE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,31}://[^\s<>]+")
_CREDENTIAL_QUERY_NAMES = frozenset({
    "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "x-goog-signature", "googleaccessid", "signature", "sig", "access_token",
    "oauth_token", "bearer_token", "api_key", "client_secret",
})
_SECRET = re.compile(
    r"(?i)(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?access[_-]?key[_-]?id|"
    r"secret\s+access\s+key|client\s+secret|private\s+key|"
    r"api(?:[_-]|\s)?key|token|secret|password|authorization)\s*[:=]|"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_QUOTED_SECRET_LABEL = re.compile(
    r"(?i)(['\"])(?:aws[_-]?secret[_-]?access[_-]?key|"
    r"aws[_-]?access[_-]?key[_-]?id|secret\s+access\s+key|"
    r"password|passwd|secret|client[_ ]secret|api[_ ]key|"
    r"access[_ ]token|refresh[_ ]token|private[_ ]key)\1\s*:\s*"
    r"(['\"])[^'\"]*\2"
)
_MAX_TEXT = 4000
_MAX_CITATIONS = 32
_MAX_EXCERPT = 2400
_MAX_CLAIMS = 32
_MAX_CLAIM_TEXT = 512
_CLOCK_SKEW_SECONDS = 0
_MAX_VERSION_LENGTH = 256
_MAX_CITATION_REFS = 8
_PROVIDER_BINDING_TTL_SECONDS = 3600
def _contains_credential_query(value: str) -> bool:
    for candidate_text in (value, html.unescape(value)):
        for candidate in _URI_CANDIDATE.findall(candidate_text):
            try:
                query = parse_qsl(urlsplit(candidate).query, keep_blank_values=True)
            except ValueError:
                continue
            if any(name.casefold() in _CREDENTIAL_QUERY_NAMES for name, _ in query):
                return True
    return False


def _text(label: str, value: Any, *, limit: int = _MAX_TEXT) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DocumentationError(f"{label} is invalid")
    decoded = html.unescape(value)
    if (
        _SECRET.search(value)
        or _SECRET.search(decoded)
        or _QUOTED_SECRET_LABEL.search(value)
        or _QUOTED_SECRET_LABEL.search(decoded)
        or _URI_USERINFO.search(value)
        or _URI_USERINFO.search(decoded)
        or _contains_credential_query(value)
    ):
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


def _canonical_version(registry: str, label: str, value: Any) -> str:
    if registry == "go" and isinstance(value, str) and value.startswith("v"):
        value = value[1:]
    if registry == "pypi" and isinstance(value, str):
        try:
            if len(value) > _MAX_VERSION_LENGTH:
                raise DocumentationError(f"{label} is invalid")
            if any(len(component) > 32 for component in re.findall(r"\d+", value)):
                raise DocumentationError(f"{label} is invalid")
            return str(Version(value))
        except (InvalidVersion, TypeError, ValueError) as exc:
            raise DocumentationError(f"{label} is invalid") from exc
    if registry == "maven" and isinstance(value, str):
        return _canonical_maven_version(label, value)
    _version(label, value)
    return value


def _canonical_concrete_version(registry: str, label: str, value: Any) -> str:
    """Validate a resolved/documented version, distinct from range syntax."""
    canonical = _canonical_version(registry, label, value)
    if registry in {"npm", "go"} and not _STRICT_SEMVER.fullmatch(canonical):
        raise DocumentationError(f"{label} must be a concrete SemVer")
    return canonical


def _maven_parts(label: str, value: str) -> tuple[tuple[int, ...], str | None, int]:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_LENGTH:
        raise DocumentationError(f"{label} is invalid")
    match = _MAVEN_VERSION.fullmatch(value)
    if not match:
        raise DocumentationError(f"{label} is invalid")
    try:
        core = tuple(int(part) for part in match.group("core").split("."))
    except (TypeError, ValueError) as exc:
        raise DocumentationError(f"{label} is invalid") from exc
    qualifier = match.group("qualifier")
    if qualifier is None:
        return core, None, 5
    normalized = qualifier.casefold()
    qualifier_match = re.fullmatch(r"([a-z]+)(?:[-.]?([0-9]+))?", normalized)
    if not qualifier_match:
        raise DocumentationError(f"{label} has unsupported Maven qualifier")
    qualifier_root = _MAVEN_QUALIFIER_ALIASES.get(
        qualifier_match.group(1), qualifier_match.group(1)
    )
    if qualifier_root not in _MAVEN_QUALIFIER_RANK:
        raise DocumentationError(f"{label} has unsupported Maven qualifier")
    normalized = qualifier_root + (qualifier_match.group(2) or "")
    return (
        core,
        normalized,
        _MAVEN_QUALIFIER_RANK[qualifier_root],
    )


def _canonical_maven_version(label: str, value: str) -> str:
    core, qualifier, _ = _maven_parts(label, value)
    return ".".join(str(part) for part in core) + (
        f".{qualifier}" if qualifier is not None else ""
    )


def _pypi_key(value: str) -> Version:
    try:
        return Version(value)
    except (InvalidVersion, TypeError, ValueError) as exc:
        raise DocumentationError("version is invalid") from exc


def _maven_key(value: str) -> tuple[Any, ...]:
    core, qualifier, rank = _maven_parts("version", value)
    qualifier_root = ""
    qualifier_number = -1
    if qualifier is not None:
        qualifier_match = re.fullmatch(
            r"([a-z]+)(?:[-.]?([0-9]+))?", qualifier
        )
        if qualifier_match is None:
            raise DocumentationError("version qualifier is invalid")
        qualifier_root = qualifier_match.group(1)
        qualifier_number = int(qualifier_match.group(2) or "-1")
        if qualifier_root in {"final", "ga", "release"}:
            qualifier_root = ""
            qualifier_number = -1
    return (
        core + (0,) * (16 - len(core)),
        rank,
        qualifier_root,
        qualifier_number,
    )


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


def _version_identity(value: str, registry: str = "semver") -> tuple[Any, ...]:
    if registry == "pypi":
        return ("pypi", _pypi_key(value))
    if registry == "maven":
        return ("maven", _maven_key(value))
    _version_key(value)
    build = value.partition("+")[2]
    return _version_key(value), build


def _canonical_package(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 256:
        raise DocumentationError("package identity is invalid")
    _text("package_id", value, limit=256)


def _validate_registry_package(registry: str, package_id: str) -> None:
    if registry not in _SUPPORTED_REGISTRIES:
        raise DocumentationError("dependency registry is unsupported")
    patterns = {
        "npm": _NPM_PACKAGE,
        "pypi": _PYPI_PACKAGE,
        "go": _GO_MODULE,
        "maven": _MAVEN_COORDINATE,
    }
    if not patterns[registry].fullmatch(package_id):
        raise DocumentationError("package identity is invalid for registry")
    if ".." in package_id or "/./" in f"/{package_id}/" or "/../" in f"/{package_id}/":
        raise DocumentationError("package identity contains traversal")


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


def _explicit_prerelease_cores(
    constraint: str, registry: str
) -> set[tuple[Any, ...]]:
    normalized = constraint.strip()
    if (
        registry in {"npm", "go"}
        and _VERSION.fullmatch(normalized)
        and "-" in normalized.partition("+")[0]
    ):
        operands = (normalized,)
    elif normalized.startswith(("^", "~")):
        operands = (normalized[1:],)
    else:
        operands = tuple(
            match.group(2)
            for term in normalized.split(",")
            if (match := re.fullmatch(r"(==|=|>=|<=|>|<)(.+)", term.strip()))
        )
    cores = set()
    for operand in operands:
        operand = _canonical_version(registry, "constraint version", operand)
        if "-" in operand.partition("+")[0]:
            cores.add(_version_key(operand)[:4])
    return cores


def _constraint_allows(
    constraint: str, version: str, registry: str
) -> bool | None:
    """Evaluate the small, deterministic constraint subset used by fixtures."""
    normalized = constraint.strip()
    canonical_version = _canonical_concrete_version(registry, "version", version)
    if registry in {"pypi", "maven"}:
        return _ecosystem_constraint_allows(normalized, canonical_version, registry)
    version_value = _version_key(canonical_version)
    if registry == "go" and normalized.startswith("v"):
        normalized = normalized[1:]
    if "-" in canonical_version.partition("+")[0]:
        if _version_key(canonical_version)[:4] not in _explicit_prerelease_cores(
            normalized, registry
        ):
            return False
    if normalized in {"", "*"}:
        return True
    if registry == "npm" and re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        components = len(normalized.split("."))
        lower = _version_key(normalized + (".0" if components == 1 else ".0"))
        numbers = lower[:4]
        upper = (
            (numbers[0] + 1, 0, 0, 0, 1, ())
            if components == 1
            else (numbers[0], numbers[1] + 1, 0, 0, 1, ())
        )
        return version_value >= lower and version_value < upper
    if _VERSION.fullmatch(normalized):
        return _version_identity(canonical_version, registry) == _version_identity(
            normalized, registry
        )
    if normalized.startswith("^"):
        base = normalized[1:]
        base = _canonical_version(registry, "constraint version", base)
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
        base = _canonical_version(registry, "constraint version", base)
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
        operand = _canonical_version(registry, "constraint version", operand)
        operand_value = _version_key(operand)
        if (
            operator in {"=", "=="}
            and _version_identity(canonical_version, registry)
            != _version_identity(operand, registry)
        ):
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


def _ecosystem_constraint_allows(
    constraint: str, version: str, registry: str
) -> bool | None:
    version_key = _pypi_key(version) if registry == "pypi" else _maven_key(version)
    if registry == "pypi":
        if constraint in {"", "*"}:
            return True
        specifier = constraint
        if re.fullmatch(r"[0-9]+(?:[.!][0-9A-Za-z-]+)*", constraint):
            specifier = f"=={constraint}"
        try:
            specifiers = SpecifierSet(specifier)
        except (InvalidSpecifier, TypeError, ValueError):
            return None
        if version_key.is_prerelease and specifiers.prereleases is not True:
            return False
        return specifiers.contains(version_key, prereleases=True)
    if constraint.startswith(("^", "~")):
        return None
    for term in (term.strip() for term in constraint.split(",")):
        match = re.fullmatch(r"(==|=|>=|<=|>|<)?(.+)", term)
        if not match:
            return None
        operator, operand = match.groups()
        try:
            canonical_operand = _canonical_version(
                registry, "constraint version", operand
            )
            operand_key = (
                _pypi_key(canonical_operand)
                if registry == "pypi"
                else _maven_key(canonical_operand)
            )
        except DocumentationError:
            return None
        operator = operator or "=="
        if operator in {"=", "=="} and version_key != operand_key:
            return False
        if operator == ">=" and version_key < operand_key:
            return False
        if operator == "<=" and version_key > operand_key:
            return False
        if operator == ">" and version_key <= operand_key:
            return False
        if operator == "<" and version_key >= operand_key:
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
        _validate_registry_package(self.registry, self.package_id)
        _text("declared_constraint", self.declared_constraint, limit=256)
        for label, value in (
            ("locked_version", self.locked_version),
            ("resolved_version", self.resolved_version),
            ("runtime_observed_version", self.runtime_observed_version),
        ):
            if value is not None:
                _canonical_concrete_version(self.registry, label, value)
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
        canonical_values = tuple(
            _canonical_concrete_version(self.registry, "dependency version", value)
            for value in values
        )
        if len({_version_identity(value, self.registry) for value in canonical_values}) != 1:
            raise DocumentationError("dependency version sources contradict")
        allowed = _constraint_allows(
            self.declared_constraint, values[0], self.registry
        )
        if allowed is not True:
            raise DocumentationError("resolved dependency does not satisfy declared constraint")
        return values[0]

    def has_exact_declaration(self) -> bool:
        constraint = self.declared_constraint
        if self.registry == "go" and constraint.startswith("v"):
            constraint = constraint[1:]
        return bool(re.fullmatch(r"(?:==|=)?\d+(?:\.\d+){0,3}", constraint))


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
        if self.operation is DocumentationOperation.RESOLVE_LIBRARY and (
            self.repository_id is not None or self.revision_sha is not None
        ):
            raise DocumentationError("library resolution is explicitly revisionless")


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
        if len(self.citation_sha256s) > _MAX_CITATION_REFS:
            raise DocumentationError("claim citation references exceed the bound")
        if len(set(self.citation_sha256s)) != len(self.citation_sha256s):
            raise DocumentationError("claim citation references must be unique")
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
    issuance_token: str = ""
    decision_sha256: str = ""
    target_sha: str = ""
    revision_scope: str = "revision-bound"
    # Runtime-only injection; persisted bindings always re-resolve canonical state.
    authority: Any = field(default=None, compare=False, repr=False)

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
            "issuance_token": self.issuance_token,
            "decision_sha256": self.decision_sha256,
            "target_sha": self.target_sha,
            "revision_scope": self.revision_scope,
        }

    def validate(
        self,
        *,
        now: str | None = None,
        eligibility_authority: ProviderEligibilityAuthority | None = None,
    ) -> None:
        if not _ID.fullmatch(self.provider_id):
            raise DocumentationError("provider binding provider_id is invalid")
        if not _ID.fullmatch(self.project_id) or not self.project_id.startswith("project-"):
            raise DocumentationError("provider binding project_id is invalid")
        _sha("provider binding profile_sha256", self.profile_sha256, _SHA256)
        if not isinstance(self.issuance_token, str) or len(self.issuance_token) < 32:
            raise DocumentationError("provider binding issuance is missing")
        _sha("provider binding decision_sha256", self.decision_sha256, _SHA256)
        if self.revision_scope not in {"revision-bound", "resolve-library"}:
            raise DocumentationError("provider binding revision scope is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", self.target_sha):
            raise DocumentationError("provider binding target revision is invalid")
        decision = _timestamp("provider binding decision_at", self.decision_at)
        if now is not None and decision > _timestamp("provider binding now", now):
            raise DocumentationError("provider binding is future-dated")
        if self.expires_at is not None:
            expiry = _timestamp("provider binding expires_at", self.expires_at)
            if expiry <= _timestamp("provider binding decision_at", self.decision_at):
                raise DocumentationError("provider binding expiry is invalid")
            if now is not None and _timestamp("provider binding now", now) >= expiry:
                raise DocumentationError("provider binding has expired")
        try:
            authority = _canonical_authority(eligibility_authority or self.authority)
            decision = authority.resolve(
                project_id=self.project_id,
                provider_id=self.provider_id,
                provider_kind="knowledge",
                capability_domain="documentation",
                now=now or self.decision_at,
                required_capabilities=("documentation",),
                target_sha=(self.target_sha if self.revision_scope == "revision-bound" else None),
                revision_scope=self.revision_scope,
                decision_domain="documentation",
            )
        except (ProviderEligibilityError, OSError, ValueError) as exc:
            raise DocumentationError("provider binding lacks canonical eligibility") from exc
        expected_observations = {
            "available": decision.health_eligible,
            "authenticated": decision.authentication_eligible is True,
            "policy_authorized": decision.policy_eligible,
            "privacy_eligible": decision.privacy_eligible,
            "network_allowed": decision.network_eligible,
        }
        if self.decision_sha256 != decision.decision_sha256:
            raise DocumentationError("provider binding decision differs from authority")
        if self.decision_at != decision.decision_at:
            raise DocumentationError("provider binding decision time differs from authority")
        if self.target_sha != decision.target_sha:
            raise DocumentationError("provider binding target revision differs from authority")
        if self.revision_scope != decision.revision_scope:
            raise DocumentationError("provider binding revision scope differs from authority")
        expected_token = hashlib.sha256(
            f"agf-provider-binding-v1:{decision.decision_sha256}:{self.profile_sha256}".encode()
        ).hexdigest()
        if self.issuance_token != expected_token:
            raise DocumentationError("provider binding issuance artifact is invalid")
        if any(
            getattr(self, label) != value
            for label, value in expected_observations.items()
            if value is not None
        ):
            raise DocumentationError("provider binding observations differ from authority")
        unsigned = {**self.to_dict(), "binding_sha256": ""}
        expected = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.binding_sha256 != expected:
            raise DocumentationError("provider binding hash does not match content")


def _seal_provider_binding(
    profile: KnowledgeProviderProfile,
    *,
    decision: Any,
    eligibility_authority: ProviderEligibilityAuthority | None = None,
) -> ProviderBinding:
    authority = _canonical_authority(eligibility_authority)
    now = decision.decision_at
    decision_at = _timestamp("provider binding decision_at", now)
    ttl_expires_at = decision_at + timedelta(seconds=_PROVIDER_BINDING_TTL_SECONDS)
    if profile.expires_at is None:
        effective_expiry = ttl_expires_at
    else:
        effective_expiry = min(
            ttl_expires_at, _timestamp("provider profile expires_at", profile.expires_at)
        )
    binding_expires_at = effective_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
    issuance_token = hashlib.sha256(
        f"agf-provider-binding-v1:{decision.decision_sha256}:{profile.profile_sha256}".encode()
    ).hexdigest()
    unsigned = {
        "provider_id": profile.knowledge_provider_id,
        "project_id": profile.project_id,
        "profile_sha256": profile.profile_sha256,
        "decision_at": now,
        "expires_at": binding_expires_at,
        "available": decision.health_eligible,
        "authenticated": decision.authentication_eligible is True,
        "policy_authorized": decision.policy_eligible,
        "privacy_eligible": decision.privacy_eligible,
        "network_allowed": decision.network_eligible,
        "binding_sha256": "",
        "issuance_token": issuance_token,
        "decision_sha256": decision.decision_sha256,
        "target_sha": decision.target_sha,
        "revision_scope": decision.revision_scope,
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    binding = ProviderBinding(
        profile.knowledge_provider_id, profile.project_id, profile.profile_sha256,
        now, binding_expires_at,
        decision.health_eligible, decision.authentication_eligible is True,
        decision.policy_eligible, decision.privacy_eligible, decision.network_eligible,
        digest, issuance_token, decision.decision_sha256, decision.target_sha,
        decision.revision_scope,
        authority,
    )
    binding.validate(eligibility_authority=authority)
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
            _canonical_concrete_version(
                self.dependency.registry, "documentation_version", self.documentation_version
            )
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
        observed_at = _timestamp("observed_at", self.observed_at)
        dependency_observed_at = _timestamp(
            "dependency observed_at", self.dependency.observed_at
        )
        if dependency_observed_at > observed_at:
            raise DocumentationError(
                "dependency observation cannot be newer than documentation retrieval"
            )
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
        expected_scope = (
            "resolve-library"
            if request.operation is DocumentationOperation.RESOLVE_LIBRARY
            else "revision-bound"
        )
        if (
            self.project_id != selected_binding.project_id
            or self.project_id != request.project_id
            or self.provider_id != selected_binding.provider_id
            or self.provider_binding_sha256 != selected_binding.binding_sha256
            or selected_binding.revision_scope != expected_scope
            or (
                expected_scope == "revision-bound"
                and selected_binding.target_sha != request.revision_sha
            )
        ):
            return DocumentationStatus.PROVIDER_INELIGIBLE
        if _timestamp("provider binding decision_at", selected_binding.decision_at) > _timestamp(
            "observed_at", self.observed_at
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
        registry = request.dependency.registry
        if _version_identity(
            _canonical_concrete_version(
                registry, "documentation_version", self.documentation_version
            ),
            registry,
        ) != _version_identity(
            _canonical_concrete_version(registry, "project version", project_version), registry
        ):
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
        claim_fields = {"assertion_key", "assertion_value", "claim_sha256", "citation_sha256s"}
        if any(
            not isinstance(item, dict) or set(item) != claim_fields
            for item in payload["claims"]
        ):
            raise DocumentationError("claim schema is missing or contains unknown fields")
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
    store: SessionStore,
    session_id: str,
    evidence: DocumentationEvidence,
    *,
    provider_binding: ProviderBinding | None = None,
) -> tuple[str, str]:
    evidence.validate()
    if provider_binding is not None:
        provider_binding.validate()
        if provider_binding.binding_sha256 != evidence.provider_binding_sha256:
            raise DocumentationError("persisted provider binding does not match evidence")
        persist_provider_binding(store, session_id, provider_binding)
    return store.write_artifact(
        session_id,
        f"documentation-{evidence.evidence_id}.json",
        json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
    )


def persist_provider_binding(
    store: SessionStore, session_id: str, binding: ProviderBinding
) -> tuple[str, str]:
    """Persist the binding separately; evidence contains only its digest."""
    binding.validate()
    return store.write_artifact(
        session_id,
        f"provider-binding-{binding.binding_sha256}.json",
        json.dumps(binding.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
    )


def load_provider_binding(
    store: SessionStore,
    session_id: str,
    binding_sha256: str,
    *,
    eligibility_authority: ProviderEligibilityAuthority | None = None,
) -> ProviderBinding:
    _sha("provider binding digest", binding_sha256, _SHA256)
    path = (
        store._path(session_id).parent.parent
        / "artifacts"
        / session_id
        / f"provider-binding-{binding_sha256}.json"
    )
    try:
        path = store.ensure_safe_path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        binding = ProviderBinding(**payload)
        binding.validate(eligibility_authority=eligibility_authority)
        if binding.binding_sha256 != binding_sha256:
            raise DocumentationError("provider binding artifact digest mismatch")
        # Preserve the verified authority for subsequent assess() calls. A
        # binding loaded from a non-default owner state must not silently
        # fall back to the process default authority after deserialization.
        return replace(binding, authority=eligibility_authority)
    except (OSError, SessionStoreError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DocumentationError("provider binding is unavailable") from exc


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


@dataclass(frozen=True)
class ProviderRuntimeConstraints:
    """Invocation-local denials; never an eligibility authority or persisted."""

    available: bool | None
    authenticated: bool | None
    policy_authorized: bool | None
    privacy_eligible: bool | None
    network_allowed: bool | None

    def denial_reason(self, profile: KnowledgeProviderProfile) -> str | None:
        if self.available is False:
            return "provider is unavailable at invocation time"
        if self.policy_authorized is False:
            return "provider is not authorized for this invocation"
        if profile.requires_credentials or profile.requires_authenticated_session:
            if self.authenticated is False:
                return "provider authentication is unavailable at invocation time"
        if profile.network_required and self.network_allowed is False:
            return "provider network access is unavailable at invocation time"
        if profile.privacy_review_required and self.privacy_eligible is False:
            return "provider privacy eligibility is unavailable at invocation time"
        return None


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
    eligibility_authority: ProviderEligibilityAuthority | None = None,
    target_sha: str | None = None,
    revision_scope: str = "revision-bound",
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
    authority = _canonical_authority(eligibility_authority)
    try:
        decision = authority.resolve_knowledge_profile(
            profile, now=now, required_capability="documentation", target_sha=target_sha,
            revision_scope=revision_scope,
        )
    except ProviderEligibilityError as exc:
        reason = str(exc)
        if "network" in reason:
            return ProviderResolution(DocumentationStatus.NETWORK_BLOCKED, reason)
        if "privacy" in reason:
            return ProviderResolution(DocumentationStatus.PRIVACY_BLOCKED, reason)
        return ProviderResolution(DocumentationStatus.PROVIDER_INELIGIBLE, reason)
    runtime = ProviderRuntimeConstraints(
        available, authenticated, policy_authorized, privacy_eligible, network_allowed
    )
    if (runtime_reason := runtime.denial_reason(profile)) is not None:
        if "network" in runtime_reason:
            status = DocumentationStatus.NETWORK_BLOCKED
        elif "privacy" in runtime_reason:
            status = DocumentationStatus.PRIVACY_BLOCKED
        else:
            status = DocumentationStatus.PROVIDER_INELIGIBLE
        return ProviderResolution(status, runtime_reason)
    reason = "eligible" if required else "optional-eligible"
    return ProviderResolution(
        DocumentationStatus.VALID,
        reason,
        _seal_provider_binding(
            profile, decision=decision, eligibility_authority=authority
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
        registry = first.dependency.registry
        if (
            item.documentation_version is None
            or first.documentation_version is None
            or _version_identity(
                _canonical_concrete_version(
                    registry, "documentation version", item.documentation_version
                ),
                registry,
            )
            != _version_identity(
                _canonical_concrete_version(
                    registry, "documentation version", first.documentation_version
                ),
                registry,
            )
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
