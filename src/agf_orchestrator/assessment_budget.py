"""Persist assessment consumption for a session bound to an existing campaign."""

import json
from contextlib import contextmanager

from .campaign_runner import CampaignStore
from .objective_acceptance import content_hash
from .session_store import SessionStoreError


@contextmanager
def assessment_invocation(session, store, request_hash):
    """Called with SessionManager's session and project locks already held.

    The existing campaign retry budget bounds fresh assessments, including direct
    calls. It is not a price estimate, provider eligibility grant or authority.
    """
    binding_hash = session.artifact_hashes.get("campaign_binding")
    path = store.ensure_safe_path(
        store.artifacts_dir / session.session_id / "campaign-binding.json",
    )
    if binding_hash is None:
        if path.exists():
            raise SessionStoreError("campaign budget binding hash is missing")
        yield
        return
    if store.artifact_hash(str(path)) != binding_hash:
        raise SessionStoreError("campaign budget binding changed")
    binding = json.loads(path.read_text())
    if (binding.get("project_id") != session.project_id
            or binding.get("session_id") != session.session_id):
        raise SessionStoreError("assessment campaign session binding differs")
    campaign_store = CampaignStore(store.state_dir, session.project_id, binding["campaign_id"])
    store.ensure_safe_path(campaign_store.path)
    campaign = campaign_store._load_unlocked()
    if (campaign is None or campaign.session_id != session.session_id
            or campaign.project_id != session.project_id
            or campaign.operation_id != binding["operation_id"]
            or campaign.retry_budget != binding["retry_budget"]):
        raise SessionStoreError("assessment campaign budget binding differs")
    identity = {"campaign_id": campaign.campaign_id, "session_id": session.session_id,
                "project_id": session.project_id, "operation_id": campaign.operation_id,
                "retry_budget": campaign.retry_budget}
    directory = store.ensure_safe_path(store.artifacts_dir / session.session_id)
    for attempt in range(campaign.retry_budget + 1):
        prefix = f"assessment-invocation-{campaign.operation_id}-{attempt}"
        started = store.ensure_safe_path(directory / f"{prefix}-started.json")
        finished = store.ensure_safe_path(directory / f"{prefix}-finished.json")
        if started.exists():
            prior = json.loads(started.read_text())
            if any(prior.get(key) != value for key, value in identity.items()):
                raise SessionStoreError("assessment invocation identity differs")
            if not finished.is_file():
                raise SessionStoreError("interrupted assessment requires reconciliation")
            outcome = json.loads(finished.read_text())
            if (outcome.get("started_sha256") != content_hash(prior)
                    or outcome.get("outcome") not in {"RETURNED", "RAISED"}):
                raise SessionStoreError("assessment invocation outcome is inconsistent")
            continue
        if finished.exists():
            raise SessionStoreError("assessment outcome has no invocation")
        payload = {**identity, "request_sha256": request_hash, "base_sha": session.base_sha,
                   "plan_sha256": session.artifact_hashes["plan"], "attempt": attempt}
        store.write_artifact(session.session_id, started.name, json.dumps(payload, sort_keys=True))
        outcome = "RETURNED"
        try:
            yield
        except Exception:
            outcome = "RAISED"
            raise
        finally:
            # BaseException (including process interruption) leaves a durable
            # unknown outcome, instead of authorizing another invocation.
            import sys
            if sys.exc_info()[0] is None or outcome == "RAISED":
                store.write_artifact(session.session_id, finished.name, json.dumps({
                    "started_sha256": content_hash(payload), "outcome": outcome,
                }, sort_keys=True))
        return
    raise SessionStoreError("persistent assessment retry budget exhausted")
