"""Versioned, attributable capability profiles with deterministic validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class CapabilityProfileError(ValueError):
    """Raised when capability-profile evidence is invalid or unusable."""


class CapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_PROJECT_ID = re.compile(r"^project-[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]|"
    r"(?:sk-|ghp_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_-]{12,}"
)
_MAX_TEXT = 4000
_MAX_CAPABILITIES = 200


@dataclass(frozen=True)
class CapabilityObservation:
    """One explicitly observed capability; UNKNOWN is never inferred."""

    name: str
    status: CapabilityStatus
    value: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "value": self.value}

    def validate(self) -> None:
        _bounded_text("capability name", self.name)
        if not isinstance(self.status, CapabilityStatus):
            raise CapabilityProfileError("capability status is invalid")
        if self.value is not None:
            _bounded_text("capability value", self.value)


@dataclass(frozen=True)
class CapabilityProfile:
    """Immutable evidence record for one provider and project."""

    schema_version: str
    profile_id: str
    project_id: str
    provider_id: str
    profile_version: int
    provenance_source: str
    provenance_sha256: str
    observed_at: str
    expires_at: str | None
    capabilities: tuple[CapabilityObservation, ...]
    profile_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "profile_version": self.profile_version,
            "provenance_source": self.provenance_source,
            "provenance_sha256": self.provenance_sha256,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "profile_sha256": self.profile_sha256,
        }

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise CapabilityProfileError("schema_version must be 1.0")
        if not _ID.fullmatch(self.profile_id) or not self.profile_id.startswith("profile-"):
            raise CapabilityProfileError("profile_id is invalid")
        if not _PROJECT_ID.fullmatch(self.project_id):
            raise CapabilityProfileError("project_id is invalid")
        if not _ID.fullmatch(self.provider_id):
            raise CapabilityProfileError("provider_id is invalid")
        if not isinstance(self.profile_version, int) or isinstance(self.profile_version, bool):
            raise CapabilityProfileError("profile_version is invalid")
        if self.profile_version < 1:
            raise CapabilityProfileError("profile_version must be positive")
        _bounded_text("provenance_source", self.provenance_source)
        if not _SHA256.fullmatch(self.provenance_sha256):
            raise CapabilityProfileError("provenance_sha256 is invalid")
        if self.provenance_sha256 != sha256_text(self.provenance_source):
            raise CapabilityProfileError("provenance_sha256 does not match provenance")
        if not _TIMESTAMP.fullmatch(self.observed_at):
            raise CapabilityProfileError("observed_at is invalid")
        observed_at = _timestamp(self.observed_at)
        if self.expires_at is not None and not _TIMESTAMP.fullmatch(self.expires_at):
            raise CapabilityProfileError("expires_at is invalid")
        if (
            self.expires_at is not None
            and _timestamp(self.expires_at) <= observed_at
        ):
            raise CapabilityProfileError("expires_at must be after observed_at")
        if not isinstance(self.capabilities, (tuple, list)) or not self.capabilities:
            raise CapabilityProfileError("capabilities must not be empty")
        if len(self.capabilities) > _MAX_CAPABILITIES:
            raise CapabilityProfileError("capabilities exceed the bound")
        names: set[str] = set()
        for capability in self.capabilities:
            if not isinstance(capability, CapabilityObservation):
                raise CapabilityProfileError("capability is invalid")
            capability.validate()
            if capability.name in names:
                raise CapabilityProfileError("capability names must be unique")
            names.add(capability.name)
        if not _SHA256.fullmatch(self.profile_sha256):
            raise CapabilityProfileError("profile_sha256 is invalid")
        if self.profile_sha256 != capability_profile_hash(self):
            raise CapabilityProfileError("profile_sha256 does not match profile content")

    def require_supported(self, capability_name: str) -> str:
        """Return a supported value; unsupported and UNKNOWN fail closed."""
        matches = [item for item in self.capabilities if item.name == capability_name]
        if len(matches) != 1 or matches[0].status is not CapabilityStatus.SUPPORTED:
            raise CapabilityProfileError(f"capability {capability_name!r} is not supported")
        if matches[0].value is None:
            raise CapabilityProfileError(f"capability {capability_name!r} has no value")
        return matches[0].value

    def validate_binding(self, project_id: str, provider_id: str) -> None:
        """Reject use of a profile outside its exact project/provider binding."""
        if self.project_id != project_id or self.provider_id != provider_id:
            raise CapabilityProfileError("profile binding does not match request")

    def is_stale(self, now: str) -> bool:
        if not _TIMESTAMP.fullmatch(now):
            raise CapabilityProfileError("now is invalid")
        now_instant = _timestamp(now)
        return self.expires_at is not None and now_instant >= _timestamp(self.expires_at)

    def validate_at(self, now: str) -> None:
        """Validate structure and reject evidence that is stale at a boundary."""
        self.validate()
        if self.is_stale(now):
            raise CapabilityProfileError("profile evidence is stale")


class CapabilityProfileRegistry:
    """Bounded project registry enforcing provider/profile version transitions."""

    def __init__(self, project_id: str) -> None:
        if not _PROJECT_ID.fullmatch(project_id):
            raise CapabilityProfileError("project_id is invalid")
        self.project_id = project_id
        self._profiles: dict[tuple[str, str], CapabilityProfile] = {}

    def record(self, profile: CapabilityProfile) -> None:
        profile.validate_binding(self.project_id, profile.provider_id)
        profile.validate()
        key = (profile.provider_id, profile.profile_id)
        previous = self._profiles.get(key)
        if previous is not None and profile.profile_version <= previous.profile_version:
            raise CapabilityProfileError("profile version must advance monotonically")
        self._profiles[key] = profile

    def get(self, provider_id: str, profile_id: str) -> CapabilityProfile:
        try:
            return self._profiles[(provider_id, profile_id)]
        except KeyError as exc:
            raise CapabilityProfileError("profile is not registered") from exc


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CapabilityProfileError("timestamp is not a real UTC instant") from exc


def _hash_payload(profile: CapabilityProfile) -> dict[str, Any]:
    payload = profile.to_dict()
    payload["profile_sha256"] = ""
    return payload


def canonical_profile_json(profile: CapabilityProfile) -> str:
    return json.dumps(_hash_payload(profile), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)


def capability_profile_hash(profile: CapabilityProfile) -> str:
    return sha256_text(canonical_profile_json(profile))


def profile_from_dict(payload: dict[str, Any]) -> CapabilityProfile:
    required = {
        "schema_version", "profile_id", "project_id", "provider_id", "profile_version",
        "provenance_source", "provenance_sha256", "observed_at", "expires_at",
        "capabilities", "profile_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise CapabilityProfileError("profile schema is missing or contains unknown fields")
    try:
        profile = CapabilityProfile(
            schema_version=payload["schema_version"], profile_id=payload["profile_id"],
            project_id=payload["project_id"], provider_id=payload["provider_id"],
            profile_version=payload["profile_version"],
            provenance_source=payload["provenance_source"],
            provenance_sha256=payload["provenance_sha256"], observed_at=payload["observed_at"],
            expires_at=payload["expires_at"],
            capabilities=tuple(
                CapabilityObservation(
                    name=item["name"], status=CapabilityStatus(item["status"]),
                    value=item["value"],
                ) for item in payload["capabilities"]
            ),
            profile_sha256=payload["profile_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityProfileError(f"invalid profile structure: {exc}") from exc
    profile.validate()
    return profile


def _bounded_text(label: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise CapabilityProfileError(f"{label} is invalid")
    if _SECRET.search(value):
        raise CapabilityProfileError(f"{label} contains secret-shaped data")
