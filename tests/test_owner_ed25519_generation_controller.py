import base64
import hashlib
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
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
from agf_orchestrator.capability_profiles import CapabilityStatus
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.provider_intelligence import (
    ARCHITECT_REQUIREMENTS,
    build_state,
    make_profile,
)
from tools import owner_ed25519_authority as controller

TEST_OWNER_KEY = b"test-owner-key-which-is-long-enough-123456"


def test_normal_provider_activation_does_not_authorize_renewal(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(controller, "project_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        controller,
        "_activate_provider_candidate",
        lambda project_id, candidate, **kwargs: calls.append(kwargs) or {"ok": "true"},
    )
    assert controller.activate_provider_candidate(
        "project-0123456789abcdef", tmp_path / "candidate"
    )
    assert calls == [{"allow_renewal": False}]


def test_provider_renewal_requires_fresh_observation_for_each_profile():
    previous = SimpleNamespace(
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex", observed_at="2026-08-13T12:00:00Z"
                )
            ),
        )
    )
    proposed = SimpleNamespace(
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex", observed_at="2026-08-13T12:00:00Z"
                )
            ),
        )
    )
    with pytest.raises(RuntimeError, match="fresh profile observations"):
        controller._require_fresh_profile_observations(previous, proposed)


def test_target_advancement_requires_exact_delivery_evidence(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    previous = SimpleNamespace(target_sha="a" * 40)
    proposed = SimpleNamespace(target_sha="b" * 40)
    project = SimpleNamespace(
        repository_root=tmp_path,
        default_branch="main",
        origin_url="file:///tmp/target.git",
    )
    intent = SimpleNamespace(
        base_sha=previous.target_sha,
        candidate_sha=proposed.target_sha,
        target_branch="main",
        repository_identity="file:///tmp/target.git",
    )
    receipt = SimpleNamespace(observed_sha=proposed.target_sha)

    class FakeStore:
        root = tmp_path / "delivery-intents"

        def __init__(self, state_root):
            assert state_root == Path.home() / ".agf-orchestrator"
            self.root.joinpath(project_id).mkdir(parents=True, exist_ok=True)
            self.root.joinpath(project_id, "delivery-001.json").write_text("{}")

        def get(self, _project_id, _delivery_id):
            return intent

        def observe(self, _project_id, _delivery_id, _root):
            return receipt

    monkeypatch.setattr(controller, "DeliveryIntentStore", FakeStore)
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    controller._verify_legitimate_target_advancement(
        project_id, project, previous, proposed, proposed.target_sha
    )


def test_target_advancement_without_matching_delivery_fails_closed(monkeypatch, tmp_path):
    project = SimpleNamespace(
        repository_root=tmp_path,
        default_branch="main",
        origin_url="file:///tmp/target.git",
    )
    class FakeStore:
        root = tmp_path / "delivery-intents"

        def __init__(self, state_root):
            assert state_root == Path.home() / ".agf-orchestrator"
            self.root.joinpath("project-0123456789abcdef").mkdir(
                parents=True, exist_ok=True
            )

    monkeypatch.setattr(controller, "DeliveryIntentStore", FakeStore)
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(RuntimeError, match="authoritative delivery receipt"):
        controller._verify_legitimate_target_advancement(
            "project-0123456789abcdef",
            project,
            SimpleNamespace(target_sha="a" * 40),
            SimpleNamespace(target_sha="b" * 40),
            "b" * 40,
        )


def test_target_rollback_fails_closed(monkeypatch, tmp_path):
    project = SimpleNamespace(
        repository_root=tmp_path,
        default_branch="main",
        origin_url="file:///tmp/target.git",
    )
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, args[0])
        ),
    )
    with pytest.raises(RuntimeError, match="valid descendant"):
        controller._verify_legitimate_target_advancement(
            "project-0123456789abcdef",
            project,
            SimpleNamespace(target_sha="b" * 40),
            SimpleNamespace(target_sha="a" * 40),
            "a" * 40,
        )


