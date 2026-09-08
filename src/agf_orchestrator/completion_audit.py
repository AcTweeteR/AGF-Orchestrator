"""Read-only audit of completion prerequisites; never an approval source."""

from __future__ import annotations

import hashlib
import json

from .models import plan_from_dict
from .project_registry import ProjectRegistry, ProjectRegistryError
from .session_store import SessionStore, SessionStoreError
from .task_dependencies import DependencyEvidenceError, verify_integrated_plan


def audit_session_completion(session_id: str, *, state_dir=None) -> dict:
    """Inspect canonical work evidence, keeping absent Objective authority explicit.

    No caller report, roadmap status, APPROVED JSON field or provider statement
    can establish acceptance. An authenticated Objective contract and criterion
    mapping are not installed yet, so this audit cannot return success.
    """
    store = SessionStore(state_dir)
    session = store.load(session_id)
    result = {
        "schema_version": "1.0", "session_id": session.session_id,
        "project_id": session.project_id, "status": "BLOCKED",
        "authority_effect": "NONE", "objective_completed": False,
        "baseline_sha": session.base_sha, "plan_sha256": None,
        "integration": {"status": "UNKNOWN", "evidence": []},
        "objective_approval": "UNKNOWN", "criterion_acceptance": "UNKNOWN",
        "blockers": [],
    }
    try:
        if session.plan_path is None:
            raise ValueError("missing plan")
        path = store.ensure_safe_path(session.plan_path)
        if path.parent != store.ensure_safe_path(store.artifacts_dir / session.session_id):
            raise ValueError("plan outside session")
        data = path.read_bytes()
        plan_hash = hashlib.sha256(data).hexdigest()
        if plan_hash != session.artifact_hashes.get("plan"):
            raise ValueError("plan hash mismatch")
        plan = plan_from_dict(json.loads(data))
        plan.validate()
        result["plan_sha256"] = plan_hash
        project = ProjectRegistry(store.state_dir).get(session.project_id)
        proofs = verify_integrated_plan(
            session.session_id, plan, project.repository_root, state_dir=store.state_dir,
        )
        result["integration"] = {"status": "VERIFIED", "evidence": proofs}
        result["status"] = "HUMAN_REQUIRED"
    except (OSError, ValueError, TypeError, KeyError, SessionStoreError,
            ProjectRegistryError, DependencyEvidenceError):
        result["integration"]["status"] = "UNVERIFIED"
        result["blockers"].append("current plan integration evidence is missing or inconsistent")
    result["blockers"].extend([
        "authenticated Objective approval contract is not installed",
        "requirement and criterion acceptance are not established",
    ])
    return result
