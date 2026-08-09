"""Deterministic invalidation of stale, changed, or cross-boundary evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from .capability_profiles import (
    CapabilityProfile,
    CapabilityProfileError,
    profile_from_dict,
    sha256_text,
)


class CapabilityInvalidationError(ValueError):
    """Raised when evidence is stale, changed, replayed, or out of scope."""


class InvalidationReason(StrEnum):
    STALE = "STALE"
    PROVIDER_UPGRADE = "PROVIDER_UPGRADE"
    HEALTH_CHANGE = "HEALTH_CHANGE"
    CONFLICT = "CONFLICT"
    CROSS_PROJECT = "CROSS_PROJECT"
    REPLAY = "REPLAY"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class CapabilityEvidenceRecord:
    """Profile plus the provider-state evidence that made it eligible."""

    profile: CapabilityProfile
    provider_state_sha256: str
    health_generation: int
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "provider_state_sha256": self.provider_state_sha256,
            "health_generation": self.health_generation,
            "recorded_at": self.recorded_at,
        }

    def validate(self) -> None:
        self.profile.validate()
        if len(self.provider_state_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.provider_state_sha256
        ):
            raise CapabilityInvalidationError("provider_state_sha256 is invalid")
        if not isinstance(self.health_generation, int) or isinstance(self.health_generation, bool):
            raise CapabilityInvalidationError("health_generation is invalid")
        if self.health_generation < 0:
            raise CapabilityInvalidationError("health_generation must not be negative")
        if not isinstance(self.recorded_at, str):
            raise CapabilityInvalidationError("recorded_at is invalid")
        try:
            self.profile.validate_at(self.recorded_at)
        except CapabilityProfileError as exc:
            raise CapabilityInvalidationError(str(exc)) from exc


@dataclass(frozen=True)
class InvalidationRecord:
    project_id: str
    provider_id: str
    profile_id: str
    profile_sha256: str
    reason: InvalidationReason
    invalidated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "profile_id": self.profile_id,
            "profile_sha256": self.profile_sha256,
            "reason": self.reason.value,
            "invalidated_at": self.invalidated_at,
        }

    def validate(self) -> None:
        if (
            not self.project_id.startswith("project-")
            or not self.provider_id
            or not self.profile_id
        ):
            raise CapabilityInvalidationError("invalidation binding is invalid")
        if len(self.profile_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.profile_sha256
        ):
            raise CapabilityInvalidationError("profile_sha256 is invalid")
        if not isinstance(self.reason, InvalidationReason):
            raise CapabilityInvalidationError("invalidation reason is invalid")
        _validate_timestamp(self.invalidated_at)


class CapabilityInvalidator:
    """Invalidate evidence monotonically and prevent replay of tombstoned hashes."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], CapabilityEvidenceRecord] = {}
        self._invalidated: dict[str, InvalidationRecord] = {}

    def record(self, evidence: CapabilityEvidenceRecord) -> None:
        evidence.validate()
        profile = evidence.profile
        if profile.profile_sha256 in self._invalidated:
            raise CapabilityInvalidationError("invalidated evidence cannot be resurrected")
        key = (profile.project_id, profile.provider_id, profile.profile_id)
        previous = self._records.get(key)
        if previous is not None and profile.profile_version <= previous.profile.profile_version:
            if profile.profile_sha256 != previous.profile.profile_sha256:
                self._invalidated[profile.profile_sha256] = InvalidationRecord(
                    profile.project_id, profile.provider_id, profile.profile_id,
                    profile.profile_sha256, InvalidationReason.CONFLICT, evidence.recorded_at,
                )
                raise CapabilityInvalidationError("conflicting profile evidence was invalidated")
            raise CapabilityInvalidationError("profile version must advance monotonically")
        if previous is not None:
            self.invalidate(previous, InvalidationReason.SUPERSEDED, evidence.recorded_at)
        self._records[key] = evidence

    def invalidate(
        self,
        evidence: CapabilityEvidenceRecord,
        reason: InvalidationReason,
        invalidated_at: str,
    ) -> InvalidationRecord:
        evidence.validate()
        if not isinstance(reason, InvalidationReason):
            raise CapabilityInvalidationError("invalidation reason is invalid")
        record = InvalidationRecord(
            evidence.profile.project_id, evidence.profile.provider_id,
            evidence.profile.profile_id, evidence.profile.profile_sha256, reason, invalidated_at,
        )
        record.validate()
        self._invalidated[evidence.profile.profile_sha256] = record
        return record

    def invalidate_provider(
        self,
        provider_id: str,
        provider_state_sha256: str,
        invalidated_at: str,
        reason: InvalidationReason,
    ) -> tuple[InvalidationRecord, ...]:
        if len(provider_state_sha256) != 64:
            raise CapabilityInvalidationError("provider_state_sha256 is invalid")
        records = tuple(
            self.invalidate(evidence, reason, invalidated_at)
            for evidence in self._records.values()
            if evidence.profile.provider_id == provider_id
            and evidence.provider_state_sha256 != provider_state_sha256
        )
        return records

    def eligible(
        self,
        evidence: CapabilityEvidenceRecord,
        *,
        project_id: str,
        provider_state_sha256: str,
        health_generation: int,
        now: str,
    ) -> bool:
        evidence.validate()
        try:
            evidence.profile.validate_binding(project_id, evidence.profile.provider_id)
        except CapabilityProfileError as exc:
            raise CapabilityInvalidationError(str(exc)) from exc
        profile_hash = evidence.profile.profile_sha256
        if profile_hash in self._invalidated:
            raise CapabilityInvalidationError("profile evidence is invalidated")
        try:
            evidence.profile.validate_at(now)
        except CapabilityProfileError as exc:
            if "stale" in str(exc):
                self.invalidate(evidence, InvalidationReason.STALE, now)
            raise CapabilityInvalidationError(str(exc)) from exc
        if evidence.provider_state_sha256 != provider_state_sha256:
            self.invalidate(evidence, InvalidationReason.PROVIDER_UPGRADE, now)
            raise CapabilityInvalidationError("provider state changed")
        if evidence.health_generation != health_generation:
            self.invalidate(evidence, InvalidationReason.HEALTH_CHANGE, now)
            raise CapabilityInvalidationError("provider health generation changed")
        return True

    def export_state(self) -> str:
        """Serialize tombstones for durable owner-controlled restart/readback."""
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "records": [
                evidence.to_dict()
                for _, evidence in sorted(self._records.items())
            ],
            "tombstones": [
                record.to_dict()
                for _, record in sorted(self._invalidated.items())
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return json.dumps(
            {**payload, "state_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

    @classmethod
    def from_state(cls, serialized: str) -> "CapabilityInvalidator":
        try:
            payload = json.loads(serialized)
            state_hash = payload.pop("state_sha256")
            canonical = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != state_hash:
                raise CapabilityInvalidationError("invalidation state hash is invalid")
            if payload.get("schema_version") != "1.0" or not isinstance(
                payload.get("tombstones"), list
            ):
                raise CapabilityInvalidationError("invalidation state schema is invalid")
        except CapabilityInvalidationError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityInvalidationError("invalidation state is invalid") from exc
        store = cls()
        if not isinstance(payload.get("records"), list):
            raise CapabilityInvalidationError("invalidation records are invalid")
        for item in payload["records"]:
            try:
                evidence = CapabilityEvidenceRecord(
                    profile_from_dict(item["profile"]), item["provider_state_sha256"],
                    item["health_generation"], item["recorded_at"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CapabilityInvalidationError("invalidation evidence is invalid") from exc
            evidence.validate()
            key = (
                evidence.profile.project_id, evidence.profile.provider_id,
                evidence.profile.profile_id,
            )
            store._records[key] = evidence
        for item in payload["tombstones"]:
            try:
                record = InvalidationRecord(
                    item["project_id"], item["provider_id"], item["profile_id"],
                    item["profile_sha256"], InvalidationReason(item["reason"]),
                    item["invalidated_at"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CapabilityInvalidationError("invalidation tombstone is invalid") from exc
            record.validate()
            store._invalidated[record.profile_sha256] = record
        return store

    def invalidation_digest(self) -> str:
        """Return a stable digest of tombstones for restart/readback evidence."""
        values = [
            ":".join((key, record.reason.value, record.invalidated_at))
            for key, record in sorted(self._invalidated.items())
        ]
        return sha256_text("|".join(values))


def _validate_timestamp(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError) as exc:
        raise CapabilityInvalidationError("timestamp is invalid") from exc
