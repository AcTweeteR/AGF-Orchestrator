"""Read-only dependency admission from canonical reconciled delivery evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .delivery_reconciliation import DeliveryIntentStore, DeliveryReceipt
from .models import ExecutionPlan, Task, plan_from_dict
from .project_registry import ProjectRegistry, ProjectRegistryError
from .session_models import SessionStatus
from .session_store import SessionStore, SessionStoreError


class DependencyEvidenceError(ValueError):
    pass


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _git(repository: str, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", repository, *args], check=True, capture_output=True,
    ).stdout


def verify_task_dependencies(
    session_id: str | None, plan: ExecutionPlan, task: Task, repository: str,
) -> list[str]:
    """Never accept caller completion flags or imported receipts as authority.

    Historical delivery proves only predecessor availability. Present execution
    policy, risk and all other gates remain the responsibility of the caller.
    """
    if not session_id:
        raise DependencyEvidenceError("dependencies require a persisted session")
    try:
        return _verify(session_id, plan, task, repository)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            SessionStoreError, ProjectRegistryError) as exc:
        raise DependencyEvidenceError("dependency evidence is missing or inconsistent") from exc


def _verify(session_id: str, plan: ExecutionPlan, task: Task, repository: str) -> list[str]:
    sessions = SessionStore()
    session = sessions.load(session_id)
    project = ProjectRegistry(sessions.state_dir).get(session.project_id)
    if (session.status not in {SessionStatus.READY, SessionStatus.EXECUTING}
            or session.blocking_issues or session.required_human_actions
            or Path(project.repository_root).resolve() != Path(repository).resolve()
            or session.base_sha != plan.repository.head_sha):
        raise DependencyEvidenceError("dependency session is not executable at this target")
    if _git(repository, "rev-parse", "HEAD").decode().strip() != session.base_sha:
        raise DependencyEvidenceError("dependency baseline changed")
    if _git(repository, "status", "--porcelain").strip():
        raise DependencyEvidenceError("dependency target is dirty")
    origin = _git(repository, "config", "--get", "remote.origin.url").decode().strip()
    branch = _git(repository, "branch", "--show-current").decode().strip()
    artifacts = sessions.ensure_safe_path(sessions.artifacts_dir / session_id)

    def read_plan(raw_path, expected_hash):
        path = sessions.ensure_safe_path(raw_path)
        if not path.is_relative_to(artifacts):
            raise DependencyEvidenceError("dependency plan belongs to another session")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise DependencyEvidenceError("dependency plan hash mismatch")
        payload = json.loads(data)
        plan_from_dict(payload).validate()
        return path, payload

    path, current = read_plan(session.plan_path, session.artifact_hashes["plan"])
    if _hash(current) != _hash(plan.to_dict()):
        raise DependencyEvidenceError("dependency caller plan is not the current session plan")
    required_ids = set(task.dependencies) | {
        edge["depends_on"] for edge in plan.dependencies if edge["task_id"] == task.task_id
    }
    required = {item.task_id: item for item in plan.tasks if item.task_id in required_ids}
    if set(required) != required_ids:
        raise DependencyEvidenceError("unknown dependency")
    intents = DeliveryIntentStore(sessions.state_dir)
    proofs = {}
    seen = set()
    for _ in range(200):
        if path in seen:
            raise DependencyEvidenceError("cyclic plan lineage")
        seen.add(path)
        scope = current["scope"]
        predecessor = scope.get("lineage")
        if not predecessor:
            break
        previous_path, previous = read_plan(predecessor, scope["predecessor_plan_sha256"])
        if ({key: value for key, value in previous["repository"].items() if key != "head_sha"}
                != {key: value for key, value in current["repository"].items()
                    if key != "head_sha"}):
            raise DependencyEvidenceError("dependency repository identity changed in lineage")
        reconciliation = scope.get("delivery_reconciliation")
        if reconciliation is None:
            if previous["repository"] != current["repository"]:
                raise DependencyEvidenceError("unreconciled target advancement")
        else:
            intent = intents.get(session.project_id, reconciliation["delivery_id"])
            if intent is None:
                raise DependencyEvidenceError("dependency delivery intent is missing")
            receipt_path = sessions.ensure_safe_path(
                intents.receipt_path(session.project_id, intent.delivery_id)
            )
            receipt_payload = json.loads(receipt_path.read_text())
            receipt = DeliveryReceipt(**receipt_payload)
            unsigned = {key: value for key, value in receipt_payload.items()
                        if key != "receipt_sha256"}
            if receipt.receipt_sha256 != _hash(unsigned):
                raise DependencyEvidenceError("dependency receipt hash mismatch")
            previous_tasks = {item["task_id"]: item for item in previous["tasks"]}
            if (
                intent.session_id != session_id or intent.repository_identity != origin
                or intent.target_branch != branch or intent.plan_id != previous["plan_id"]
                or intent.plan_hash != _hash(previous)
                or intent.task_id not in previous_tasks
                or intent.task_hash != _hash(previous_tasks[intent.task_id])
                or intent.base_sha != previous["repository"]["head_sha"]
                or intent.candidate_sha != current["repository"]["head_sha"]
                or reconciliation != {
                    "delivery_id": intent.delivery_id, "intent_hash": intent.content_sha256,
                    "receipt_hash": receipt.receipt_sha256,
                    "observed_sha": intent.candidate_sha, "completed_task_id": intent.task_id,
                }
                or receipt.state != "VERIFIED" or receipt.project_id != session.project_id
                or receipt.delivery_id != intent.delivery_id
                or receipt.repository_identity != origin or receipt.base_sha != intent.base_sha
                or receipt.observed_sha != intent.candidate_sha
                or receipt.intent_hash != intent.content_sha256
                or receipt.observed_tree_sha != intent.candidate_tree_sha
                or receipt.diff_sha256 != intent.diff_sha256
            ):
                raise DependencyEvidenceError("dependency delivery bindings differ")
            _git(repository, "merge-base", "--is-ancestor", intent.base_sha, intent.candidate_sha)
            tree = _git(
                repository, "rev-parse", f"{intent.candidate_sha}^{{tree}}"
            ).decode().strip()
            diff = _git(repository, "diff", f"{intent.base_sha}..{intent.candidate_sha}")
            changed = tuple(_git(repository, "diff", "--name-only",
                                 f"{intent.base_sha}..{intent.candidate_sha}").decode().splitlines())
            if (tree != intent.candidate_tree_sha or hashlib.sha256(diff).hexdigest()
                    != intent.diff_sha256 or changed != intent.changed_files):
                raise DependencyEvidenceError("dependency tree or patch differs")
            if intent.task_id in required:
                expected_task = next(item for item in plan.to_dict()["tasks"]
                                     if item["task_id"] == intent.task_id)
                previous_edges = {
                    edge["depends_on"] for edge in previous["dependencies"]
                    if edge["task_id"] == intent.task_id
                }
                current_edges = {
                    edge["depends_on"] for edge in plan.dependencies
                    if edge["task_id"] == intent.task_id
                }
                if intent.task_hash != _hash(expected_task) or previous_edges != current_edges:
                    raise DependencyEvidenceError("dependency definition changed")
                # A historical success cannot excuse a reverted or overwritten result.
                if _git(repository, "diff", "--name-only", intent.candidate_sha,
                        plan.repository.head_sha, "--", *intent.changed_files).strip():
                    raise DependencyEvidenceError("dependency result changed after integration")
                if intent.task_id in proofs:
                    raise DependencyEvidenceError("ambiguous repeated dependency delivery")
                proofs[intent.task_id] = (
                    f"dependency {intent.task_id}: receipt={receipt.receipt_sha256}; "
                    f"intent={intent.content_sha256}; integrated={intent.candidate_sha}"
                )
        path, current = previous_path, previous
    else:
        raise DependencyEvidenceError("dependency lineage exceeds limit")
    if set(proofs) != set(required):
        raise DependencyEvidenceError("dependency has no verified integrated delivery")
    return [proofs[key] for key in sorted(proofs)]
