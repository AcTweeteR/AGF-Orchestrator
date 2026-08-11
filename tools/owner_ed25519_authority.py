"""External owner controller for the one-time Ed25519 trust-anchor ceremony.

This file is intentionally outside the AGF runtime package.  It is the only
component in this repository allowed to load the legacy HMAC owner secret or
the Ed25519 private key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator.authority_context import AuthorityContext, resolve_authority
from agf_orchestrator.authority_generation import (
    AuthorityComponent,
    AuthorityGenerationStore,
    GenerationStatus,
    _atomic_write,
    build_generation,
)
from agf_orchestrator.constitution import ConstitutionAuthority, canonical_json
from agf_orchestrator.owner_authority import (
    PINNED_OWNER_FINGERPRINT,
    canonical_bytes,
    load_pinned_anchor,
)
from agf_orchestrator.policy_authority import PolicyAuthority
from agf_orchestrator.policy_state_store import PolicyStateStore
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.provider_intelligence import ProviderIntelligenceStore, state_from_dict


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _legacy_key(state_dir: Path) -> bytes:
    authority = state_dir / "constitution-authority"
    path = authority / "owner.key"
    if authority.is_symlink() or path.is_symlink() or authority.stat().st_mode & 0o077:
        raise RuntimeError("legacy owner authority permissions are invalid")
    key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    if len(key) < 32:
        raise RuntimeError("legacy owner authority is invalid")
    return key


def _private_key(root: Path) -> Ed25519PrivateKey:
    path = root / "owner-private.key"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("owner Ed25519 private-key permissions are invalid")
    try:
        value = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
        return Ed25519PrivateKey.from_private_bytes(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("owner Ed25519 private key is unavailable") from exc


def sign_envelope(payload: object, root: Path) -> dict[str, str]:
    """Sign one payload for an external owner operation; never returns key material."""
    private = _private_key(root)
    public = private.public_key().public_bytes_raw()
    payload_bytes = canonical_bytes(payload)
    return {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": "owner-ed25519-1",
        "public_key_fingerprint": hashlib.sha256(public).hexdigest(),
        "payload_hash": hashlib.sha256(payload_bytes).hexdigest(),
        "signature": base64.b64encode(private.sign(payload_bytes)).decode(),
    }


def activate_provider_candidate(project_id: str, candidate: Path) -> dict[str, str]:
    """Owner-sign a provider candidate produced by the read-only runtime step."""
    state = state_from_dict(json.loads(candidate.read_text(encoding="utf-8")))
    if state.project_id != project_id:
        raise RuntimeError("provider candidate project binding is invalid")
    project = ProjectRegistry().verify(project_id)
    target_sha = subprocess.run(
        ["git", "-C", project.repository_root, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    authority = resolve_authority(project_id)
    if authority.policy is None or authority.constitution is None:
        raise RuntimeError("current project authority is unavailable")
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state.validate(now=now, target_sha=target_sha)
    if state.constitution_id != authority.constitution.constitution_id:
        raise RuntimeError("provider candidate Constitution binding is stale")
    if state.constitution_record_hash != authority.constitution.record_hash:
        raise RuntimeError("provider candidate Constitution hash is stale")
    expected_generation = (
        authority.context.generation_number
        if authority.context is not None
        else authority.policy_snapshot.get("generation")
        if isinstance(authority.policy_snapshot, dict)
        else None
    )
    if state.policy_generation != expected_generation:
        raise RuntimeError("provider candidate policy generation is stale")
    evidence = dict(state.gate_evidence)
    expected_policy_evidence = (
        f"active-policy:{authority.policy.policy_id}:{authority.policy.policy_hash}"
    )
    if evidence.get("policy_eligible") != expected_policy_evidence:
        raise RuntimeError("provider candidate policy evidence is stale")
    unsigned_signed = state.__class__(
        **{
            **state.__dict__,
            "signing_key_id": "owner-ed25519-1",
            "signature": None,
            "state_sha256": "0" * 64,
        }
    )
    envelope = sign_envelope(unsigned_signed._unsigned(), Path.home() / ".agf-owner-root")
    unsigned_signed = state.__class__(
        **{**unsigned_signed.__dict__, "signature": envelope}
    )
    state_hash = hashlib.sha256(
        json.dumps(
            unsigned_signed._unsigned(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    signed = unsigned_signed.__class__(**{**unsigned_signed.__dict__, "state_sha256": state_hash})
    ProviderIntelligenceStore().for_project(project_id).save(signed)
    return {"project_id": project_id, "state_sha256": signed.state_sha256}


def _migration_state_dir() -> Path:
    return Path.home() / ".agf-orchestrator"


def _object_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _write_object(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_write(path, value)


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"authority artifact is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("authority artifact must be an object")
    return value


def _monotonic_floor(store: AuthorityGenerationStore, project_id: str) -> int:
    store._recover_metadata(project_id)
    path = store._floor_path(project_id)
    if not path.exists():
        return 0
    value = _read_object(path)
    if (
        value.get("schema_version") != "1.0"
        or value.get("project_id") != project_id
        or not isinstance(value.get("generation_number"), int)
        or value["generation_number"] < 0
    ):
        raise RuntimeError("authority generation floor is invalid")
    return value["generation_number"]


def _generation_root() -> Path:
    root = Path.home() / ".agf-owner-root"
    anchor, _ = load_pinned_anchor()
    if anchor["fingerprint"] != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("pinned owner fingerprint changed")
    return root


def _verified_legacy_transition(project_id: str, policy, constitution, generation: int) -> dict:
    transition_path = Path.home() / ".agf-owner-root" / "transition.json"
    transition = _read_object(transition_path)
    signature = transition.pop("legacy_signature", None)
    if not isinstance(signature, str):
        raise RuntimeError("legacy authority transition signature is missing")
    expected = hmac.new(
        _legacy_key(_migration_state_dir()), canonical_json(transition), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError("legacy authority transition signature is invalid")
    if (
        transition.get("project_id") != project_id
        or transition.get("policy_id") != policy.policy_id
        or transition.get("policy_hash") != policy.policy_hash
        or transition.get("constitution_id") != constitution.constitution_id
        or transition.get("constitution_record_hash") != constitution.record_hash
        or transition.get("policy_generation") != generation
        or transition.get("new_authority", {}).get("fingerprint") != PINNED_OWNER_FINGERPRINT
    ):
        raise RuntimeError("legacy authority transition binding is invalid")
    return {**transition, "legacy_signature": signature}


def _legacy_components(project_id: str) -> tuple[dict[str, dict], int]:
    state_dir = _migration_state_dir()
    project = ProjectRegistry().get(project_id)
    constitution = ConstitutionAuthority().resolve(project_id)
    policy = PolicyAuthority().resolve(project_id)
    snapshot = PolicyStateStore(state_dir, read_only=True).snapshot(project_id)
    if snapshot is None or not isinstance(snapshot.get("activation"), dict):
        raise RuntimeError("current HMAC policy state is unavailable")
    current_generation = int(snapshot.get("generation", 0))
    if current_generation < 1:
        raise RuntimeError("current HMAC policy generation is invalid")
    constitution_record = _read_object(
        state_dir
        / "projects"
        / project_id
        / "constitution"
        / f"{constitution.constitution_id}.json"
    )
    if (
        _object_hash(constitution_record) != constitution.record_hash
        or constitution_record.get("project_id") != project_id
        or constitution_record.get("constitution_id") != constitution.constitution_id
    ):
        raise RuntimeError("legacy Constitution artifact is not the verified record")
    if (
        _object_hash(snapshot["policy"]) != policy.policy_hash
        or snapshot.get("active_policy_id") != policy.policy_id
        or snapshot.get("active_policy_hash") != policy.policy_hash
        or _object_hash(snapshot["activation"]) != policy.activation_hash
        or snapshot["activation"].get("project_id") != project_id
        or snapshot["activation"].get("policy_id") != policy.policy_id
        or snapshot["activation"].get("policy_hash") != policy.policy_hash
    ):
        raise RuntimeError("legacy policy artifacts are not the verified active state")
    _verified_legacy_transition(project_id, policy, constitution, current_generation)
    kill_switch = PolicyStateStore(state_dir, read_only=True).authority_snapshot(project_id)
    return {
        "constitution": constitution_record,
        "policy": snapshot["policy"],
        "activation": snapshot["activation"],
        "rollback": {
            "project_id": project_id,
            "status": "PINNED_BASELINE",
            "rollback_target": policy.rollback_target,
            "policy_id": policy.policy_id,
            "policy_hash": policy.policy_hash,
            "generation": current_generation,
            "kill_switch": kill_switch,
        },
        "registration": project.to_dict(),
        "provider_intelligence": {
            "project_id": project_id,
            "status": "PENDING_PROVIDER_INTELLIGENCE",
            "policy_generation": current_generation,
        },
    }, current_generation


def _build_prepared_generation(project_id: str, generation_id: str, operation_id: str):
    state_dir = _migration_state_dir()
    values, current_generation = _legacy_components(project_id)
    components = []
    names = (
        "constitution",
        "policy",
        "activation",
        "rollback",
        "registration",
        "provider_intelligence",
    )
    for name in names:
        value = values[name]
        relative = Path("authority-generations") / project_id / generation_id / f"{name}.json"
        _write_object(state_dir / relative, value)
        artifact_hash = _object_hash(value)
        components.append(
            AuthorityComponent(
                name=name,
                generation_id=generation_id,
                artifact_hash=artifact_hash,
                scheme="Ed25519",
                project_id=project_id,
                semantic_hash=artifact_hash,
                artifact_path=str(relative),
                artifact_signature=sign_envelope(value, _generation_root()),
            )
        )
    transition = _verified_legacy_transition(
        project_id,
        PolicyAuthority().resolve(project_id),
        ConstitutionAuthority().resolve(project_id),
        current_generation,
    )
    generation = build_generation(
        generation_id=generation_id,
        project_id=project_id,
        scheme="Ed25519",
        owner_key_id="owner-ed25519-1",
        owner_fingerprint=PINNED_OWNER_FINGERPRINT,
        constitution_id=values["constitution"]["constitution_id"],
        constitution_hash=_object_hash(values["constitution"]),
        policy_hash=_object_hash(values["policy"]),
        operation_id=operation_id,
        status=GenerationStatus.VERIFIED,
        components=tuple(components),
        predecessor_id=f"legacy-hmac-generation-{current_generation}",
        predecessor_hash=_object_hash(transition),
    )
    return replace(generation, signature=sign_envelope(generation._unsigned(), _generation_root()))


def prepare_ed25519_generation(project_id: str, operation_id: str) -> dict[str, object]:
    state_dir = _migration_state_dir()
    project = ProjectRegistry().get(project_id)
    if project.status.value != "ACTIVE":
        raise RuntimeError("project registration is not ACTIVE")
    policy = PolicyAuthority().resolve(project_id)
    if policy.policy_id != "merge-policy-adr-0003":
        raise RuntimeError("unexpected current policy")
    snapshot = PolicyStateStore(state_dir, read_only=True).snapshot(project_id)
    current_number = int(snapshot.get("generation", 0) if snapshot else 0)
    store = AuthorityGenerationStore(state_dir)
    for candidate_path in store._directory(project_id).glob("generation-*.json"):
        candidate = store.load(project_id, candidate_path.stem)
        if candidate.operation_id != operation_id:
            continue
        if candidate.status is GenerationStatus.ACTIVE:
            return {
                "status": "ALREADY_ACTIVE",
                "project_id": project_id,
                "generation_id": candidate.generation_id,
                "generation_number": candidate.generation_number,
                "manifest_hash": candidate.manifest_hash,
                "operation_id": operation_id,
            }
        if candidate.status is not GenerationStatus.SUPERSEDED:
            return {
                "status": "PREPARED",
                "project_id": project_id,
                "generation_id": candidate.generation_id,
                "generation_number": candidate.generation_number,
                "manifest_hash": candidate.manifest_hash,
                "operation_id": operation_id,
            }
        raise RuntimeError("authority operation identity has already been consumed")
    current_number = max(current_number, _monotonic_floor(store, project_id))
    generation_id = f"generation-{current_number + 1}"
    existing_path = store._generation_path(project_id, generation_id)
    if existing_path.exists():
        raise RuntimeError("next authority generation is already prepared")
    generation = _build_prepared_generation(project_id, generation_id, operation_id)
    store._save_prepared_owner_controlled(generation)
    return {
        "status": "PREPARED",
        "project_id": project_id,
        "generation_id": generation_id,
        "generation_number": generation.generation_number,
        "manifest_hash": generation.manifest_hash,
        "operation_id": operation_id,
    }


def _verify_prepared_generation(project_id: str, generation_id: str):
    state_dir = _migration_state_dir()
    store = AuthorityGenerationStore(state_dir)
    generation = store.load(project_id, generation_id)
    if generation.scheme != "Ed25519" or generation.owner_fingerprint != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("prepared generation owner binding is invalid")
    artifacts = AuthorityContext._verify_artifacts(
        generation, artifact_root=state_dir, artifacts=None
    )
    context = AuthorityContext(
        project_id=project_id,
        generation_id=generation.generation_id,
        generation_number=generation.generation_number,
        scheme=generation.scheme,
        manifest_hash=generation.manifest_hash,
        constitution_hash=generation.constitution_hash,
        policy_hash=generation.policy_hash,
        components={item.name: item.to_dict() for item in generation.components},
        artifacts=artifacts,
    )
    if context.generation_id != generation_id or context.scheme != "Ed25519":
        raise RuntimeError("prepared generation context is inconsistent")
    return generation, context


def verify_ed25519_generation(project_id: str, generation_id: str) -> dict[str, object]:
    generation, _ = _verify_prepared_generation(project_id, generation_id)
    directory = _migration_state_dir() / "authority-generations" / project_id
    selector_path = directory / "active.json"
    floor_path = directory / "generation-floor.json"
    selector = _read_object(selector_path) if selector_path.exists() else None
    floor = _read_object(floor_path) if floor_path.exists() else {"generation_number": 0}
    readiness = {
        "schema_version": "1.0", "project_id": project_id, "generation_id": generation_id,
        "generation_number": generation.generation_number,
        "manifest_hash": generation.manifest_hash,
        "component_hashes": {item.name: item.artifact_hash for item in generation.components},
        "predecessor_id": generation.predecessor_id,
        "predecessor_hash": generation.predecessor_hash,
        "current_selector": selector, "current_floor": floor.get("generation_number", 0),
        "owner_fingerprint": PINNED_OWNER_FINGERPRINT, "operation_id": generation.operation_id,
        "verified_at": _now(), "verification": "PASS",
    }
    _write_object(
        directory / generation_id / "readiness.json",
        {**readiness, "signature": sign_envelope(readiness, _generation_root())},
    )
    return {"status": "GENERATION_READY_FOR_CUTOVER", **readiness}


def cutover_ed25519_generation(project_id: str, generation_id: str) -> dict[str, object]:
    state_dir = _migration_state_dir()
    AuthorityGenerationStore._validate_project_id(project_id)
    AuthorityGenerationStore._validate_generation_id(generation_id)
    directory = state_dir / "authority-generations" / project_id / generation_id
    readiness_record = _read_object(directory / "readiness.json")
    signature = readiness_record.pop("signature", None)
    if not isinstance(signature, dict):
        raise RuntimeError("generation readiness signature is missing")
    from agf_orchestrator.owner_authority import verify_envelope

    verify_envelope(readiness_record, signature)
    generation, _ = _verify_prepared_generation(project_id, generation_id)
    expected_components = {item.name: item.artifact_hash for item in generation.components}
    if (
        readiness_record.get("project_id") != project_id
        or readiness_record.get("generation_id") != generation.generation_id
        or readiness_record.get("generation_number") != generation.generation_number
        or readiness_record.get("manifest_hash") != generation.manifest_hash
        or readiness_record.get("component_hashes") != expected_components
        or readiness_record.get("predecessor_id") != generation.predecessor_id
        or readiness_record.get("predecessor_hash") != generation.predecessor_hash
        or readiness_record.get("operation_id") != generation.operation_id
        or readiness_record.get("owner_fingerprint") != PINNED_OWNER_FINGERPRINT
    ):
        raise RuntimeError("generation readiness is not bound to prepared generation")
    authority_dir = state_dir / "authority-generations" / project_id
    selector_path = authority_dir / "active.json"
    floor_path = authority_dir / "generation-floor.json"
    selector = _read_object(selector_path) if selector_path.exists() else None
    floor = _read_object(floor_path) if floor_path.exists() else {"generation_number": 0}
    if selector and selector.get("generation_id") == generation_id:
        return {"status": "ALREADY_ACTIVE", "generation_id": generation_id}
    if (
        selector != readiness_record["current_selector"]
        or floor.get("generation_number", 0) != readiness_record["current_floor"]
    ):
        raise RuntimeError("generation readiness is stale")
    active_unsigned = build_generation(
        **{
            **generation.__dict__,
            "status": GenerationStatus.ACTIVE,
            "manifest_hash": "0" * 64,
            "signature": None,
        }
    )
    active_signature = sign_envelope(active_unsigned._unsigned(), _generation_root())
    AuthorityGenerationStore(state_dir)._activate_owner_controlled(
        project_id, generation_id, active_signature=active_signature
    )
    active, context = _verify_prepared_generation(project_id, generation_id)
    if active.status is not GenerationStatus.ACTIVE or context.scheme != "Ed25519":
        raise RuntimeError("post-cutover authority verification failed")
    return {"status": "CUTOVER_COMPLETE", "generation_id": generation_id,
            "generation_number": active.generation_number, "manifest_hash": active.manifest_hash,
            "scheme": context.scheme}


def prepare_root(project_id: str, operation_id: str, root: Path) -> dict[str, str]:
    constitution = ConstitutionAuthority().resolve(project_id)
    policy = PolicyAuthority().resolve(project_id)
    state_dir = Path.home() / ".agf-orchestrator"
    legacy_key = _legacy_key(state_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    private_path = root / "owner-private.key"
    public_path = root / "owner-public.key"
    anchor_path = root / "anchor.json"
    transition_path = root / "transition.json"
    if not private_path.exists():
        raise RuntimeError("owner Ed25519 root must be provisioned by the owner")
    private = _private_key(root)
    public_bytes = private.public_key().public_bytes_raw()
    public_fingerprint = hashlib.sha256(public_bytes).hexdigest()
    if public_fingerprint != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("pre-provisioned owner root does not match pinned authority")
    key_id = "owner-ed25519-1"
    if public_path.exists() or anchor_path.exists() or transition_path.exists():
        raise RuntimeError("owner Ed25519 root metadata already exists")
    public_path.write_text(base64.b64encode(public_bytes).decode() + "\n")
    os.chmod(public_path, 0o644)
    anchor = {
        "schema_version": "1.0",
        "signature_scheme": "Ed25519",
        "key_id": key_id,
        "fingerprint": public_fingerprint,
    }
    anchor_path.write_text(json.dumps(anchor, sort_keys=True) + "\n")
    os.chmod(anchor_path, 0o644)
    unsigned = {
        "schema_version": "1.0",
        "operation_id": operation_id,
        "previous_authority": {"scheme": "HMAC-SHA256", "key_id": "owner-key-1"},
        "new_authority": {
            "scheme": "Ed25519",
            "key_id": key_id,
            "fingerprint": public_fingerprint,
        },
        "project_id": project_id,
        "constitution_id": constitution.constitution_id,
        "constitution_record_hash": constitution.record_hash,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "policy_generation": 3,
        "created_at": _now(),
    }
    transition = {
        **unsigned,
        "legacy_signature": hmac.new(
            legacy_key, canonical_json(unsigned), hashlib.sha256
        ).hexdigest(),
    }
    transition_path.write_text(json.dumps(transition, sort_keys=True) + "\n")
    os.chmod(transition_path, 0o600)
    return {"operation_id": operation_id, "key_id": key_id, "fingerprint": public_fingerprint}


def main() -> int:
    parser = argparse.ArgumentParser(description="external owner Ed25519 authority controller")
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--prepare-generation", action="store_true")
    parser.add_argument("--verify-generation")
    parser.add_argument("--cutover-generation")
    args = parser.parse_args()
    if args.prepare_generation:
        result = prepare_ed25519_generation(args.project, args.operation_id)
    elif args.verify_generation:
        result = verify_ed25519_generation(args.project, args.verify_generation)
    elif args.cutover_generation:
        result = cutover_ed25519_generation(args.project, args.cutover_generation)
    else:
        result = prepare_root(args.project, args.operation_id, Path.home() / ".agf-owner-root")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
