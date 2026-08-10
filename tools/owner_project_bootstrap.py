"""Explicit owner-operated project bootstrap; never imported by AGF runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agf_orchestrator.constitution import ConstitutionAuthority, canonical_json
from agf_orchestrator.policy_authority import PolicyAuthority
from agf_orchestrator.project_models import ProjectPolicy
from agf_orchestrator.project_registry import (
    ProjectRegistry,
    ProjectRegistryError,
    parse_remote_url,
)
from tools.owner_policy_controller import OwnerControllerError, OwnerPolicyController


class OwnerBootstrapError(RuntimeError):
    """Raised when explicit project onboarding cannot be completed safely."""


class OwnerProjectBootstrapper:
    """Owner-only project registration and authority bootstrap coordinator."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = (state_dir or Path.home() / ".agf-orchestrator").resolve()
        self.registry = ProjectRegistry(self.state_dir)
        self.policy = OwnerPolicyController(self.state_dir)
        self.authority_dir = self.state_dir / "constitution-authority"

    def inspect(self, repository: str | Path) -> dict[str, Any]:
        root = self._repository_root(repository)
        project = self._find_project(root)
        if project is None:
            return {"repository": str(root), "registered": False, "project_id": None}
        constitution = self._constitution_status(project.project_id)
        policy = self.policy.inspect(project.project_id)
        return {
            "repository": str(root), "registered": True,
            "project_id": project.project_id, "name": project.name,
            "status": project.status.value, "constitution": constitution,
            "policy": policy,
        }

    def bootstrap(self, repository: str | Path, *, name: str | None = None) -> dict[str, Any]:
        root = self._repository_root(repository)
        project = self._find_project(root)
        created_registration = False
        if project is None:
            if not name:
                raise OwnerBootstrapError("project name is required for unregistered repository")
            project = self.registry.add(
                name, root,
                policy=ProjectPolicy(
                    allowed_remote_hosts=[parse_remote_url(self._origin(root)).host],
                    allow_live_execution=True, allow_delivery=True, require_human_merge=True,
                ),
            )
            created_registration = True
        else:
            if Path(project.repository_root).resolve() != root:
                raise OwnerBootstrapError("registered project repository identity conflicts")
            project = self.registry.verify(project.project_id)
        try:
            if project.status.value != "ACTIVE":
                raise OwnerBootstrapError(f"project registration is {project.status.value}")
            self._bootstrap_constitution(project.project_id)
            self._verify_constitution(project.project_id)
            policy = self.policy.inspect(project.project_id)
            if not policy["active_present"]:
                self.policy.prepare(project.project_id)
                operation = (
                    "operation-bootstrap-policy-"
                    f"{project.project_id.removeprefix('project-')}"
                )
                self.policy.activate(project.project_id, operation)
            self._verify_policy(project.project_id)
            snapshot = self.policy.store.authority_snapshot(project.project_id)
            if snapshot is None:
                self.policy.bootstrap_authority(project.project_id)
            elif int(snapshot["generation"]) < 1 or int(snapshot["kill_switch_active"]):
                raise OwnerBootstrapError("existing authority state is not a safe baseline")
            return self.verify(project.project_id)
        except Exception:
            if created_registration:
                self._cleanup_new_project(project.project_id)
            raise

    def verify(self, project_id: str) -> dict[str, Any]:
        project = self.registry.verify(project_id)
        if project.status.value != "ACTIVE":
            raise OwnerBootstrapError(f"project registration is {project.status.value}")
        constitution = self._verify_constitution(project_id)
        active = self._verify_policy(project_id)
        authority = self.policy.store.authority_snapshot(project_id)
        if authority is None or int(authority["generation"]) < 1:
            raise OwnerBootstrapError("rollback/authority baseline is missing")
        if int(authority["kill_switch_active"]):
            raise OwnerBootstrapError("kill switch is active")
        return {
            "project_id": project_id, "repository": project.repository_root,
            "origin": project.origin_url, "registration": "ACTIVE",
            "constitution": constitution.evidence(),
            "policy": {
                "policy_id": active.policy_id, "policy_hash": active.policy_hash,
                "activation_hash": active.activation_hash, "generation": authority["generation"],
                "rollback_target": active.rollback_target,
            },
            "rollback_baseline": "VERIFIED",
        }

    def _bootstrap_constitution(self, project_id: str) -> None:
        existing = self._constitution_status(project_id)
        if existing["status"] == "VERIFIED":
            return
        source_id = "project-efc8e8ef7be7050b"
        try:
            source = ConstitutionAuthority().resolve(source_id)
        except Exception as exc:
            raise OwnerBootstrapError("approved Constitution source is unavailable") from exc
        unsigned = {
            "schema_version": ConstitutionAuthority.schema_version,
            "constitution_id": source.constitution_id,
            "version": source.version,
            "project_id": project_id,
            "compatibility": source.compatibility,
            "approval_status": source.approval_status,
            "body": _plain(source.body),
            "key_id": source.key_id,
        }
        record = {**unsigned, "signature": hmac.new(
            self._owner_key(), canonical_json(unsigned), hashlib.sha256
        ).hexdigest()}
        record_hash = hashlib.sha256(canonical_json(record)).hexdigest()
        pointer = {
            "schema_version": ConstitutionAuthority.schema_version,
            "project_id": project_id,
            "constitution_id": source.constitution_id,
            "record_hash": record_hash,
        }
        directory = self.state_dir / "projects" / project_id / "constitution"
        if directory.is_symlink() or directory.parent.is_symlink():
            raise OwnerBootstrapError("project Constitution directory symlink is not trusted")
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        os.chmod(directory, 0o700)
        _atomic_json(directory / f"{source.constitution_id}.json", record)
        _atomic_json(directory / "active.json", pointer)
        os.chmod(directory / f"{source.constitution_id}.json", 0o600)
        os.chmod(directory / "active.json", 0o600)

    def _cleanup_new_project(self, project_id: str) -> None:
        """Compensate a failed new-project bootstrap without touching the repository."""
        if self.policy.store.path.exists():
            with self.policy.store.transaction() as connection:
                for table in (
                    "operation_journal", "rollback_records", "activations", "active_state",
                    "authority_state", "policies",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
        constitution_root = self.state_dir / "projects" / project_id
        if constitution_root.exists() and not constitution_root.is_symlink():
            shutil.rmtree(constitution_root)
        self.registry.remove(project_id)

    def _constitution_status(self, project_id: str) -> dict[str, Any]:
        try:
            active = ConstitutionAuthority().resolve(project_id)
        except Exception as exc:
            return {"status": "UNVERIFIED", "reason": str(exc).split(": ", 1)[-1]}
        return {"status": "VERIFIED", "constitution_id": active.constitution_id,
                "record_hash": active.record_hash}

    def _verify_constitution(self, project_id: str):
        try:
            return ConstitutionAuthority().resolve(project_id)
        except Exception as exc:
            raise OwnerBootstrapError("target ConstitutionAuthority is not VERIFIED") from exc

    def _verify_policy(self, project_id: str):
        try:
            return PolicyAuthority().resolve(project_id)
        except Exception as exc:
            raise OwnerBootstrapError("target Merge Policy is not VERIFIED") from exc

    def _owner_key(self) -> bytes:
        path = self.authority_dir / "owner.key"
        if path.is_symlink() or self.authority_dir.is_symlink():
            raise OwnerBootstrapError("owner key symlink is not trusted")
        try:
            if self.authority_dir.stat().st_mode & 0o077 or path.stat().st_mode & 0o077:
                raise OwnerBootstrapError("owner key permissions are broad")
            key = base64.b64decode(path.read_text(encoding="ascii"), validate=True)
        except (OSError, UnicodeError, ValueError) as exc:
            raise OwnerBootstrapError("owner key is unreadable") from exc
        if len(key) < 32:
            raise OwnerBootstrapError("owner key is too short")
        return key

    def _find_project(self, root: Path):
        matches = [p for p in self.registry.list() if Path(p.repository_root).resolve() == root]
        if len(matches) > 1:
            raise OwnerBootstrapError("repository has conflicting registrations")
        return matches[0] if matches else None

    @staticmethod
    def _repository_root(repository: str | Path) -> Path:
        root = Path(repository).expanduser()
        if root.is_symlink():
            raise OwnerBootstrapError("repository path symlink is not trusted")
        root = root.resolve(strict=True)
        if not (root / ".git").exists():
            raise OwnerBootstrapError("repository is not a Git root")
        return root

    @staticmethod
    def _origin(root: Path) -> str:
        import subprocess
        try:
            return subprocess.run(
                ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OwnerBootstrapError("repository origin is unavailable") from exc


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(canonical_json(payload))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="explicit owner project bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "bootstrap", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--repository", required=name != "verify")
        command.add_argument("--project", required=name == "verify")
        if name == "bootstrap":
            command.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        controller = OwnerProjectBootstrapper()
        result = (
            controller.inspect(args.repository) if args.command == "inspect"
            else controller.bootstrap(
                args.repository, name=args.name
            ) if args.command == "bootstrap"
            else controller.verify(args.project)
        )
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    except (
        OwnerBootstrapError, OwnerControllerError, ProjectRegistryError, OSError, ValueError
    ) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
