import base64
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator import owner_authority
from agf_orchestrator.authority_generation import (
    COMPONENTS,
    AuthorityComponent,
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)
from agf_orchestrator.objective_acceptance import (
    ObjectiveAcceptanceError,
    content_hash,
    criterion_statements,
    read_objective_acceptance,
)
from agf_orchestrator.objective_models import objective_from_dict, objective_hash
from agf_orchestrator.remote_identity import canonical_remote_identity


def contract(project, session):
    objective = objective_from_dict({
        "schema_version": "1.0", "objective_id": "objective-calculator",
        "title": "Correct addition", "statement": session.goal,
        "requirements": [{"requirement_id": "requirement-add", "statement": "Correct addition",
                          "mandatory": True, "acceptance_criteria": ["Addition returns sum"]}],
        "constraints": [], "prohibited_outcomes": [],
        "completion_criteria": ["The integrated calculator passes verification"],
        "owner_namespace": "fixture-owner", "status": "APPROVED",
    })
    return {
        "schema_version": "1.0", "generation_id": "generation-2",
        "project_id": project.project_id, "session_id": session.session_id,
        "repository_identity": canonical_remote_identity(project.origin_url),
        "goal_sha256": content_hash(" ".join(session.goal.split())),
        "objective": objective.to_dict(), "objective_sha256": objective_hash(objective),
        "criteria": [{"criterion_id": key, "statement_sha256": content_hash(text),
                      "method": "validation", "task_ids": ["task-001"],
                      "validation_commands": ["git diff --check"]}
                     for key, text in criterion_statements(objective).items()],
    }


def install_fixture_generation(tmp_path, monkeypatch, payload, *, schema="2.0", policy_state=False):
    """Simulate only an external owner in an isolated test home; no production keys."""
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    fingerprint = owner_authority.fingerprint(public)
    anchor = tmp_path / "fixture-owner"
    anchor.mkdir(mode=0o700)
    (anchor / "owner-public.key").write_text(base64.b64encode(public).decode())
    (anchor / "anchor.json").write_text(json.dumps({
        "schema_version": "1.0", "signature_scheme": "Ed25519",
        "key_id": "fixture-owner", "fingerprint": fingerprint,
    }))
    monkeypatch.setattr(owner_authority, "DEFAULT_ROOT", anchor)
    monkeypatch.setattr(owner_authority, "PINNED_OWNER_FINGERPRINT", fingerprint)

    def sign(value):
        data = owner_authority.canonical_bytes(value)
        return {"signature_scheme": "Ed25519", "signature_version": "1",
                "key_id": "fixture-owner", "public_key_fingerprint": fingerprint,
                "payload_hash": content_hash(value),
                "signature": base64.b64encode(key.sign(data)).decode()}

    root = Path.home() / ".agf-orchestrator"
    root.mkdir(exist_ok=True)
    artifacts = {name: {"fixture_component": name} for name in COMPONENTS}
    artifacts["constitution"] = {
        "constitution_id": "fixture-constitution", "version": "1", "compatibility": "1",
        "approval_status": "APPROVED", "key_id": "fixture-owner", "body": {},
    }
    artifacts["policy"] = {
        "policy_id": "fixture-policy", "version": "1", "key_id": "fixture-owner",
        "body": {"freshness_limits": {}},
        "project_id": payload["project_id"], "schema_version": "1.0",
        "compatibility": "fixture", "signature": "component-signed",
        "created_at": "2026-09-08T00:00:00Z",
    }
    artifacts["activation"] = {
        "rollback_target": "fixture-previous", "project_id": payload["project_id"],
        "policy_id": "fixture-policy", "policy_hash": content_hash(artifacts["policy"]),
        "previous_policy_hash": "a" * 64, "activation_time": "2026-09-08T00:00:00Z",
        "operation_id": "fixture-activation", "signature": "component-signed",
    }
    if schema == "2.0":
        artifacts["objective_acceptance"] = payload
    components = []
    for name, value in artifacts.items():
        (root / f"{name}.json").write_text(json.dumps(value))
        components.append(AuthorityComponent(
            name, "generation-2", content_hash(value), "Ed25519", payload["project_id"],
            content_hash(value), f"{name}.json", sign(value),
        ))
    generation = build_generation(
        generation_id="generation-2", project_id=payload["project_id"], scheme="Ed25519",
        owner_key_id="fixture-owner", owner_fingerprint=fingerprint,
        constitution_id="fixture-constitution",
        constitution_hash=content_hash(artifacts["constitution"]),
        policy_hash=content_hash(artifacts["policy"]), operation_id="fixture-operation",
        status=GenerationStatus.VERIFIED, components=tuple(components), schema_version=schema,
    )
    generation = replace(generation, signature=sign(generation._unsigned()))
    store = AuthorityGenerationStore(root)
    store._save_prepared_owner_controlled(generation)
    active = build_generation(**{**generation.__dict__, "status": GenerationStatus.ACTIVE})
    store._activate_owner_controlled(
        payload["project_id"], generation.generation_id, active_signature=sign(active._unsigned()),
    )
    if policy_state:
        from agf_orchestrator.policy_state_store import PolicyStateStore

        policies = PolicyStateStore(root)
        policies.prepare(artifacts["policy"], content_hash(artifacts["policy"]))
        policies.activate(payload["project_id"], artifacts["policy"], artifacts["activation"])
        policies.bootstrap_authority(payload["project_id"], generation=2)
    return root, sign


