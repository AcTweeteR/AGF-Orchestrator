import base64
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

import pytest

from agf_orchestrator.constitution import canonical_json
from agf_orchestrator.policy_authority import (
    EffectiveRisk,
    PolicyActivationError,
    PolicyAuthority,
)
from tools.owner_policy_controller import OwnerPolicyController

PROJECT = "project-efc8e8ef7be7050b"
KEY = b"owner-key-material-that-is-at-least-32-bytes-long"


def _authority_state(home: Path) -> None:
    authority = home / ".agf-orchestrator" / "constitution-authority"
    constitution = home / ".agf-orchestrator" / "projects" / PROJECT / "constitution"
    authority.mkdir(parents=True)
    constitution.mkdir(parents=True)
    authority.chmod(0o700)
    (home / ".agf-orchestrator").chmod(0o700)
    (authority / "owner.key").write_text(base64.b64encode(KEY).decode("ascii"))
    (authority / "owner.key").chmod(0o600)
    unsigned = {
        "schema_version": "1.0",
        "constitution_id": "constitution-v1",
        "version": "1.0",
        "project_id": PROJECT,
        "compatibility": "agf-constitution-v1",
        "approval_status": "APPROVED",
        "body": {"protected": True},
        "key_id": "owner-key-1",
    }
    record = {
        **unsigned,
        "signature": hmac.new(KEY, canonical_json(unsigned), hashlib.sha256).hexdigest(),
    }
    record_hash = hashlib.sha256(canonical_json(record)).hexdigest()
    (constitution / "constitution-v1.json").write_text(json.dumps(record))
    (constitution / "active.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "project_id": PROJECT,
                "constitution_id": "constitution-v1",
                "record_hash": record_hash,
            }
        )
    )


@pytest.fixture
def authority(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _authority_state(tmp_path)
    return PolicyAuthority()


def test_external_activation_is_signed_and_effective(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-activation")
    first = authority.resolve(PROJECT)
    assert first.allows_autonomous_merge(EffectiveRisk.LOW)
    assert first.allows_autonomous_merge(EffectiveRisk.MEDIUM)
    assert first.allows_autonomous_merge(EffectiveRisk.HIGH)
    assert not first.allows_autonomous_merge(EffectiveRisk.CRITICAL)
    assert first.requires_human_merge(EffectiveRisk.CRITICAL)
    assert first.requires_human_merge(EffectiveRisk.UNKNOWN)
    assert (authority.state_dir / "policy-state.sqlite3").stat().st_mode & 0o077 == 0


def test_runtime_has_no_activation_api():
    assert not hasattr(PolicyAuthority, "activate")


def test_tampered_policy_hash_fails_closed(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-tamper")
    with sqlite3.connect(authority.state_dir / "policy-state.sqlite3") as db:
        db.execute("UPDATE active_state SET active_policy_hash=? WHERE project_id=?",
                   ("0" * 64, PROJECT))
        db.commit()
    with pytest.raises(PolicyActivationError, match="POLICY_NOT_ACTIVATED"):
        authority.resolve(PROJECT)


def test_invalid_policy_signature_fails_closed(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-signature")
    with sqlite3.connect(authority.state_dir / "policy-state.sqlite3") as db:
        db.execute("UPDATE policies SET artifact_json=? WHERE project_id=?",
                   (json.dumps({"signature": "0" * 64}), PROJECT))
        db.commit()
    with pytest.raises(PolicyActivationError, match="POLICY_NOT_ACTIVATED"):
        authority.resolve(PROJECT)


def test_invalid_activation_signature_fails_closed(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-activation-signature")
    with sqlite3.connect(authority.state_dir / "policy-state.sqlite3") as db:
        db.execute("UPDATE activations SET record_json=? WHERE project_id=?",
                   (json.dumps({"signature": "0" * 64}), PROJECT))
        db.commit()
    with pytest.raises(PolicyActivationError, match="POLICY_NOT_ACTIVATED"):
        authority.resolve(PROJECT)


def test_wrong_project_binding_and_missing_activation_fail_closed(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-binding")
    with sqlite3.connect(authority.state_dir / "policy-state.sqlite3") as db:
        db.execute("UPDATE activations SET project_id=?, record_json=? WHERE project_id=?",
                   ("project-0000000000000001",
                    json.dumps({"project_id": "project-0000000000000001"}), PROJECT))
        db.commit()
    with pytest.raises(PolicyActivationError, match="POLICY_NOT_ACTIVATED"):
        authority.resolve(PROJECT)


def test_rollback_is_owner_controller_only_and_removes_active_state(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-rollback")
    receipt = controller.rollback(PROJECT, "operation-adr-0003-rollback-now")
    assert receipt["project_id"] == PROJECT
    with pytest.raises(PolicyActivationError, match="POLICY_NOT_ACTIVATED"):
        authority.resolve(PROJECT)


def test_risk_names_are_case_insensitive_and_unknown_fails_closed(authority):
    controller = OwnerPolicyController()
    controller.prepare(PROJECT)
    controller.activate(PROJECT, "operation-adr-0003-risk")
    active = authority.resolve(PROJECT)
    assert active.allows_autonomous_merge("high")
    assert active.requires_human_merge("not-a-risk")


def test_invalid_project_identity_is_rejected_before_path_lookup(authority):
    with pytest.raises(PolicyActivationError, match="invalid project identity"):
        authority.resolve_or_none("../../outside")
