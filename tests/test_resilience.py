import json
from dataclasses import replace

import pytest

import agf_orchestrator.resilience as resilience
from agf_orchestrator.project_models import Project, ProjectPolicy, ProjectStatus
from agf_orchestrator.resilience import (
    DiagnosticStatus,
    ResilienceError,
    bind_workspace,
    build_evidence_archive,
    derive_scorecard,
    doctor,
)
from agf_orchestrator.session_models import Session, SessionEvent, SessionStatus
from agf_orchestrator.session_store import SessionStore


def project(tmp_path):
    return Project("project-1", "alpha", str(tmp_path / "repo"),
                   "https://github.com/example/alpha.git", "main", "a" * 40,
                   "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z",
                   ProjectStatus.ACTIVE, ProjectPolicy())


def session():
    return Session("session-1", "project-1", "goal", "t", "t", "a" * 40,
                   "READY", SessionStatus.READY)


def test_workspace_binding_rejects_wrong_project(tmp_path):
    p = project(tmp_path)
    with pytest.raises(ResilienceError, match="does not match"):
        bind_workspace(p, repository_root=str(tmp_path / "other"),
                       origin_url=p.origin_url, target_sha=p.current_head_sha)


def test_doctor_unknown_without_workspace_and_archive_is_deterministic(tmp_path):
    p = project(tmp_path)
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "plan.json", json.dumps({"ok": True}) + "\n")
    s.artifact_hashes["plan"] = store.artifact_hash(
        str(store.artifacts_dir / s.session_id / "plan.json")
    )
    findings = doctor(s, store)
    assert findings[0].status is DiagnosticStatus.UNKNOWN
    archive = build_evidence_archive(s, store)
    assert archive == build_evidence_archive(s, store)
    assert archive["scorecard"]["terminal"] is False
    assert p.project_id == s.project_id


def test_archive_rejects_secret_shaped_evidence(tmp_path):
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "secret.json", '{"api_key": "do-not-store"}\n')
    with pytest.raises(ResilienceError, match="secret-shaped"):
        build_evidence_archive(s, store)


def test_scorecard_is_evidence_derived(tmp_path):
    scorecard = derive_scorecard(session())
    assert scorecard.event_count == 0
    assert scorecard.artifact_count == 0
    assert scorecard.evidence_count == 0
    assert scorecard.terminal is False


def valid_session_with_lineage():
    s = session()
    s.events.append(SessionEvent(
        "event-1", "operation-1", "2026-08-24T00:00:00Z", s.session_id,
        "PLANNING_TO_READY", "PLANNING", "READY", "session started", [], [], "DIRECTOR",
    ))
    return s


def test_lineage_requires_real_valid_transition_and_rejects_tamper(tmp_path):
    s = valid_session_with_lineage()
    store = SessionStore(tmp_path / "state")
    assert doctor(s, store)[-1].status is DiagnosticStatus.PASS
    s.events[0] = replace(s.events[0], session_id="session-foreign")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL


def test_lineage_accepts_persisted_ready_observation_events(tmp_path):
    s = valid_session_with_lineage()
    s.events.append(SessionEvent(
        "event-2", "external-advance:external-advance-1", "2026-08-24T00:01:00Z", s.session_id,
        "READY_TO_READY", "READY", "READY", "external target reconciled",
        [f"/state/external-advancements/{s.project_id}/external-advance-1.json"], [],
        "RELEASE_MANAGER",
    ))
    store = SessionStore(tmp_path / "state")

    assert doctor(s, store)[-1].status is DiagnosticStatus.PASS


def test_lineage_accepts_authenticated_blocked_external_reconciliation(tmp_path):
    s = valid_session_with_lineage()
    s.events.append(SessionEvent(
        "event-2", "external-advance-1", "2026-08-24T00:01:00Z", s.session_id,
        "READY_TO_BLOCKED", "READY", "BLOCKED", "blocked pending external merge",
        [], [], "DIRECTOR",
    ))
    s.events.append(SessionEvent(
        "event-3", "external-advance:external-advance-1",
        "2026-08-24T00:02:00Z", s.session_id, "BLOCKED_TO_READY", "BLOCKED", "READY",
        "external target reconciled",
        [f"/state/external-advancements/{s.project_id}/external-advance-1.json"], [],
        "RELEASE_MANAGER",
    ))
    store = SessionStore(tmp_path / "state")

    assert doctor(s, store)[-1].status is DiagnosticStatus.PASS


def test_lineage_accepts_distinct_assessment_events_under_session_operation(tmp_path):
    s = valid_session_with_lineage()
    for index in range(2):
        s.events.append(SessionEvent(
            f"event-assessment-{index}", f"assessment:{s.session_id}",
            f"2026-08-24T00:0{index + 1}:00Z", s.session_id,
            "READY_TO_READY", "READY", "READY", "assessment persisted",
            [f"/state/artifacts/{s.session_id}/assessment-v{index + 1}.json"],
            [], "DIRECTOR",
        ))
    store = SessionStore(tmp_path / "state")

    assert doctor(s, store)[-1].status is DiagnosticStatus.PASS