def identities():
    project = SimpleNamespace(project_id="project-0123456789abcdef",
                              origin_url="https://github.com/example/calculator")
    session = SimpleNamespace(project_id=project.project_id, session_id="session-fixture",
                              goal="Correct calculator addition")
    return project, session


def test_installed_signed_v2_component_has_exact_mandatory_coverage(tmp_path, monkeypatch):
    project, session = identities()
    payload = contract(project, session)
    install_fixture_generation(tmp_path, monkeypatch, payload)
    result = read_objective_acceptance(project, session)
    assert result.objective_sha256 == payload["objective_sha256"]
    assert len(result.criteria) == 2
    assert result.generation_id == "generation-2"


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "text", "session", "goal", "repository", "method"],
)
def test_even_signed_bad_mapping_fails_closed(tmp_path, monkeypatch, case):
    project, session = identities()
    payload = contract(project, session)
    if case == "missing":
        payload["criteria"].pop()
    elif case == "duplicate":
        payload["criteria"][1] = payload["criteria"][0]
    elif case == "text":
        payload["criteria"][0]["statement_sha256"] = "a" * 64
    elif case == "session":
        payload["session_id"] = "session-other"
    elif case == "goal":
        payload["goal_sha256"] = "a" * 64
    elif case == "repository":
        payload["repository_identity"] = "github.com/example/other"
    else:
        payload["criteria"][0]["method"] = "caller-approved"
    install_fixture_generation(tmp_path, monkeypatch, payload)
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, session)


def test_legacy_generation_cannot_authorize_objective_closure(tmp_path, monkeypatch):
    project, session = identities()
    install_fixture_generation(tmp_path, monkeypatch, contract(project, session), schema="1.0")
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, session)


def test_tampered_objective_file_is_not_approved_by_its_status(tmp_path, monkeypatch):
    project, session = identities()
    payload = contract(project, session)
    root, _ = install_fixture_generation(tmp_path, monkeypatch, payload)
    payload["objective"]["statement"] = "Different approved claim"
    (root / "objective_acceptance.json").write_text(json.dumps(payload))
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, session)


@pytest.mark.parametrize("field", ["constraints", "prohibited_outcomes"])
def test_nonempty_constraints_cannot_be_omitted_from_signed_mapping(tmp_path, monkeypatch, field):
    project, session = identities()
    payload = contract(project, session)
    payload["objective"][field] = ["Preserve subtraction behavior"]
    objective = objective_from_dict(payload["objective"])
    payload["objective_sha256"] = objective_hash(objective)
    install_fixture_generation(tmp_path, monkeypatch, payload)
    with pytest.raises(ObjectiveAcceptanceError):
        read_objective_acceptance(project, session)