def test_controller_rejects_historical_profile_without_mutating(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    previous = SimpleNamespace(
        project_id=project_id,
        target_sha="a" * 40,
        observed_at="2026-08-13T12:00:00Z",
        state_sha256="old",
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex",
                    profile_version=1,
                    observed_at="2026-08-13T12:00:00Z",
                )
            ),
        ),
    )
    proposed = SimpleNamespace(
        project_id=project_id,
        target_sha=previous.target_sha,
        observed_at="2026-08-13T12:01:00Z",
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex",
                    profile_version=2,
                    observed_at="2026-08-13T12:00:00Z",
                )
            ),
        ),
    )

    class FakeStore:
        def for_project(self, _project_id):
            return self

        def _load_for_owner_recovery(self):
            return previous

    monkeypatch.setattr(controller, "ProviderIntelligenceStore", FakeStore)
    monkeypatch.setattr(controller, "project_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(controller, "_candidate_path", lambda *_: tmp_path / "candidate.json")
    monkeypatch.setattr(controller, "state_from_dict", lambda _payload: proposed)
    monkeypatch.setattr(controller, "_activate_provider_candidate", pytest.fail)
    (tmp_path / "candidate.json").write_text("{}")
    with pytest.raises(RuntimeError, match="fresh profile observations"):
        controller.renew_provider_candidate(project_id, tmp_path / "candidate.json")


def test_controller_accepts_fresh_profile_and_authorizes_renewal(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    previous = SimpleNamespace(
        project_id=project_id,
        target_sha="a" * 40,
        observed_at="2026-08-13T12:00:00Z",
        state_sha256="old",
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex",
                    profile_version=1,
                    observed_at="2026-08-13T12:00:00Z",
                )
            ),
        ),
    )
    proposed = SimpleNamespace(
        project_id=project_id,
        target_sha=previous.target_sha,
        observed_at="2026-08-13T12:01:00Z",
        candidates=(
            SimpleNamespace(
                profile=SimpleNamespace(
                    profile_id="provider-codex",
                    profile_version=2,
                    observed_at="2026-08-13T12:01:00Z",
                )
            ),
        ),
    )

    class FakeStore:
        def for_project(self, _project_id):
            return self

        def _load_for_owner_recovery(self):
            return previous

        def load(self):
            return SimpleNamespace(state_sha256="new")

    calls = []
    monkeypatch.setattr(controller, "ProviderIntelligenceStore", FakeStore)
    monkeypatch.setattr(controller, "project_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(controller, "_candidate_path", lambda *_: tmp_path / "candidate.json")
    monkeypatch.setattr(controller, "state_from_dict", lambda _payload: proposed)
    monkeypatch.setattr(
        controller,
        "_activate_provider_candidate",
        lambda *args, **kwargs: calls.append(kwargs) or {"state_sha256": "new"},
    )
    (tmp_path / "candidate.json").write_text("{}")
    result = controller.renew_provider_candidate(project_id, tmp_path / "candidate.json")
    assert result["renewed"] == "true"
    assert calls == [{"allow_renewal": True}]


def test_controller_renewal_uses_real_store_and_replaces_atomically(monkeypatch, tmp_path):
    project_id = "project-0123456789abcdef"
    target = "a" * 40
    state_root = tmp_path / ".agf-orchestrator"
    monkeypatch.setenv("AGF_STATE_DIR", str(state_root))
    monkeypatch.setattr(controller.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "agf_orchestrator.provider_intelligence.verify_envelope", lambda *_: None
    )
    monkeypatch.setattr(
        controller, "sign_envelope", lambda *_: {"key_id": "owner-ed25519-1"}
    )
    monkeypatch.setattr(
        controller,
        "ProjectRegistry",
        lambda: SimpleNamespace(
            verify=lambda _project: SimpleNamespace(repository_root=tmp_path)
        ),
    )
    authority = SimpleNamespace(
        policy=SimpleNamespace(policy_id="merge-policy-adr-0003", policy_hash="p" * 64),
        constitution=SimpleNamespace(
            constitution_id="constitution-agf-v1", record_hash="c" * 64
        ),
        context=SimpleNamespace(generation_number=2),
        policy_snapshot={"generation": 2},
    )
    monkeypatch.setattr(controller, "resolve_authority", lambda _project: authority)
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=f"{target}\n"),
    )

    def make_provider_state(observed, version):
        profile = make_profile(
            project_id=project_id,
            provider_id="provider-codex",
            provenance_source="runtime-canary:codex:test-v1",
            observed_at=observed,
            expires_at="2030-01-01T00:00:00Z",
            capability_results={
                name: CapabilityStatus.SUPPORTED for name in ARCHITECT_REQUIREMENTS
            },
            profile_version=version,
        )
        value = build_state(
            project_id=project_id,
            target_sha=target,
            constitution_id="constitution-agf-v1",
            constitution_record_hash="c" * 64,
            observed_at=observed,
            expires_at="2030-01-01T00:00:00Z",
            candidates=(CapabilityCandidate(profile, priority=0),),
            provider_interfaces=(("provider-codex", "codex"),),
            gates=SelectionGates(True, True, True, True, True, True),
            gate_evidence=(
                ("policy_eligible", "active-policy:merge-policy-adr-0003:" + "p" * 64),
                ("privacy_eligible", "codex-safe-environment-v1;read-only-canary;True"),
                ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
                ("budget_eligible", "bounded-timeout-seconds:90;True"),
                ("health_eligible", "invocation-verified:True"),
                ("empirical_evidence_eligible", "deterministic-canary-sha256:" + "e" * 64),
            ),
            policy_generation=2,
        )
        signed = value.__class__(
            **{
                **value.__dict__,
                "signing_key_id": "owner-ed25519-1",
                "signature": {"key_id": "owner-ed25519-1"},
            }
        )
        return signed.__class__(
            **{
                **signed.__dict__,
                "state_sha256": hashlib.sha256(
                    json.dumps(signed._unsigned(), sort_keys=True, separators=(",", ":"))
                    .encode()
                ).hexdigest(),
            }
        )

    from agf_orchestrator.provider_intelligence import ProviderIntelligenceStore

    store = ProviderIntelligenceStore().for_project(project_id)
    old = make_provider_state("2026-08-13T12:00:00Z", 1)
    renewed = make_provider_state("2026-08-13T12:01:00Z", 2)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(old.to_dict()))
    candidate = state_root / "capability-intelligence" / project_id / "candidate.json"
    candidate.write_text(json.dumps(renewed.to_dict()))

    result = controller.renew_provider_candidate(project_id, candidate)

    assert result["renewed"] == "true"
    assert store._load_for_owner_recovery().state_sha256 == renewed.state_sha256

    failed_candidate = state_root / "capability-intelligence" / project_id / "failed.json"
    failed_candidate.write_text(
        json.dumps(make_provider_state("2026-08-13T12:02:00Z", 3).to_dict())
    )
    monkeypatch.setattr(
        "agf_orchestrator.provider_intelligence._atomic_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected write failure")),
    )
    with pytest.raises(OSError, match="injected write failure"):
        controller.renew_provider_candidate(project_id, failed_candidate)
    assert store._load_for_owner_recovery().state_sha256 == renewed.state_sha256




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
