"""Durable, fail-closed reconciliation for externally completed deliveries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DeliveryReconciliationError(ValueError):
    """Raised when delivery intent or observed target state is not provable."""


_HEX = re.compile(r"^[0-9a-f]{40,64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _git(repository: str | Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class DeliveryIntent:
    schema_version: str
    delivery_id: str
    project_id: str
    session_id: str
    plan_id: str
    plan_hash: str
    task_id: str
    task_hash: str
    repository_identity: str
    base_sha: str
    candidate_sha: str
    candidate_tree_sha: str
    delivery_branch: str
    target_branch: str
    allowed_paths: tuple[str, ...]
    changed_files: tuple[str, ...]
    diff_sha256: str
    review_sha256: str
    compliance_sha256: str
    authorization_sha256: str
    review_evidence: dict[str, Any]
    compliance_evidence: dict[str, Any]
    authorization_evidence: dict[str, Any]
    policy_hash: str
    constitution_id: str
    authority_generation: int
    evidence_generation: int
    created_at: str
    state: str
    content_sha256: str

    def _payload(self) -> dict[str, Any]:
        payload = self.__dict__.copy()
        payload["allowed_paths"] = list(self.allowed_paths)
        payload["changed_files"] = list(self.changed_files)
        payload.pop("content_sha256")
        return payload

    def validate(self) -> None:
        if self.schema_version != "1.0" or not _ID.fullmatch(self.delivery_id):
            raise DeliveryReconciliationError("delivery intent identity is invalid")
        if not self.project_id.startswith("project-") or not self.session_id.startswith("session-"):
            raise DeliveryReconciliationError("delivery intent project/session binding is invalid")
        if not self.task_id.startswith("task-") or not self.plan_id:
            raise DeliveryReconciliationError("delivery intent plan/task binding is invalid")
        for name in (
            "plan_hash", "task_hash", "base_sha", "candidate_sha", "candidate_tree_sha",
            "diff_sha256",
            "review_sha256", "compliance_sha256", "authorization_sha256", "policy_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise DeliveryReconciliationError(f"delivery intent {name} is invalid")
        if not self.constitution_id or not self.repository_identity:
            raise DeliveryReconciliationError("delivery intent authority binding is incomplete")
        if not self.target_branch or not isinstance(self.target_branch, str):
            raise DeliveryReconciliationError("delivery intent target branch is invalid")
        for payload, expected, label in (
            (self.review_evidence, self.review_sha256, "review"),
            (self.compliance_evidence, self.compliance_sha256, "compliance"),
            (self.authorization_evidence, self.authorization_sha256, "authorization"),
        ):
            if not isinstance(payload, dict) or _sha(payload) != expected:
                raise DeliveryReconciliationError(f"delivery intent {label} evidence is invalid")
        if self.review_evidence.get("status") != "APPROVE":
            raise DeliveryReconciliationError("delivery intent review is not approved")
        if self.compliance_evidence.get("status") != "PASS":
            raise DeliveryReconciliationError("delivery intent Compliance is not passing")
        if self.authorization_evidence.get("authorization_status") not in {
            "AUTHORIZED", "authorized"
        }:
            raise DeliveryReconciliationError("delivery intent authorization is not valid")
        if not isinstance(self.authority_generation, int) or self.authority_generation < 1:
            raise DeliveryReconciliationError("delivery intent authority generation is invalid")
        if not isinstance(self.evidence_generation, int) or self.evidence_generation < 1:
            raise DeliveryReconciliationError("delivery intent evidence generation is invalid")
        if self.state not in {"EXTERNAL_ACTION_REQUIRED", "OBSERVED", "VERIFIED", "COMPLETED"}:
            raise DeliveryReconciliationError("delivery intent state is invalid")
        if not self.allowed_paths or not self.changed_files or not set(self.changed_files).issubset(
            set(self.allowed_paths)
        ):
            raise DeliveryReconciliationError("delivery intent path binding is invalid")
        if self.content_sha256 != _sha(self._payload()):
            raise DeliveryReconciliationError("delivery intent hash is invalid")

    def to_dict(self) -> dict[str, Any]:
        result = self._payload()
        result["content_sha256"] = self.content_sha256
        return result


@dataclass(frozen=True)
class DeliveryReceipt:
    delivery_id: str
    project_id: str
    repository_identity: str
    base_sha: str
    observed_sha: str
    intent_hash: str
    observed_tree_sha: str
    diff_sha256: str
    state: str
    observed_at: str
    receipt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        result = self.__dict__.copy()
        result.pop("receipt_sha256")
        result["receipt_sha256"] = self.receipt_sha256
        return result


class DeliveryIntentStore:
    """Atomic state-root storage for delivery intent and observed receipts."""

    def __init__(self, state_root: str | Path):
        self.root = Path(state_root).expanduser().resolve() / "delivery-intents"

    def _path(self, project_id: str, delivery_id: str) -> Path:
        if not project_id.startswith("project-") or not _ID.fullmatch(delivery_id):
            raise DeliveryReconciliationError("delivery intent path identity is invalid")
        directory = self.root / project_id
        if self.root.is_symlink() or directory.is_symlink():
            raise DeliveryReconciliationError("delivery intent namespace uses symlinks")
        return directory / f"{delivery_id}.json"

    def receipt_path(self, project_id: str, delivery_id: str) -> Path:
        return self._path(project_id, delivery_id).with_name(f"{delivery_id}.receipt.json")

    @contextmanager
    def _lock(self, project_id: str, delivery_id: str):
        lock_path = self._path(project_id, delivery_id).with_name(f".{delivery_id}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def put(self, intent: DeliveryIntent) -> str:
        intent.validate()
        path = self._path(intent.project_id, intent.delivery_id)
        with self._lock(intent.project_id, intent.delivery_id):
            existing = self.get(intent.project_id, intent.delivery_id)
            if existing is not None and existing != intent:
                raise DeliveryReconciliationError("conflicting delivery intent is rejected")
            if existing is None:
                self._atomic_json(path, intent.to_dict())
        return intent.content_sha256

    def get(self, project_id: str, delivery_id: str) -> DeliveryIntent | None:
        path = self._path(project_id, delivery_id)
        if not path.exists():
            return None
        if path.is_symlink():
            raise DeliveryReconciliationError("delivery intent must not be a symlink")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            intent = DeliveryIntent(
                **{
                    **payload,
                    "allowed_paths": tuple(payload["allowed_paths"]),
                    "changed_files": tuple(payload["changed_files"]),
                }
            )
            intent.validate()
            if intent.project_id != project_id or intent.delivery_id != delivery_id:
                raise DeliveryReconciliationError("delivery intent binding differs")
            return intent
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
            raise DeliveryReconciliationError("delivery intent is unreadable") from exc

    def for_session(self, project_id: str, session_id: str) -> list[DeliveryIntent]:
        directory = self.root / project_id
        if self.root.is_symlink():
            raise DeliveryReconciliationError("delivery intent root uses symlinks")
        if not directory.exists():
            return []
        if directory.is_symlink():
            raise DeliveryReconciliationError("delivery intent namespace uses symlinks")
        result = []
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".receipt.json"):
                continue
            if path.is_symlink():
                raise DeliveryReconciliationError("delivery intent must not be a symlink")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                intent = DeliveryIntent(
                    **{
                        **payload,
                        "allowed_paths": tuple(payload["allowed_paths"]),
                        "changed_files": tuple(payload["changed_files"]),
                    }
                )
                intent.validate()
                if intent.project_id != project_id or intent.delivery_id != path.stem:
                    raise DeliveryReconciliationError("delivery intent namespace binding differs")
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
                raise DeliveryReconciliationError("delivery intent is unreadable") from exc
            if intent.session_id == session_id:
                result.append(intent)
        return result

    def observe(self, project_id: str, delivery_id: str, repository: str | Path) -> DeliveryReceipt:
        intent = self.get(project_id, delivery_id)
        if intent is None:
            raise DeliveryReconciliationError("delivery intent is missing")
        if intent.state not in {"EXTERNAL_ACTION_REQUIRED", "OBSERVED", "VERIFIED"}:
            raise DeliveryReconciliationError("delivery intent is not pending reconciliation")
        head = _git(repository, "rev-parse", "HEAD")
        identity = _git(repository, "config", "--get", "remote.origin.url")
        if identity != intent.repository_identity:
            raise DeliveryReconciliationError("observed repository identity differs")
        branch = _git(repository, "branch", "--show-current")
        if branch != intent.target_branch:
            raise DeliveryReconciliationError("observed target branch differs")
        remote = subprocess.run(
            ["git", "-C", str(repository), "ls-remote", "--heads", "origin", branch],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        if len(remote) != 1 or remote[0].split("\t") != [head, f"refs/heads/{branch}"]:
            raise DeliveryReconciliationError("remote target ref does not match observed delivery")
        receipt_path = self.receipt_path(project_id, delivery_id)
        if head == intent.base_sha:
            raise DeliveryReconciliationError("delivery has not occurred")
        if head != intent.candidate_sha:
            raise DeliveryReconciliationError("observed target SHA is not the authorized candidate")
        subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", intent.base_sha, head],
            check=True,
            capture_output=True,
        )
        tree = _git(repository, "rev-parse", f"{head}^{{tree}}")
        changed = tuple(
            _git(repository, "diff", "--name-only", f"{intent.base_sha}..{head}").splitlines()
        )
        diff = subprocess.run(
            ["git", "-C", str(repository), "diff", f"{intent.base_sha}..{head}"],
            check=True, capture_output=True,
        ).stdout
        diff_sha = hashlib.sha256(diff).hexdigest()
        if (
            tree != intent.candidate_tree_sha
            or changed != intent.changed_files
            or diff_sha != intent.diff_sha256
        ):
            raise DeliveryReconciliationError("observed target tree or diff differs")
        if not set(changed).issubset(set(intent.allowed_paths)):
            raise DeliveryReconciliationError("observed target changed paths exceed intent")
        if receipt_path.is_symlink():
            raise DeliveryReconciliationError("delivery receipt must not be a symlink")
        if receipt_path.exists():
            try:
                prior_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                prior = DeliveryReceipt(**prior_payload)
                prior_base = {**prior_payload}
                prior_base.pop("receipt_sha256")
                if prior.receipt_sha256 != _sha(prior_base):
                    raise DeliveryReconciliationError("delivery receipt hash is invalid")
                if (
                    prior.delivery_id != delivery_id
                    or prior.project_id != project_id
                    or prior.repository_identity != identity
                    or prior.base_sha != intent.base_sha
                    or prior.observed_sha != head
                    or prior.intent_hash != intent.content_sha256
                    or prior.observed_tree_sha != tree
                    or prior.diff_sha256 != diff_sha
                    or prior.state != "VERIFIED"
                ):
                    raise DeliveryReconciliationError("conflicting delivery receipt is rejected")
                return prior
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
                raise DeliveryReconciliationError("delivery receipt is unreadable") from exc
        observed_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {
            "delivery_id": delivery_id, "project_id": project_id,
            "repository_identity": identity, "base_sha": intent.base_sha,
            "observed_sha": head, "intent_hash": intent.content_sha256,
            "observed_tree_sha": tree, "diff_sha256": diff_sha,
            "state": "VERIFIED", "observed_at": observed_at,
        }
        receipt = DeliveryReceipt(**payload, receipt_sha256=_sha(payload))
        receipt_payload = {**payload, "receipt_sha256": receipt.receipt_sha256}
        with self._lock(project_id, delivery_id):
            if receipt_path.exists():
                if receipt_path.is_symlink():
                    raise DeliveryReconciliationError("delivery receipt must not be a symlink")
                try:
                    prior_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                    prior = DeliveryReceipt(**prior_payload)
                    prior_base = {**prior_payload}
                    prior_base.pop("receipt_sha256")
                    if prior.receipt_sha256 != _sha(prior_base):
                        raise DeliveryReconciliationError("delivery receipt hash is invalid")
                    prior_compare = prior.to_dict()
                    current_compare = receipt.to_dict()
                    prior_compare.pop("observed_at")
                    prior_compare.pop("receipt_sha256")
                    current_compare.pop("observed_at")
                    current_compare.pop("receipt_sha256")
                    if prior_compare != current_compare:
                        raise DeliveryReconciliationError(
                            "conflicting delivery receipt is rejected"
                        )
                    return prior
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
                    raise DeliveryReconciliationError("delivery receipt is unreadable") from exc
            self._atomic_json(receipt_path, receipt_payload)
        return receipt
