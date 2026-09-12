"""External owner publication using the installed root; no implicit activation.

Preparation signs a verified candidate. Activation is a separate owner command
bound to the reviewed candidate manifest. The runtime never imports this tool.
"""

import argparse
import json
import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from agf_orchestrator.authority_context import AuthorityContext
from agf_orchestrator.authority_generation import (
    AuthorityComponent,
    AuthorityGenerationStore,
    GenerationStatus,
    build_generation,
)
from agf_orchestrator.locking import project_lock, session_lock
from agf_orchestrator.objective_acceptance import content_hash
from agf_orchestrator.owner_authority import verify_envelope
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.session_store import SessionStore
from tools import owner_ed25519_authority as owner
from tools.prepare_objective_acceptance import _prepare_proposal_locked


def _immutable(path, value, store):
    path = store.ensure_safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text() != encoded:
            raise RuntimeError("publication evidence cannot be overwritten")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".publication-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic creation without replacing an already committed record.
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _result(generation, status):
    return {"status": status, "project_id": generation.project_id,
            "generation_id": generation.generation_id,
            "manifest_hash": generation.manifest_hash,
            "operation_id": generation.operation_id}


def _validate_objective_succession(previous, component, session):
    stable_fields = {
        "project_id", "session_id", "repository_identity", "goal_sha256",
        "objective", "objective_sha256", "criteria",
    }
    if any(previous.get(field) != component.get(field) for field in stable_fields):
        raise RuntimeError("Objective succession changes accepted content")
    if previous.get("approved_plan_sha256") == component.get("approved_plan_sha256"):
        raise RuntimeError("Objective succession requires a freshly projected plan")
    if session.artifact_hashes.get("external_advancement") is None:
        raise RuntimeError("Objective succession requires owner-authorized target advance")


@contextmanager
def _final_validation(registry, project, sessions, session, proposal, store, predecessor):
    # Registry writers use a separate lock from project execution transactions.
    with registry._lock("owner-objective-final-validation"):
        def get_locked(identity):
            matches = [item for item in registry._load() if item.project_id == identity]
            if len(matches) != 1:
                raise RuntimeError("publication project registration is unavailable")
            return matches[0]

        if get_locked(project.project_id) != project:
            raise RuntimeError("project changed before publication commit")
        current = sessions.load(session.session_id)
        if current != session or store.active(project.project_id) != predecessor:
            raise RuntimeError("session or authority changed before publication commit")
        if _prepare_proposal_locked(current, project, proposal["component"], sessions,
                                    SimpleNamespace(get=get_locked)) != proposal:
            raise RuntimeError("canonical proposal changed before publication commit")
        yield


