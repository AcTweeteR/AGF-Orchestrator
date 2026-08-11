import base64
import hashlib
import hmac
import json
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agf_orchestrator.owner_authority as owner_authority
from agf_orchestrator.authority_context import AuthorityContext, AuthorityContextError
from agf_orchestrator.authority_generation import (
    COMPONENTS,
    AuthorityComponent,
    AuthorityGenerationError,
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)

PROJECT = "project-efc8e8ef7be7050b"
HASH = "a" * 64
LEGACY_KEY = b"legacy-test-owner-key-which-is-long-enough"


def generation(status=GenerationStatus.VERIFIED, scheme="HMAC-SHA256"):
    components = tuple(
        AuthorityComponent(name, "generation-2", HASH, scheme, PROJECT, HASH) for name in COMPONENTS
    )
    value = build_generation(
        generation_id="generation-2",
        project_id=PROJECT,
        scheme=scheme,
        owner_key_id="owner-ed25519-1",
        owner_fingerprint=HASH,
        constitution_id="constitution-v1",
        constitution_hash=HASH,
        policy_hash=HASH,
        operation_id="operation-migration-2",
        status=status,
        components=components,
    )
    if scheme == "HMAC-SHA256":
        signature = hmac.new(
            LEGACY_KEY,
            json.dumps(value._unsigned(), sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        value = replace(value, signature=signature)
    return value


def test_complete_generation_round_trips_and_selector_is_single_source(tmp_path):
    store = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    value = generation()
    store._save_prepared_owner_controlled(value)
    store._activate_owner_controlled(PROJECT, value.generation_id)
    active = store.active(PROJECT)
    assert active.status is GenerationStatus.ACTIVE
    assert active.generation_id == value.generation_id


def test_incomplete_or_mixed_generation_fails_closed(tmp_path):
    value = generation()
    bad = build_generation(
        **{**value.__dict__, "components": value.components[:-1], "manifest_hash": "0" * 64}
    )
    with pytest.raises(AuthorityGenerationError):
        bad.validate()
    mixed = list(value.components)
    mixed[0] = AuthorityComponent(
        mixed[0].name, mixed[0].generation_id, HASH, "Ed25519", PROJECT, HASH
    )
    bad_mixed = build_generation(
        **{**value.__dict__, "components": tuple(mixed), "manifest_hash": "0" * 64}
    )
    with pytest.raises(AuthorityGenerationError):
        bad_mixed.validate()


def test_prepared_generation_cannot_be_activated(tmp_path):
    store = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    value = generation(GenerationStatus.PREPARED)
    store._save_prepared_owner_controlled(value)
    with pytest.raises(AuthorityGenerationError):
        store._activate_owner_controlled(PROJECT, value.generation_id)


def test_selector_tamper_fails_closed(tmp_path):
    store = AuthorityGenerationStore(tmp_path, legacy_signing_key=LEGACY_KEY)
    value = generation()
    store._save_prepared_owner_controlled(value)
    store._activate_owner_controlled(PROJECT, value.generation_id)
    path = tmp_path / "authority-generations" / PROJECT / "active.json"
    current = store.active(PROJECT)
    path.write_text(path.read_text().replace(current.manifest_hash, "b" * 64))
    with pytest.raises(AuthorityGenerationError):
        store.active(PROJECT)


def test_ed25519_manifest_is_verified_by_pinned_public_anchor(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    root = tmp_path / "owner-root"
    root.mkdir(mode=0o700)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode())
    (root / "anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "signature_scheme": "Ed25519",
                "key_id": "owner-ed25519-1",
                "fingerprint": owner_authority.fingerprint(public),
            }
        )
    )
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(
        owner_authority, "PINNED_OWNER_FINGERPRINT", owner_authority.fingerprint(public)
    )
    value = generation(scheme="Ed25519")
    value = replace(
        value,
        components=tuple(
            replace(item, artifact_signature={"placeholder": True}) for item in value.components
        ),
    )
    value = replace(
        value,
        manifest_hash=__import__("hashlib")
        .sha256(owner_authority.canonical_bytes(value._unsigned()))
        .hexdigest(),
    )
    signature = {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": "owner-ed25519-1",
        "public_key_fingerprint": owner_authority.fingerprint(public),
        "payload_hash": hashlib.sha256(
            owner_authority.canonical_bytes(value._unsigned())
        ).hexdigest(),
        "signature": base64.b64encode(
            private.sign(owner_authority.canonical_bytes(value._unsigned()))
        ).decode(),
    }
    signed = replace(value, signature=signature)
    AuthorityGenerationStore(tmp_path / "state")._save_prepared_owner_controlled(signed)
    assert AuthorityGenerationStore(tmp_path / "state").load(PROJECT, "generation-2") == signed


