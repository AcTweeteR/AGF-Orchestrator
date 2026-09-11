import json
from contextlib import contextmanager

import pytest
from test_session_manager import registered

from agf_orchestrator.assessment_budget import assessment_invocation
from agf_orchestrator.campaign_runner import (
    CampaignStore,
    PersistentCampaignRunner,
    make_initial_state,
)
from agf_orchestrator.locking import project_lock, session_lock
from agf_orchestrator.objective_acceptance import content_hash
from agf_orchestrator.session_manager import SessionManager
from agf_orchestrator.session_store import SessionStore, SessionStoreError


def bound_session(tmp_path, budget=1):
    _, state_dir = registered(tmp_path)
    manager = SessionManager(state_dir)
    session = manager.start("alpha", "Add contributor link validation")
    campaign = make_initial_state(
        project_id=session.project_id, campaign_id="campaign-budget-test",
        session_id=session.session_id, phase="ASSESSMENT", operation_id="operation-budget-test",
        target_sha=session.base_sha, lineage_binding=session.artifact_hashes["plan"],
        retry_budget=budget,
    )
    campaigns = CampaignStore(state_dir, session.project_id, campaign.campaign_id)
    campaigns.create(campaign)
    binding = {"campaign_id": campaign.campaign_id, "session_id": session.session_id,
               "project_id": session.project_id, "operation_id": campaign.operation_id,
               "retry_budget": campaign.retry_budget}
    _, digest = manager.store.write_artifact(
        session.session_id, "campaign-binding.json", json.dumps(binding, sort_keys=True),
    )
    session.artifact_hashes["campaign_binding"] = digest
    manager.store.save(session)
    return manager.store, session, campaigns


@contextmanager
def invocation(store, session, request="a" * 64):
    with session_lock(store.state_dir, session.session_id, "test-assessment"):
        with project_lock(store.state_dir, session.project_id, "test-assessment"):
            with assessment_invocation(session, store, request):
                yield


def test_success_and_error_consume_durable_budget_before_provider_call(tmp_path):
    store, session, _ = bound_session(tmp_path)
    directory = store.artifacts_dir / session.session_id
    with invocation(store, session):
        started = list(directory.glob("assessment-invocation-*-started.json"))
        assert len(started) == 1
        assert not list(directory.glob("assessment-invocation-*-finished.json"))
        payload = json.loads(started[0].read_text())
        assert payload["request_sha256"] == "a" * 64
        assert payload["plan_sha256"] == session.artifact_hashes["plan"]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        with invocation(store, session, "b" * 64):
            raise RuntimeError("provider unavailable")
    outcomes = []
    for path in sorted(directory.glob("assessment-invocation-*-finished.json")):
        result = json.loads(path.read_text())
        source = json.loads(path.with_name(path.name.replace("finished", "started")).read_text())
        assert result["started_sha256"] == content_hash(source)
        outcomes.append(result["outcome"])
    assert outcomes == ["RETURNED", "RAISED"]
    restarted = SessionStore(store.state_dir)
    with pytest.raises(SessionStoreError, match="budget exhausted"):
        with invocation(restarted, restarted.load(session.session_id), "c" * 64):
            pytest.fail("restarting the store must not reset consumption")


def test_interrupted_assessment_remains_unknown_after_restart(tmp_path):
    class ProcessInterrupted(BaseException):
        pass

    store, session, _ = bound_session(tmp_path)
    with pytest.raises(ProcessInterrupted):
        with invocation(store, session):
            raise ProcessInterrupted()
    directory = store.artifacts_dir / session.session_id
    assert len(list(directory.glob("assessment-invocation-*-started.json"))) == 1
    assert not list(directory.glob("assessment-invocation-*-finished.json"))
    restarted = SessionStore(store.state_dir)
    with pytest.raises(SessionStoreError, match="interrupted assessment"):
        with invocation(restarted, restarted.load(session.session_id)):
            pytest.fail("an uncertain prior invocation must not invoke the provider again")
    assert len(list(directory.glob("assessment-invocation-*-started.json"))) == 1


@pytest.mark.parametrize("damage", ["missing_hash", "missing_file", "corrupt_file"])
def test_budget_binding_damage_fails_before_invocation(tmp_path, damage):
    store, session, _ = bound_session(tmp_path)
    path = store.artifacts_dir / session.session_id / "campaign-binding.json"
    if damage == "missing_hash":
        session.artifact_hashes.pop("campaign_binding")
        store.save(session)
    elif damage == "missing_file":
        path.unlink()
    else:
        path.write_text("{}")
    with pytest.raises((SessionStoreError, OSError)):
        with invocation(store, store.load(session.session_id)):
            pytest.fail("damaged campaign binding must not bypass its budget")
    assert not list(path.parent.glob("assessment-invocation-*-started.json"))


@pytest.mark.parametrize("field,value", [
    ("session_id", "session-other"), ("project_id", "project-other"),
    ("operation_id", "operation-other"), ("retry_budget", 2),
])
def test_rehashed_binding_must_match_persisted_campaign(tmp_path, field, value):
    store, session, _ = bound_session(tmp_path)
    path = store.artifacts_dir / session.session_id / "campaign-binding.json"
    binding = json.loads(path.read_text())
    binding[field] = value
    path.write_text(json.dumps(binding, sort_keys=True))
    session.artifact_hashes["campaign_binding"] = store.artifact_hash(str(path))
    store.save(session)
    with pytest.raises(SessionStoreError):
        with invocation(store, session):
            pytest.fail("a changed binding must not authorize provider consumption")


def test_campaign_retry_reset_does_not_reset_assessment_consumption(tmp_path):
    store, session, campaigns = bound_session(tmp_path)
    for _ in range(2):
        with invocation(store, session):
            pass
    runner = PersistentCampaignRunner(campaigns)
    assert runner.schedule_retry("temporary external outage").retry_count == 1
    reset = runner.reset_retry("external outage repaired")
    assert reset.retry_count == 0
    restarted = SessionStore(store.state_dir)
    with pytest.raises(SessionStoreError, match="budget exhausted"):
        with invocation(restarted, restarted.load(session.session_id)):
            pytest.fail("external polling retry reset cannot reset provider consumption")