def publish(project_id, operation_id, proposal, *, action="prepare", expected_manifest=None):
    """Owner entry point. No keys or root locations are accepted from the caller."""
    if action not in {"prepare", "verify", "activate"}:
        raise ValueError("unknown publication action")
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200:
        raise ValueError("publication operation identity is invalid")
    if (not isinstance(proposal, dict)
            or set(proposal) != {"status", "authority_effect", "component", "projected_plan"}
            or proposal["status"] != "PROPOSAL" or proposal["authority_effect"] != "NONE"):
        raise ValueError("publication requires a complete unsigned proposal")
    component = proposal["component"]
    if not isinstance(component, dict) or component.get("schema_version") != "2.0":
        raise ValueError("publication requires first-plan-bound Objective schema 2.0")
    generation_id = component.get("generation_id")
    AuthorityGenerationStore._validate_project_id(project_id)
    AuthorityGenerationStore._validate_generation_id(generation_id)
    sessions = SessionStore()
    session_id = component.get("session_id")
    # Validate canonical identity before it is used to construct a lock path.
    session = sessions.load(session_id)
    if session.project_id != project_id or component.get("project_id") != project_id:
        raise ValueError("publication project/session binding differs")
    root = owner._migration_state_dir()
    safe = SessionStore(root)
    store = AuthorityGenerationStore(root)
    registry = ProjectRegistry(sessions.state_dir)
    with ExitStack() as locks:
        locks.enter_context(session_lock(sessions.state_dir, session_id, "owner-objective"))
        locks.enter_context(project_lock(sessions.state_dir, project_id, "owner-objective"))
        if root.resolve() != sessions.state_dir.resolve():
            locks.enter_context(project_lock(root, project_id, "owner-objective-authority"))
        session = sessions.load(session_id)
        project = registry.get(project_id)
        active = store.active(project_id)
        if active.scheme != "Ed25519" or active.owner_fingerprint != owner.PINNED_OWNER_FINGERPRINT:
            raise RuntimeError("publication requires the installed pinned owner generation")
        directory = safe.ensure_safe_path(store._directory(project_id) / generation_id)
        manifest_path = safe.ensure_safe_path(store._generation_path(project_id, generation_id))
        record_path = directory / "objective-publication.json"
        existing = store.load(project_id, generation_id) if manifest_path.exists() else None
        record = None
        if record_path.exists():
            record = json.loads(safe.ensure_safe_path(record_path).read_text())
            verify_envelope(record["payload"], record["envelope"])
            binding = record["payload"]
            if (binding.get("proposal") != proposal or binding.get("operation_id") != operation_id
                    or binding.get("project_id") != project_id):
                raise RuntimeError("publication operation or reviewed content differs")
        if existing is not None:
            if record is None or existing.operation_id != operation_id:
                raise RuntimeError("generation identity is already occupied")
            if existing.status is GenerationStatus.ACTIVE:
                # A completed activation may outlive the original planning state.
                # Verify its exact signed candidate rather than rerunning planning.
                verified = build_generation(**{**existing.__dict__,
                                               "status": GenerationStatus.VERIFIED,
                                               "manifest_hash": "0" * 64, "signature": None})
                if (active != existing
                        or record["payload"]["candidate_hash"] != verified.manifest_hash
                        or (action == "activate" and expected_manifest != verified.manifest_hash)):
                    raise RuntimeError("active publication differs from reviewed candidate")
                AuthorityContext._verify_artifacts(existing, artifact_root=root, artifacts=None)
                return _result(existing, "ALREADY_ACTIVE")
        if _prepare_proposal_locked(session, project, component, sessions, registry) != proposal:
            raise RuntimeError("proposal differs from current canonical planning content")
        context = AuthorityContext.resolve_runtime(project_id, root)
        if context is None or context.manifest_hash != active.manifest_hash:
            raise RuntimeError("current owner authority cannot be verified")
        values = dict(context.artifacts)
        base_components = {
            "constitution", "policy", "activation", "rollback", "registration",
            "provider_intelligence",
        }
        if frozenset(values) not in {
            frozenset(base_components),
            frozenset({*base_components, "objective_acceptance"}),
        }:
            raise RuntimeError("current generation has unsupported components")
        previous_objective = values.get("objective_acceptance")
        if previous_objective is not None:
            _validate_objective_succession(previous_objective, component, session)
        if record is not None and (
            record["payload"]["predecessor_id"] != active.generation_id
            or record["payload"]["predecessor_hash"] != active.manifest_hash
        ):
            raise RuntimeError("publication predecessor changed")
        if int(generation_id.removeprefix("generation-")) != active.generation_number + 1:
            raise RuntimeError("proposal does not name the next generation")
        values["objective_acceptance"] = component
        components = []
        for name, value in values.items():
            relative = (directory / f"{name}.json").relative_to(root)
            digest = content_hash(value)
            components.append(AuthorityComponent(
                name, generation_id, digest, "Ed25519", project_id, digest, str(relative),
                owner.sign_envelope(value, owner._generation_root()),
            ))
        candidate = build_generation(
            generation_id=generation_id, project_id=project_id, scheme="Ed25519",
            owner_key_id=active.owner_key_id, owner_fingerprint=active.owner_fingerprint,
            constitution_id=active.constitution_id, constitution_hash=active.constitution_hash,
            policy_hash=active.policy_hash, operation_id=operation_id,
            status=GenerationStatus.VERIFIED, components=tuple(components), schema_version="2.0",
            predecessor_id=active.generation_id, predecessor_hash=active.manifest_hash,
        )
        candidate = replace(candidate, signature=owner.sign_envelope(
            candidate._unsigned(), owner._generation_root(),
        ))
        binding = {"project_id": project_id, "operation_id": operation_id, "proposal": proposal,
                   "predecessor_id": active.generation_id, "predecessor_hash": active.manifest_hash,
                   "candidate_hash": candidate.manifest_hash}
        signed = {"payload": binding,
                  "envelope": owner.sign_envelope(binding, owner._generation_root())}
        if action == "prepare":
            with _final_validation(registry, project, sessions, session, proposal, store, active):
                _immutable(record_path, signed, safe)
                for name, value in values.items():
                    _immutable(directory / f"{name}.json", value, safe)
                store._save_prepared_owner_controlled_locked(candidate)
            return _result(candidate, "PREPARED_NOT_ACTIVE")
        if existing != candidate or record != signed:
            raise RuntimeError("prepared publication is absent or differs")
        AuthorityContext._verify_artifacts(candidate, artifact_root=root, artifacts=None)
        if action == "verify":
            with _final_validation(registry, project, sessions, session, proposal, store, active):
                return _result(candidate, "READY_FOR_OWNER_ACTIVATION")
        if expected_manifest != candidate.manifest_hash:
            raise RuntimeError("activation requires the exact reviewed manifest hash")
        active_candidate = build_generation(**{**candidate.__dict__,
                                               "status": GenerationStatus.ACTIVE,
                                               "manifest_hash": "0" * 64, "signature": None})
        signature = owner.sign_envelope(active_candidate._unsigned(), owner._generation_root())
        with _final_validation(registry, project, sessions, session, proposal, store, active):
            store._activate_owner_controlled_locked(project_id, generation_id,
                                                   active_signature=signature)
        return _result(store.active(project_id), "ACTIVATED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "activate"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--expected-manifest")
    args = parser.parse_args(argv)
    try:
        result = publish(args.project, args.operation_id, json.loads(args.proposal.read_text()),
                         action=args.action, expected_manifest=args.expected_manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Owner Objective publication failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
