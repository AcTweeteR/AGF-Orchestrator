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
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator.authority_context import resolve_authority
from agf_orchestrator.constitution import ConstitutionAuthority, canonical_json
from agf_orchestrator.owner_authority import (
    PINNED_OWNER_FINGERPRINT,
    canonical_bytes,
)
from agf_orchestrator.policy_authority import PolicyAuthority
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
    if (
        not isinstance(authority.policy_snapshot, dict)
        or state.policy_generation != authority.policy_snapshot.get("generation")
    ):
        raise RuntimeError("provider candidate policy generation is stale")
    evidence = dict(state.gate_evidence)
    expected_policy_evidence = (
        f"active-policy:{authority.policy.policy_id}:{authority.policy.policy_hash}"
    )
    if evidence.get("policy_eligible") != expected_policy_evidence:
        raise RuntimeError("provider candidate policy evidence is stale")
    envelope = sign_envelope(state._unsigned(), Path.home() / ".agf-owner-root")
    unsigned_signed = state.__class__(
        **{
            **state.__dict__,
            "signing_key_id": envelope["key_id"],
            "signature": envelope,
            "state_sha256": "0" * 64,
        }
    )
    state_hash = hashlib.sha256(
        json.dumps(
            unsigned_signed._unsigned(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    signed = unsigned_signed.__class__(**{**unsigned_signed.__dict__, "state_sha256": state_hash})
    ProviderIntelligenceStore().for_project(project_id).save(signed)
    return {"project_id": project_id, "state_sha256": signed.state_sha256}


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
    args = parser.parse_args()
    result = prepare_root(args.project, args.operation_id, Path.home() / ".agf-owner-root")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