def test_lineage_rejects_replayed_assessment_evidence(tmp_path):
    s = valid_session_with_lineage()
    for index in range(2):
        s.events.append(SessionEvent(
            f"event-assessment-{index}", f"assessment:{s.session_id}",
            f"2026-08-24T00:0{index + 1}:00Z", s.session_id,
            "READY_TO_READY", "READY", "READY", "assessment persisted",
            [f"/state/artifacts/{s.session_id}/assessment-v1.json"], [], "DIRECTOR",
        ))
    store = SessionStore(tmp_path / "state")

    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL


def test_doctor_resolves_owner_external_advance_hash_from_canonical_store(
    tmp_path, monkeypatch,
):
    import agf_orchestrator.external_advancement as external

    p = project(tmp_path)
    store = SessionStore(tmp_path / "state")
    s = session()
    digest = "a" * 64
    s.artifact_hashes["external_advancement"] = digest
    directory = tmp_path / "state" / "external-advancements" / p.project_id
    directory.mkdir(parents=True)
    (directory / "advance-one.json").write_text("{}\n")
    item = type("Advance", (), {"evidence_hash": digest, "session_id": s.session_id})()

    class Store:
        root = tmp_path / "state" / "external-advancements"

        def __init__(self, _):
            pass

        def get(self, *_):
            return item

    monkeypatch.setattr(external, "ExternalAdvancementStore", Store)
    monkeypatch.setattr(external, "verify_external_advancement", lambda *_args, **_kwargs: None)

    finding = next(x for x in doctor(s, store, project=p)
                   if x.check == "artifact:external_advancement")

    assert finding.status is DiagnosticStatus.PASS


def test_doctor_resolves_objective_hash_from_signed_generation_artifact(
    tmp_path, monkeypatch,
):
    import agf_orchestrator.authority_context as authority_context
    import agf_orchestrator.authority_generation as authority_generation

    p = project(tmp_path)
    store = SessionStore(tmp_path / "state")
    s = session()
    digest = "b" * 64
    s.artifact_hashes["objective_binding"] = digest
    directory = tmp_path / "state" / "authority-generations" / p.project_id
    directory.mkdir(parents=True)
    (directory / "generation-1.json").write_text("{}\n")
    component = type("Component", (), {"name": "objective_acceptance",
                                        "artifact_hash": digest})()
    generation = type("Generation", (), {
        "status": authority_generation.GenerationStatus.SUPERSEDED,
        "components": [component],
    })()

    class Store:
        root = tmp_path / "state"

        def __init__(self, _):
            pass

        def _directory(self, _):
            return directory

        def load(self, *_):
            return generation

    monkeypatch.setattr(authority_generation, "AuthorityGenerationStore", Store)
    monkeypatch.setattr(
        authority_context.AuthorityContext, "_verify_artifacts",
        staticmethod(lambda *_args, **_kwargs: {
            "objective_acceptance": {"session_id": s.session_id},
        }),
    )

    finding = next(x for x in doctor(s, store, project=p)
                   if x.check == "artifact:objective_binding")

    assert finding.status is DiagnosticStatus.PASS


def test_lineage_rejects_reordering_and_replay(tmp_path):
    s = valid_session_with_lineage()
    s.events.append(replace(
        s.events[0], event_id="event-2", operation_id="operation-2",
        timestamp="2026-08-24T00:01:00Z", from_status="READY",
        to_status="EXECUTING", event_type="READY_TO_EXECUTING",
    ))
    store = SessionStore(tmp_path / "state")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL
    s.events[1] = replace(s.events[1], operation_id="operation-1")
    assert doctor(s, store)[-1].status is DiagnosticStatus.FAIL


def test_archive_rejects_traversal_and_symlink_escape(tmp_path):
    store = SessionStore(tmp_path / "state")
    bad = Session(
        "../foreign", "project-1", "goal", "t", "t", "a" * 40,
        "READY", SessionStatus.READY,
    )
    with pytest.raises(ResilienceError):
        build_evidence_archive(bad, store)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.artifacts_dir.mkdir(parents=True)
    (store.artifacts_dir / "session-1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResilienceError):
        build_evidence_archive(session(), store)
    (store.artifacts_dir / "session-1").unlink()
    directory = store.artifacts_dir / "session-1"
    directory.mkdir()
    (outside / "foreign.json").write_text("{}\n")
    (directory / "foreign.json").symlink_to(outside / "foreign.json")
    with pytest.raises(ResilienceError):
        build_evidence_archive(session(), store)


def test_archive_checks_size_before_read_and_bounds_aggregate(tmp_path, monkeypatch):
    monkeypatch.setattr(resilience, "_MAX_ARCHIVE_BYTES", 6)
    store = SessionStore(tmp_path / "state")
    s = session()
    store.write_artifact(s.session_id, "large.json", "1234567")
    with pytest.raises(ResilienceError, match="byte limit"):
        build_evidence_archive(s, store)
    (store.artifacts_dir / s.session_id / "large.json").unlink()
    store.write_artifact(s.session_id, "first.json", "1234")
    store.write_artifact(s.session_id, "second.json", "5678")
    with pytest.raises(ResilienceError, match="byte limit"):
        build_evidence_archive(s, store)
