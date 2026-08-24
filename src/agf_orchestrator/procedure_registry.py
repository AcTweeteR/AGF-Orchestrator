"""Project-isolated registry and deterministic selection for governed procedures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .capability_extensions import (
    CapabilityExtensionError,
    InvocationPolicy,
    ProcedureProfile,
    procedure_profile_from_dict,
)
from .risk_models import RiskLevel


class ProcedureRegistryError(ValueError):
    """Raised when procedure persistence or selection is unsafe or ambiguous."""


@dataclass(frozen=True)
class ProcedureRequirements:
    capabilities: tuple[str, ...]
    risk: RiskLevel
    requested_paths: tuple[str, ...]
    provider_capabilities: tuple[str, ...]


class ProcedureRegistry:
    """Persist procedure evidence below an explicit caller-owned state root."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _project_dir(self, project_id: str) -> Path:
        if not project_id.startswith("project-") or "/" in project_id or ".." in project_id:
            raise ProcedureRegistryError("project_id is invalid")
        return self.root / "procedures" / project_id

    def put(self, profile: ProcedureProfile) -> Path:
        try:
            profile.validate()
        except CapabilityExtensionError as exc:
            raise ProcedureRegistryError(str(exc)) from exc
        directory = self._project_dir(profile.project_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{profile.procedure_id}.json"
        payload = json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n"
        target.write_text(payload, encoding="utf-8")
        return target

    def get(
        self,
        project_id: str,
        procedure_id: str,
        *,
        now: str | None = None,
    ) -> ProcedureProfile:
        if not procedure_id.startswith("procedure-") or "/" in procedure_id or ".." in procedure_id:
            raise ProcedureRegistryError("procedure_id is invalid")
        path = self._project_dir(project_id) / f"{procedure_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            profile = procedure_profile_from_dict(payload)
            profile.validate(now=now)
        except (OSError, json.JSONDecodeError, CapabilityExtensionError) as exc:
            raise ProcedureRegistryError(
                f"procedure evidence is unavailable or invalid: {exc}"
            ) from exc
        if profile.project_id != project_id or profile.procedure_id != procedure_id:
            raise ProcedureRegistryError("procedure evidence binding mismatch")
        return profile

    def list(self, project_id: str, *, now: str | None = None) -> tuple[ProcedureProfile, ...]:
        directory = self._project_dir(project_id)
        if not directory.exists():
            return ()
        profiles: list[ProcedureProfile] = []
        for path in sorted(directory.glob("procedure-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                profile = procedure_profile_from_dict(payload)
                profile.validate(now=now)
            except (OSError, json.JSONDecodeError, CapabilityExtensionError) as exc:
                raise ProcedureRegistryError(
                    f"invalid procedure evidence at {path.name}: {exc}"
                ) from exc
            if profile.project_id != project_id:
                raise ProcedureRegistryError("cross-project procedure evidence detected")
            profiles.append(profile)
        return tuple(profiles)

    def select(
        self,
        project_id: str,
        requirements: ProcedureRequirements,
        *,
        now: str,
    ) -> ProcedureProfile:
        candidates = [
            profile
            for profile in self.list(project_id, now=now)
            if _eligible(profile, requirements)
        ]
        if not candidates:
            raise ProcedureRegistryError("no eligible governed procedure")
        ranked = sorted(candidates, key=_rank)
        best_rank = _rank(ranked[0])
        best = [profile for profile in ranked if _rank(profile) == best_rank]
        if len(best) != 1:
            raise ProcedureRegistryError("ambiguous governed procedure selection")
        return best[0]


def _eligible(profile: ProcedureProfile, requirements: ProcedureRequirements) -> bool:
    if profile.invocation_policy is not InvocationPolicy.AGF_SELECTABLE:
        return False
    if requirements.risk > profile.max_risk:
        return False
    if not set(requirements.capabilities).issubset(profile.capabilities):
        return False
    if not set(profile.provider_requirements).issubset(requirements.provider_capabilities):
        return False
    return all(_path_allowed(path, profile.allowed_paths) for path in requirements.requested_paths)


def _path_allowed(requested: str, allowed: tuple[str, ...]) -> bool:
    if requested.startswith(("/", "~")) or ".." in requested.split("/"):
        return False
    for rule in allowed:
        if rule == "**" or rule == requested:
            return True
        if rule.endswith("/**"):
            prefix = rule[:-3].rstrip("/")
            if requested == prefix or requested.startswith(prefix + "/"):
                return True
    return False


def _rank(profile: ProcedureProfile) -> tuple[int, int, int]:
    """Prefer narrower capability/path scope and then newer explicit profile versions."""
    return (len(profile.capabilities), len(profile.allowed_paths), -profile.profile_version)
