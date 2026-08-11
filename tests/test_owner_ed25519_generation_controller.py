import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator import owner_authority
from agf_orchestrator.authority_generation import (
    AuthorityComponent,
    AuthorityGenerationError,
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)
from tools import owner_ed25519_authority as controller


def test_cutover_rejects_path_escape_before_reading_state():
    with pytest.raises(AuthorityGenerationError, match="project id"):
        controller.cutover_ed25519_generation("../project", "generation-1")
    with pytest.raises(AuthorityGenerationError, match="generation id"):
        controller.cutover_ed25519_generation("project-0123456789abcdef", "../generation-1")


def test_cutover_rejects_readiness_bound_to_another_generation(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    generation_id = "generation-2"
    component = SimpleNamespace(name="constitution", artifact_hash="a" * 64)
    generation = SimpleNamespace(
        project_id=project_id,
        generation_id=generation_id,
        generation_number=2,
        scheme="Ed25519",
        owner_fingerprint=controller.PINNED_OWNER_FINGERPRINT,
        manifest_hash="b" * 64,
        components=(component,),
        predecessor_id="legacy-hmac-generation-1",
        predecessor_hash="c" * 64,
        operation_id="operation-2",
    )
    monkeypatch.setattr(controller, "_migration_state_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "_verify_prepared_generation", lambda *_: (generation, None))
    monkeypatch.setattr(
        "agf_orchestrator.owner_authority.verify_envelope", lambda *_: None
    )
    monkeypatch.setattr(
        controller,
        "_read_object",
        lambda _path: {
            "project_id": project_id,
            "generation_id": "generation-3",
            "generation_number": 3,
            "manifest_hash": generation.manifest_hash,
            "component_hashes": {"constitution": component.artifact_hash},
            "predecessor_id": generation.predecessor_id,
            "predecessor_hash": generation.predecessor_hash,
            "owner_fingerprint": generation.owner_fingerprint,
            "operation_id": generation.operation_id,
            "current_selector": None,
            "current_floor": 0,
            "signature": {},
        },
    )
    with pytest.raises(RuntimeError, match="not bound"):
        controller.cutover_ed25519_generation(project_id, generation_id)


def test_verify_generation_persists_signed_readiness_for_all_components(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    generation_id = "generation-2"
    components = tuple(
        SimpleNamespace(name=name, artifact_hash=f"{index:064x}")
        for index, name in enumerate(
            (
                "constitution", "policy", "activation", "rollback", "registration",
                "provider_intelligence",
            ),
            1,
        )
    )
    generation = SimpleNamespace(
        generation_id=generation_id,
        generation_number=2,
        manifest_hash="b" * 64,
        components=components,
        predecessor_id="legacy-hmac-generation-1",
        predecessor_hash="c" * 64,
        operation_id="operation-2",
    )
    monkeypatch.setattr(controller, "_migration_state_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "_verify_prepared_generation", lambda *_: (generation, None))
    monkeypatch.setattr(controller, "sign_envelope", lambda payload, _root: {"payload": payload})
    result = controller.verify_ed25519_generation(project_id, generation_id)
    assert result["status"] == "GENERATION_READY_FOR_CUTOVER"
    assert result["component_hashes"]["provider_intelligence"] == f"{6:064x}"
    readiness_path = (
        tmp_path / "authority-generations" / project_id / generation_id / "readiness.json"
    )
    assert readiness_path.exists()


def test_prepare_generation_uses_verified_floor(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    floor_path = tmp_path / "floor.json"
    floor_path.write_text(
        '{"schema_version":"1.0","project_id":"project-0123456789abcdef",'
        '"generation_number":7}'
    )
    generation = SimpleNamespace(
        to_dict=lambda: {"generation_id": "generation-8", "manifest_hash": "a" * 64},
        generation_id="generation-8",
        generation_number=8,
        manifest_hash="a" * 64,
    )
    class Project:
        status = SimpleNamespace(value="ACTIVE")

    class FakeStore:
        saved = False

        def __init__(self, _root, read_only=False):
            self.read_only = read_only

        def snapshot(self, _project):
            return {"generation": 3}

        def _floor(self, _project):
            return 7

        def _recover_metadata(self, _project):
            return None

        def _floor_path(self, _project):
            return floor_path

        def _directory(self, _project):
            return tmp_path / "missing-generations"

        def _generation_path(self, _project, _generation):
            return tmp_path / "missing"

        def _save_prepared_owner_controlled(self, _generation):
            self.saved = True

    monkeypatch.setattr(
        controller, "ProjectRegistry", lambda: SimpleNamespace(get=lambda _id: Project())
    )
    monkeypatch.setattr(
        controller,
        "PolicyAuthority",
        lambda: SimpleNamespace(
            resolve=lambda _id: SimpleNamespace(policy_id="merge-policy-adr-0003")
        ),
    )
    monkeypatch.setattr(controller, "PolicyStateStore", FakeStore)
    monkeypatch.setattr(controller, "AuthorityGenerationStore", FakeStore)
    monkeypatch.setattr(controller, "_build_prepared_generation", lambda _p, gid, _o: generation)
    first = controller.prepare_ed25519_generation(project_id, "operation-8")
    assert first["generation_id"] == "generation-8"
    assert first["generation_number"] == 8


def test_prepare_reuses_active_operation_instead_of_replaying(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    generation_path = tmp_path / "generation-8.json"
    generation_path.write_text("{}")

    class Project:
        status = SimpleNamespace(value="ACTIVE")

    class FakeStore:
        def __init__(self, _root, read_only=False):
            self.read_only = read_only

        def snapshot(self, _project):
            return {"generation": 3}

        def _directory(self, _project):
            return tmp_path

        def load(self, _project, _generation):
            return SimpleNamespace(
                operation_id="operation-8", status=GenerationStatus.ACTIVE,
                generation_id="generation-8", generation_number=8, manifest_hash="a" * 64,
            )

    monkeypatch.setattr(
        controller, "ProjectRegistry", lambda: SimpleNamespace(get=lambda _id: Project())
    )
    monkeypatch.setattr(
        controller,
        "PolicyAuthority",
        lambda: SimpleNamespace(
            resolve=lambda _id: SimpleNamespace(policy_id="merge-policy-adr-0003")
        ),
    )
    monkeypatch.setattr(controller, "PolicyStateStore", FakeStore)
    monkeypatch.setattr(controller, "AuthorityGenerationStore", FakeStore)
    result = controller.prepare_ed25519_generation(project_id, "operation-8")
    assert result["status"] == "ALREADY_ACTIVE"


def test_real_verify_and_cutover_round_trip_is_atomic(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    generation_id = "generation-2"
    root = tmp_path / "owner-root"
    root.mkdir(mode=0o700)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    fingerprint = hashlib.sha256(public).hexdigest()
    private_path = root / "owner-private.key"
    private_path.write_text(
        base64.b64encode(private.private_bytes_raw()).decode("ascii")
    )
    private_path.chmod(0o600)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode("ascii"))
    (root / "anchor.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "signature_scheme": "Ed25519",
                "key_id": "owner-ed25519-1",
                "fingerprint": fingerprint,
            }
        )
    )
    monkeypatch.setattr(controller, "PINNED_OWNER_FINGERPRINT", fingerprint)
    monkeypatch.setattr(owner_authority, "PINNED_OWNER_FINGERPRINT", fingerprint)
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", root)
    monkeypatch.setattr(controller, "_migration_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(controller, "_generation_root", lambda: root)
    state_dir = tmp_path / "state"
    artifacts = {}
    components = []
    names = (
        "constitution", "policy", "activation", "rollback", "registration",
        "provider_intelligence",
    )
    for name in names:
        value = {"project_id": project_id, "component": name}
        relative = f"authority-generations/{project_id}/{generation_id}/{name}.json"
        artifact_path = state_dir / relative
        controller._write_object(artifact_path, value)
        artifact_hash = controller._object_hash(value)
        artifacts[name] = value
        components.append(
            AuthorityComponent(
                name=name, generation_id=generation_id, artifact_hash=artifact_hash,
                scheme="Ed25519", project_id=project_id, semantic_hash=artifact_hash,
                artifact_path=relative,
                artifact_signature=controller.sign_envelope(value, root),
            )
        )
    generation = build_generation(
        generation_id=generation_id, project_id=project_id, scheme="Ed25519",
        owner_key_id="owner-ed25519-1", owner_fingerprint=fingerprint,
        constitution_id="constitution-v1", constitution_hash=components[0].semantic_hash,
        policy_hash=components[1].semantic_hash, operation_id="operation-2",
        status=GenerationStatus.VERIFIED, components=tuple(components),
        predecessor_id="legacy-hmac-generation-1", predecessor_hash="d" * 64,
    )
    generation_signature = controller.sign_envelope(generation._unsigned(), root)
    generation = generation.__class__(
        **{**generation.__dict__, "signature": generation_signature}
    )
    store = AuthorityGenerationStore(state_dir)
    store._save_prepared_owner_controlled(generation)
    readiness = controller.verify_ed25519_generation(project_id, generation_id)
    assert readiness["status"] == "GENERATION_READY_FOR_CUTOVER"
    result = controller.cutover_ed25519_generation(project_id, generation_id)
    assert result["status"] == "CUTOVER_COMPLETE"
    assert store.active(project_id).status is GenerationStatus.ACTIVE