def test_context_rejects_missing_or_substituted_real_artifact(tmp_path):
    artifacts = {name: {"component": name, "project_id": PROJECT} for name in COMPONENTS}
    components = tuple(
        AuthorityComponent(
            name,
            "generation-2",
            hashlib.sha256(
                json.dumps(artifacts[name], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "HMAC-SHA256",
            PROJECT,
            hashlib.sha256(
                json.dumps(artifacts[name], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            f"{name}.json",
        )
        for name in COMPONENTS
    )
    value = build_generation(
        generation_id="generation-2",
        project_id=PROJECT,
        scheme="HMAC-SHA256",
        owner_key_id="owner-key-1",
        owner_fingerprint=HASH,
        constitution_id="constitution-v1",
        constitution_hash=components[0].semantic_hash,
        policy_hash=components[1].semantic_hash,
        operation_id="operation-2",
        status=GenerationStatus.VERIFIED,
        components=components,
    )
    store = AuthorityGenerationStore(tmp_path / "state", legacy_signing_key=LEGACY_KEY)
    store._save_prepared_owner_controlled(value)
    store._activate_owner_controlled(PROJECT, value.generation_id)
    with pytest.raises(AuthorityContextError):
        AuthorityContext.resolve(store, PROJECT)
    altered = dict(artifacts, policy={"component": "tampered"})
    with pytest.raises(AuthorityContextError):
        AuthorityContext.resolve(store, PROJECT, artifacts=altered)


def test_disposable_ed25519_cutover_and_old_generation_replay_fail_closed(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    root = tmp_path / "owner-root"
    root.mkdir(mode=0o700)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode())
    (root / "anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "signature_scheme": "Ed25519",
                "key_id": "owner-ed25519-1",
                "fingerprint": owner_authority.fingerprint(public),
            }
        )
    )
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(
        owner_authority, "PINNED_OWNER_FINGERPRINT", owner_authority.fingerprint(public)
    )
    artifacts = {name: {"component": name, "generation": 2} for name in COMPONENTS}

    def envelope(payload):
        raw = owner_authority.canonical_bytes(payload)
        return {
            "signature_scheme": "Ed25519",
            "signature_version": "1",
            "key_id": "owner-ed25519-1",
            "public_key_fingerprint": owner_authority.fingerprint(public),
            "payload_hash": hashlib.sha256(raw).hexdigest(),
            "signature": base64.b64encode(private.sign(raw)).decode(),
        }

    components = tuple(
        AuthorityComponent(
            name,
            "generation-2",
            hashlib.sha256(owner_authority.canonical_bytes(artifacts[name])).hexdigest(),
            "Ed25519",
            PROJECT,
            hashlib.sha256(owner_authority.canonical_bytes(artifacts[name])).hexdigest(),
            f"{name}.json",
            envelope(artifacts[name]),
        )
        for name in COMPONENTS
    )
    prepared = build_generation(
        generation_id="generation-2",
        project_id=PROJECT,
        scheme="Ed25519",
        owner_key_id="owner-ed25519-1",
        owner_fingerprint=owner_authority.fingerprint(public),
        constitution_id="constitution-v1",
        constitution_hash=components[0].semantic_hash,
        policy_hash=components[1].semantic_hash,
        operation_id="operation-2",
        status=GenerationStatus.VERIFIED,
        components=components,
    )
    prepared = replace(prepared, signature=envelope(prepared._unsigned()))
    store = AuthorityGenerationStore(tmp_path / "state")
    store._save_prepared_owner_controlled(prepared)
    active_unsigned = build_generation(
        **{**prepared.__dict__, "status": GenerationStatus.ACTIVE, "signature": None}
    )
    store._activate_owner_controlled(
        PROJECT,
        prepared.generation_id,
        active_signature=envelope(active_unsigned._unsigned()),
    )
    context = AuthorityContext.resolve(store, PROJECT, artifacts=artifacts)
    assert context.scheme == "Ed25519"
    assert context.generation_number == 2
    with pytest.raises(AuthorityGenerationError, match="ready|downgrade|replay"):
        store._activate_owner_controlled(PROJECT, prepared.generation_id)
