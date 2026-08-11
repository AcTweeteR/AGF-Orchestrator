"""Single immutable runtime authority resolution path.

This module deliberately has no mutation methods and never reads private key
material.  The owner controller is responsible for preparing and activating
generation records; runtime consumers receive only ``AuthorityContext``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .authority_generation import (
    AuthorityGeneration,
    AuthorityGenerationError,
    AuthorityGenerationStore,
)
from .owner_authority import OwnerAuthorityError, verify_envelope


class AuthorityContextError(RuntimeError):
    """Raised when a complete verified authority context cannot be built."""


@dataclass(frozen=True)
class RuntimeAuthority:
    """The only authority bundle exposed to consequential runtime callers."""

    constitution: Any
    policy: Any
    context: "AuthorityContext | None"
    snapshot: Mapping[str, Any] | None = None
    policy_snapshot: Mapping[str, Any] | None = None


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class AuthorityContext:
    """Immutable, project-bound authority snapshot for consequential work."""

    project_id: str
    generation_id: str
    generation_number: int
    scheme: str
    manifest_hash: str
    constitution_hash: str
    policy_hash: str
    components: Mapping[str, Mapping[str, Any]]
    artifacts: Mapping[str, Any]

    @classmethod
    def resolve_runtime(cls, project_id: str, state_root: str | Path) -> "AuthorityContext | None":
        """Use the selector path when installed; missing staging is explicit."""
        store = AuthorityGenerationStore(state_root)
        try:
            selector_path = store._selector_path(project_id)
        except AuthorityGenerationError:
            # Let the legacy authority emit its canonical project-identity error.
            return None
        if not selector_path.exists():
            return None
        return cls.resolve(store, project_id, artifact_root=state_root)

    @classmethod
    def resolve(
        cls,
        store: AuthorityGenerationStore,
        project_id: str,
        *,
        artifact_root: str | Path | None = None,
        artifacts: Mapping[str, Any] | None = None,
    ) -> "AuthorityContext":
        loaded_artifacts: dict[str, Any] = {}
        try:
            generation = store.active(project_id)
            loaded_artifacts = cls._verify_artifacts(
                generation, artifact_root=artifact_root, artifacts=artifacts
            )
        except (AuthorityGenerationError, OSError, TypeError, ValueError) as exc:
            raise AuthorityContextError("AUTHORITY_CONTEXT_UNAVAILABLE") from exc
        return cls(
            project_id=project_id,
            generation_id=generation.generation_id,
            generation_number=generation.generation_number,
            scheme=generation.scheme,
            manifest_hash=generation.manifest_hash,
            constitution_hash=generation.constitution_hash,
            policy_hash=generation.policy_hash,
            components=MappingProxyType(
                {item.name: MappingProxyType(item.to_dict()) for item in generation.components}
            ),
            artifacts=MappingProxyType(loaded_artifacts),
        )

    @staticmethod
    def _verify_artifacts(
        generation: AuthorityGeneration,
        *,
        artifact_root: str | Path | None,
        artifacts: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        root = Path(artifact_root).resolve() if artifact_root is not None else None
        loaded: dict[str, Any] = {}
        for component in generation.components:
            if component.artifact_path is None:
                raise AuthorityContextError("authority component artifact is unbound")
            if artifacts is not None and component.name in artifacts:
                actual = artifacts[component.name]
            elif root is not None:
                path = (root / component.artifact_path).resolve()
                if root not in path.parents and path != root:
                    raise AuthorityContextError("authority component escapes artifact root")
                actual = json.loads(path.read_text(encoding="utf-8"))
            else:
                raise AuthorityContextError("authority component artifact is unavailable")
            if _canonical_hash(actual) != component.artifact_hash:
                raise AuthorityContextError("authority component artifact hash mismatch")
            if _canonical_hash(actual) != component.semantic_hash:
                raise AuthorityContextError("authority component semantic hash mismatch")
            loaded[component.name] = actual
            if generation.scheme == "Ed25519":
                try:
                    verify_envelope(actual, component.artifact_signature or {})
                except OwnerAuthorityError as exc:
                    raise AuthorityContextError("authority component signature invalid") from exc
        return loaded


def resolve_authority(
    project_id: str,
    *,
    constitution_backend: Any | None = None,
    policy_backend: Any | None = None,
    include_policy: bool = True,
) -> RuntimeAuthority:
    """Resolve one project through the installed generation path or legacy backend.

    The legacy branch is a compatibility staging path only. Once an external
    owner-controlled selector exists, failure to verify it is fatal and no
    legacy fallback is attempted.
    """
    from .constitution import ConstitutionAuthority
    from .policy_authority import PolicyActivationError, PolicyAuthority
    from .policy_state_store import PolicyStateError, PolicyStateStore

    state_root = Path.home() / ".agf-orchestrator"
    authority_snapshot = None
    policy_snapshot = None
    if (state_root / "policy-state.sqlite3").exists():
        try:
            store = PolicyStateStore(state_root, read_only=True)
            authority_snapshot = store.authority_snapshot(project_id)
            policy_snapshot = store.snapshot(project_id)
        except PolicyStateError:
            authority_snapshot = None
            policy_snapshot = None
        if include_policy and (
            policy_snapshot is None or policy_snapshot.get("active_policy_hash") is None
        ):
            return RuntimeAuthority(None, None, None, authority_snapshot, policy_snapshot)
    staged = AuthorityContext.resolve_runtime(project_id, state_root)
    if staged is not None:
        try:
            from .constitution import ActiveConstitution, _freeze
            from .policy_authority import ActiveMergePolicy, canonical_hash

            constitution_record = staged.artifacts["constitution"]
            policy_record = staged.artifacts["policy"]
            activation = staged.artifacts["activation"]
            constitution = ActiveConstitution(
                project_id=project_id,
                constitution_id=constitution_record["constitution_id"],
                version=constitution_record["version"],
                compatibility=constitution_record["compatibility"],
                approval_status=constitution_record["approval_status"],
                key_id=constitution_record["key_id"],
                record_hash=staged.constitution_hash,
                body=_freeze(constitution_record["body"]),
            )
            policy = ActiveMergePolicy(
                project_id=project_id,
                policy_id=policy_record["policy_id"],
                version=policy_record["version"],
                policy_hash=staged.policy_hash,
                activation_hash=canonical_hash(activation),
                rollback_target=activation["rollback_target"],
                key_id=policy_record["key_id"],
                freshness_limits=policy_record["body"]["freshness_limits"],
            )
            return RuntimeAuthority(
                constitution, policy, staged, authority_snapshot, policy_snapshot
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorityContextError("AUTHORITY_CONTEXT_ARTIFACTS_INVALID") from exc

    # The only legacy compatibility gate is the complete pre-cutover state:
    # no generation selector plus the owner-controlled legacy artifacts.  A
    # partial/random state never authorizes the fallback.
    constitution_pointer = (
        state_root / "projects" / project_id / "constitution" / "active.json"
    )
    legacy_policy_state = state_root / "policy-state.sqlite3"
    if include_policy and (
        not constitution_pointer.exists() or not legacy_policy_state.exists()
    ):
        return RuntimeAuthority(None, None, None, authority_snapshot, policy_snapshot)
    constitution = (constitution_backend or ConstitutionAuthority())._resolve_legacy(project_id)
    if include_policy:
        try:
            policy = (policy_backend or PolicyAuthority())._resolve_legacy(project_id)
        except PolicyActivationError as exc:
            if not any(detail in str(exc) for detail in ("unreadable", "not activated")):
                raise
            policy = None
    else:
        policy = None
    return RuntimeAuthority(
        constitution, policy, None, authority_snapshot, policy_snapshot
    )
