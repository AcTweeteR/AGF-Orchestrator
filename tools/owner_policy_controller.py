"""Explicit owner-operated controller for ADR-0003 policy state.

This module is intentionally not imported by the AGF runtime.  The runtime
only consumes and verifies the artifacts produced here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agf_orchestrator.constitution import ConstitutionAuthority, canonical_json
from agf_orchestrator.policy_authority import (
    EffectiveRisk,
    PolicyAuthority,
    canonical_hash,
)
from agf_orchestrator.policy_state_store import PolicyStateError, PolicyStateStore

PROJECT_RE = re.compile(r"^project-[0-9a-f]{16}$")
OPERATION_RE = re.compile(r"^operation-[a-z0-9][a-z0-9-]{2,127}$")
POLICY_ID = "merge-policy-adr-0003"
POLICY_VERSION = "1.0"
SCHEMA = "1.0"
KEY_ID = "owner-key-1"
COMPATIBILITY = "agf-merge-policy-v1"
ROLLBACK_TARGET = "project-policy-require-human-merge"
AGF_0003_POLICY_HASH = "fd31b8964e66d867803020d81552d8c21f76c04a54b34d6a8ef7ede296efd6e4"
MANDATORY_GATES = (
    "constitution", "policy", "plan", "implementation", "review", "compliance",
    "validation", "risk", "caller_clean", "base_sha", "authorized_paths",
    "remote_state", "delivery_branch",
)


class OwnerControllerError(RuntimeError):
    """Raised for invalid owner-controller input or state."""


class OwnerPolicyController:
    """Owner-only state mutator; AGF runtime has no reference to this class."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or (Path.home() / ".agf-orchestrator")
        self.authority_dir = self.state_dir / "constitution-authority"
        self.store = PolicyStateStore(self.state_dir)

    def inspect(self, project_id: str) -> dict[str, Any]:
        self._validate_project(project_id)
        if not self.store.path.exists():
            return {"project_id": project_id, "candidate_present": False,
                    "active_present": False, "active_policy_id": None}
        try:
            reader = PolicyStateStore(self.state_dir, read_only=True)
            snapshot = reader.snapshot(project_id)
            candidate = reader.latest_policy(project_id, POLICY_ID)
        except PolicyStateError as exc:
            raise OwnerControllerError("transactional policy state is unreadable") from exc
        return {
            "project_id": project_id,
            "candidate_present": candidate is not None,
            "active_present": (
                snapshot is not None and snapshot.get("active_policy_hash") is not None
            ),
            "active_policy_id": None if snapshot is None else snapshot.get("active_policy_id"),
            "generation": None if snapshot is None else snapshot.get("generation"),
        }

    def prepare(self, project_id: str) -> dict[str, Any]:
        self._validate_project(project_id)
        constitution = ConstitutionAuthority().resolve(project_id)
        try:
            existing = self.store.latest_policy(project_id, POLICY_ID)
        except PolicyStateError as exc:
            raise OwnerControllerError("transactional policy state is unreadable") from exc
        if existing is not None:
            return existing
        previous = self._previous_identity(project_id)
        unsigned = {
            "schema_version": SCHEMA,
            "policy_id": POLICY_ID,
            "version": POLICY_VERSION,
            "project_id": project_id,
            "constitution_id": constitution.constitution_id,
            "compatibility": COMPATIBILITY,
            "approval_status": "APPROVED",
            "key_id": KEY_ID,
            "previous_policy": previous,
            "activation_time": self._now(),
            "body": {
                "risk_merge_behavior": {
                    EffectiveRisk.LOW.value: "AUTONOMOUS",
                    EffectiveRisk.MEDIUM.value: "AUTONOMOUS",
                    EffectiveRisk.HIGH.value: "AUTONOMOUS",
                    EffectiveRisk.CRITICAL.value: "HUMAN_REQUIRED",
                    EffectiveRisk.UNKNOWN.value: "HUMAN_REQUIRED",
                },
                "mandatory_gates": list(MANDATORY_GATES),
                "freshness_limits": {"policy_seconds": 86400, "gate_seconds": 3600},
                "protected_object_prohibitions": [
                    "constitution", "objective", "policy", "credential", "permission",
                    "merge-rule", "production", "financial", "destructive",
                ],
                "protected_boundary_uncertainty": "CRITICAL",
                "rollback_target": ROLLBACK_TARGET,
            },
        }
        policy = self._signed(unsigned)
        self.store.prepare(policy, canonical_hash(policy))
        return policy

    def activate(self, project_id: str, operation_id: str) -> dict[str, Any]:
        self._validate_project(project_id)
        if not OPERATION_RE.fullmatch(operation_id):
            raise OwnerControllerError("invalid activation operation identity")
        try:
            policy = self.store.latest_policy(project_id, POLICY_ID)
        except PolicyStateError as exc:
            raise OwnerControllerError("transactional policy state is unreadable") from exc
        if policy is None:
            raise OwnerControllerError("no prepared policy exists")
        authority = PolicyAuthority()
        authority._verify_policy(
            project_id,
            ConstitutionAuthority().resolve(project_id).constitution_id,
            {"schema_version": SCHEMA, "project_id": project_id,
             "policy_id": POLICY_ID, "policy_hash": canonical_hash(policy),
             "activation_hash": "0" * 64},
            policy,
        )
        policy_hash = canonical_hash(policy)
        previous = policy["previous_policy"]
        activation_unsigned = {
            "schema_version": SCHEMA,
            "project_id": project_id,
            "policy_id": POLICY_ID,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash,
            "previous_policy_hash": previous["policy_hash"],
            "active_pointer_value": {
                "project_id": project_id,
                "policy_id": POLICY_ID,
                "policy_hash": policy_hash,
            },
            "activation_time": policy["activation_time"],
            "compatibility": COMPATIBILITY,
            "rollback_target": previous,
            "key_id": KEY_ID,
            "operation_id": operation_id,
        }
        activation = self._signed(activation_unsigned)
        pointer = {
            "schema_version": SCHEMA,
            "project_id": project_id,
            "policy_id": POLICY_ID,
            "policy_hash": policy_hash,
            "activation_hash": canonical_hash(activation),
        }
        try:
            generation = self.store.activate(project_id, policy, activation)
        except PolicyStateError as exc:
            raise OwnerControllerError(str(exc)) from exc
        return {**pointer, "activation_id": operation_id, "generation": generation}

    def verify(self, project_id: str):
        self._validate_project(project_id)
        return PolicyAuthority().resolve(project_id)

    def refresh(self, project_id: str, operation_id: str) -> dict[str, Any]:
        """Renew only the unchanged, owner-approved AGF-0003 activation."""
        self._validate_project(project_id)
        if not OPERATION_RE.fullmatch(operation_id):
            raise OwnerControllerError("invalid refresh operation identity")
        constitution = ConstitutionAuthority().resolve(project_id)
        try:
            snapshot = PolicyStateStore(self.state_dir, read_only=True).snapshot(project_id)
        except PolicyStateError as exc:
            raise OwnerControllerError("transactional policy state is unreadable") from exc
        if snapshot is None or snapshot.get("active_policy_hash") is None:
            raise OwnerControllerError("no active policy to refresh")
        policy = snapshot.get("policy")
        active_hash = snapshot.get("active_policy_hash")
        if (
            snapshot.get("active_policy_id") != POLICY_ID
            or not isinstance(policy, dict)
            or canonical_hash(policy) != active_hash
        ):
            raise OwnerControllerError("active policy is not the authorized AGF-0003 artifact")
        current_activation = snapshot.get("activation")
        if not isinstance(current_activation, dict):
            raise OwnerControllerError("active activation evidence is missing")
        authority = PolicyAuthority()
        authority._verify_policy(
            project_id, constitution.constitution_id,
            {"schema_version": SCHEMA, "project_id": project_id,
             "policy_id": POLICY_ID, "policy_hash": active_hash,
             "activation_hash": canonical_hash(current_activation)},
            policy,
        )
        pointer = {
            "schema_version": SCHEMA, "project_id": project_id,
            "policy_id": POLICY_ID, "policy_hash": active_hash,
            "activation_hash": canonical_hash(current_activation),
        }
        authority._verify_activation(
            project_id, pointer, policy, current_activation, enforce_freshness=False
        )
        activation_time = datetime.fromisoformat(current_activation["activation_time"])
        freshness = policy["body"]["freshness_limits"]["policy_seconds"]
        if activation_time > datetime.now(UTC) or (
            datetime.now(UTC) - activation_time <= timedelta(seconds=freshness)
        ):
            raise OwnerControllerError("active policy is not eligible for refresh")
        activation_unsigned = {
            "schema_version": SCHEMA,
            "project_id": project_id,
            "policy_id": POLICY_ID,
            "policy_version": policy["version"],
            "policy_hash": active_hash,
            "previous_policy_hash": policy["previous_policy"]["policy_hash"],
            "active_pointer_value": {
                "project_id": project_id, "policy_id": POLICY_ID,
                "policy_hash": active_hash,
            },
            "activation_time": self._now(),
            "compatibility": COMPATIBILITY,
            "rollback_target": policy["previous_policy"],
            "key_id": KEY_ID,
            "operation_id": operation_id,
        }
        activation = self._signed(activation_unsigned)
        try:
            generation = self.store.refresh(
                project_id, policy, activation,
                expected_generation=int(snapshot["generation"]),
                expected_active_policy_hash=active_hash,
            )
        except PolicyStateError as exc:
            raise OwnerControllerError(str(exc)) from exc
        return {
            "project_id": project_id, "policy_id": POLICY_ID,
            "policy_hash": active_hash, "activation_id": operation_id,
            "activation_hash": canonical_hash(activation), "generation": generation,
        }

    def bootstrap_authority(self, project_id: str) -> dict[str, Any]:
        """Pin the initial inactive kill-switch generation after policy activation."""
        self._validate_project(project_id)
        ConstitutionAuthority().resolve(project_id)
        snapshot = PolicyStateStore(self.state_dir, read_only=True).snapshot(project_id)
        if snapshot is None or snapshot.get("active_policy_hash") is None:
            raise OwnerControllerError("active policy is required before authority bootstrap")
        generation = int(snapshot["generation"])
        self.store.bootstrap_authority(project_id, generation=generation)
        return {"project_id": project_id, "generation": generation, "kill_switch_active": False}

    def set_kill_switch(
        self, project_id: str, operation_id: str, *, active: bool, reason: str
    ) -> dict[str, Any]:
        """Owner-only atomic activation or clearing of the emergency stop."""
        self._validate_project(project_id)
        if not OPERATION_RE.fullmatch(operation_id):
            raise OwnerControllerError("invalid kill-switch operation identity")
        ConstitutionAuthority().resolve(project_id)
        try:
            unsigned = {
                "project_id": project_id, "operation_id": operation_id,
                "active": active, "reason": reason, "key_id": KEY_ID,
            }
            signed = self._signed(unsigned)
            generation = self.store.set_kill_switch(
                project_id, operation_id=operation_id, active=active,
                reason=reason, authorization=signed,
            )
        except PolicyStateError as exc:
            raise OwnerControllerError(str(exc)) from exc
        return {
            "project_id": project_id, "generation": generation,
            "kill_switch_active": active,
        }

    def rollback(self, project_id: str, operation_id: str) -> dict[str, Any]:
        self._validate_project(project_id)
        if not OPERATION_RE.fullmatch(operation_id):
            raise OwnerControllerError("invalid rollback operation identity")
        try:
            snapshot = PolicyStateStore(self.state_dir, read_only=True).snapshot(project_id)
        except PolicyStateError as exc:
            raise OwnerControllerError(str(exc)) from exc
        if snapshot is None or snapshot.get("active_policy_hash") is None:
            raise OwnerControllerError("no active policy to rollback")
        policy = snapshot["policy"]
        receipt = {
            "schema_version": SCHEMA,
            "project_id": project_id,
            "operation_id": operation_id,
            "superseded_policy_hash": snapshot["active_policy_hash"],
            "restored_policy_hash": policy["previous_policy"]["policy_hash"],
            "rollback_target": policy["previous_policy"],
            "rollback_time": self._now(),
            "key_id": KEY_ID,
        }
        receipt["tombstone_hash"] = canonical_hash(receipt)
        signed = self._signed(receipt)
        try:
            generation = self.store.rollback(
                project_id, signed,
                expected_generation=int(snapshot["generation"]),
                expected_active_policy_hash=snapshot["active_policy_hash"],
            )
        except PolicyStateError as exc:
            raise OwnerControllerError(str(exc)) from exc
        return {"project_id": project_id, "rollback_receipt_hash": canonical_hash(signed),
                "generation": generation}

    def _previous_identity(self, project_id: str) -> dict[str, str]:
        return {"policy_id": ROLLBACK_TARGET,
                "policy_hash": hashlib.sha256(canonical_json(
                    {"project_id": project_id, "require_human_merge": True}
                )).hexdigest()}

    def _signed(self, unsigned: dict[str, Any]) -> dict[str, Any]:
        return {**unsigned, "signature": hmac.new(
            self._owner_key(), canonical_json(unsigned), hashlib.sha256
        ).hexdigest()}

    def _owner_key(self) -> bytes:
        path = self.authority_dir / "owner.key"
        if path.is_symlink() or self.authority_dir.is_symlink():
            raise OwnerControllerError("owner key symlink is not trusted")
        if self.authority_dir.stat().st_mode & 0o077 or path.stat().st_mode & 0o077:
            raise OwnerControllerError("owner key permissions are broad")
        key = base64.b64decode(path.read_text(encoding="ascii"), validate=True)
        if len(key) < 32:
            raise OwnerControllerError("owner key is too short")
        return key

    @staticmethod
    def _validate_project(project_id: str) -> None:
        if not isinstance(project_id, str) or not PROJECT_RE.fullmatch(project_id):
            raise OwnerControllerError("invalid project identity")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="explicit owner policy controller")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare", "verify", "bootstrap-authority"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
    for name in ("activate", "refresh", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        command.add_argument("--operation-id", required=True)
    switch = commands.add_parser("set-kill-switch")
    switch.add_argument("--project", required=True)
    switch.add_argument("--operation-id", required=True)
    switch.add_argument("--reason", required=True)
    switch.add_argument("--active", action="store_true")
    args = parser.parse_args(argv)
    controller = OwnerPolicyController()
    if args.command in {"activate", "refresh", "rollback"}:
        result = getattr(controller, args.command)(args.project, args.operation_id)
    elif args.command == "set-kill-switch":
        result = controller.set_kill_switch(
            args.project, args.operation_id, active=args.active, reason=args.reason
        )
    else:
        method = "bootstrap_authority" if args.command == "bootstrap-authority" else args.command
        result = getattr(controller, method)(args.project)
    if hasattr(result, "__dict__"):
        result = result.__dict__
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
