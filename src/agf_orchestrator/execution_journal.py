"""Durable dispatch observation before registered live execution begins."""

import json
import subprocess
from uuid import uuid4

from .locking import session_lock
from .objective_acceptance import content_hash
from .session_store import SessionStore, SessionStoreError


class ExecutionRecoveryRequired(ValueError):
    """Prior dispatch must be reconciled before another invocation is allowed."""


def _worktrees(plan):
    return content_hash(subprocess.check_output(
        ["git", "-C", plan.repository.root, "worktree", "list", "--porcelain"],
    ).decode())


def require_reconciled_execution(store, session, plan, *, allow_completed=False):
    """Reject unresolved dispatch from every entry point, not only continuation."""
    directory = store.ensure_safe_path(store.artifacts_dir / session.session_id)
    failed_invocations = 0
    for candidate in sorted(directory.glob("execution-started-*.json")):
        path = store.ensure_safe_path(candidate)
        started = json.loads(path.read_text())
        if (started.get("session_id") != session.session_id
                or started.get("project_id") != session.project_id
                or path.name != f"execution-started-{content_hash(started)}.json"):
            raise SessionStoreError("execution journal binding is inconsistent")
        if _integrated_binding(store, session, plan, started, allow_completed=allow_completed):
            continue
        result_path = store.ensure_safe_path(
            directory / f"execution-finished-{content_hash(started)}.json",
        )
        if not result_path.is_file():
            raise ExecutionRecoveryRequired("prior execution has no verified outcome")
        result = json.loads(result_path.read_text())
        report = result.get("report", {})
        if (result.get("started_sha256") != content_hash(started)
                or started.get("plan_sha256") != session.artifact_hashes.get("plan")
                or started.get("base_sha") != session.base_sha
                or started.get("worktrees_sha256") != _worktrees(plan)
                or report.get("execution_status") != "FAILED"
                or report.get("review_status") != "NOT_RUN"
                or report.get("push_status") != "NOT_REQUESTED"
                or report.get("commit_sha") is not None
                or report.get("status") not in {"BLOCKED", "FAILED"}
                or report.get("task_id") != started["task_id"]
                or report.get("plan_id") != plan.plan_id
                or type(report.get("correction_rounds")) is not int
                or report["correction_rounds"] != 0):
            raise ExecutionRecoveryRequired("prior execution requires canonical reconciliation")
        failed_invocations += 1
    # Historical coordinator dispatch journals predate the shared runtime
    # journal. Every entry point must preserve their uncertain outcome; neither
    # a missing runtime start nor a caller switching APIs resolves a dispatch.
    for candidate in sorted(directory.glob("continuation-*-started.json")):
        path = store.ensure_safe_path(candidate)
        payload = json.loads(path.read_text())
        binding = payload["binding"]
        prefix = f"continuation-{content_hash(binding)}-attempt-"
        attempt = path.name.removeprefix(prefix).removesuffix("-started.json")
        if (not path.name.startswith(prefix) or not attempt.isdigit()
                or binding.get("session_id") != session.session_id
                or binding.get("project_id") != session.project_id):
            raise SessionStoreError("continuation journal binding is inconsistent")
        if _integrated_binding(store, session, plan, binding, allow_completed=allow_completed):
            continue
        raise ExecutionRecoveryRequired("continuation dispatch requires reconciliation")
    return failed_invocations


def _integrated_binding(store, session, plan, binding, *, allow_completed):
    from .delivery_reconciliation import DeliveryIntentStore
    from .task_dependencies import MissingIntegrationEvidence, _hash, verify_integrated_task

    try:
        verify_integrated_task(session.session_id, plan, binding["task_id"],
                               plan.repository.root, state_dir=store.state_dir,
                               allow_completed=allow_completed)
    except MissingIntegrationEvidence:
        return False
    # An older receipt for the same task cannot resolve a later dispatch.
    cursor = store.ensure_safe_path(session.plan_path)
    for _ in range(200):
        payload = json.loads(cursor.read_text())
        if store.artifact_hash(str(cursor)) == binding.get("plan_sha256"):
            return any(
                item.task_id == binding["task_id"] and item.base_sha == binding.get("base_sha")
                and item.plan_hash == _hash(payload)
                for item in DeliveryIntentStore(store.state_dir).for_session(
                    session.project_id, session.session_id,
                )
            )
        previous = payload.get("scope", {}).get("lineage")
        if not previous:
            break
        cursor = store.ensure_safe_path(previous)
    return False


def record_execution_start(session_id, plan, task_id):
    if not session_id:
        return
    store = SessionStore()
    with session_lock(store.state_dir, session_id, "execution-journal"):
        return _record(store, session_id, plan, task_id)


def _record(store, session_id, plan, task_id):
    session = store.load(session_id)
    path = store.ensure_safe_path(session.plan_path)
    if store.artifact_hash(str(path)) != session.artifact_hashes.get("plan"):
        raise SessionStoreError("execution plan hash differs from session")
    if content_hash(json.loads(path.read_text())) != content_hash(plan.to_dict()):
        raise SessionStoreError("execution plan differs from canonical session plan")
    failures = require_reconciled_execution(store, session, plan)
    from .project_registry import ProjectRegistry

    project = ProjectRegistry(store.state_dir).get(session.project_id)
    if failures >= project.policy.maximum_correction_rounds + 1:
        raise ExecutionRecoveryRequired("registered execution retry budget exhausted")
    payload = {"schema_version": "1.0", "session_id": session_id,
               "project_id": session.project_id, "plan_sha256": session.artifact_hashes["plan"],
               "task_id": task_id, "base_sha": session.base_sha,
               "invocation_id": uuid4().hex, "worktrees_sha256": _worktrees(plan),
               "remaining_attempts": project.policy.maximum_correction_rounds + 1 - failures}
    store.write_artifact(session_id, f"execution-started-{content_hash(payload)}.json",
                         json.dumps(payload, sort_keys=True) + "\n")
    return payload


def record_execution_result(started, report):
    """Persist a runtime delivery outcome paired with this exact invocation."""
    if started is None:
        return
    store = SessionStore()
    session_id = started["session_id"]
    with session_lock(store.state_dir, session_id, "execution-result"):
        digest = content_hash(started)
        path = store.ensure_safe_path(store.artifacts_dir / session_id
                                      / f"execution-started-{digest}.json")
        if json.loads(path.read_text()) != started:
            raise SessionStoreError("execution start changed before recording its result")
        store.write_artifact(session_id, f"execution-finished-{digest}.json",
                             json.dumps({"started_sha256": digest, "report": report},
                                        sort_keys=True) + "\n")
