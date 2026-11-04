"""A signed, bound first plan is executable without a second Architect opinion."""

from unittest.mock import patch

from test_objective_plan import approved_first_plan
from test_session_continuation import FLAGS, ForbiddenPipeline

from agf_orchestrator.session_continuation import SessionContinuation


def test_owner_signed_bound_plan_reaches_delivery_without_architect(tmp_path, monkeypatch):
    root, _, manager, session, _, projected, _ = approved_first_plan(
        tmp_path, monkeypatch, persist_intent=False
    )
    manager.bind_objective_plan(session.session_id)
    driver = SessionContinuation(manager, ForbiddenPipeline())

    def dispatched(bound_session, project, plan, task, binding):
        assert plan.to_dict() == projected.to_dict()
        assert bound_session.session_id == session.session_id
        assert task.task_id == "task-001"
        assert binding["plan_sha256"] == bound_session.artifact_hashes["plan"]
        return driver._result(bound_session, "CONTINUE", "authenticated-delivery",
                              "ready for independently governed delivery")

    with (
        patch("agf_orchestrator.session_continuation.admit_live_delivery"),
        patch.object(driver, "_dispatch", side_effect=dispatched) as work,
        patch.object(driver, "_assess", side_effect=AssertionError("unneeded Architect")),
    ):
        result = driver.tick(session.session_id, **FLAGS)
    assert result["status"] == "CONTINUE", result
    assert result["action"] == "authenticated-delivery"
    work.assert_called_once()
    assert not (root / "calculator.py").read_text().endswith("a + b\n")


def test_changed_signed_binding_cannot_skip_architect(tmp_path, monkeypatch):
    _, _, manager, session, _, _, _ = approved_first_plan(
        tmp_path, monkeypatch, persist_intent=False
    )
    bound = manager.bind_objective_plan(session.session_id)
    bound.artifact_hashes["objective_binding"] = "0" * 64
    manager.store.save(bound)
    driver = SessionContinuation(manager, ForbiddenPipeline())
    with patch.object(driver, "_dispatch", side_effect=AssertionError("unauthorized dispatch")):
        result = driver.tick(session.session_id, **FLAGS)
    assert result["status"] in {"BLOCKED", "HUMAN_REQUIRED"}, result
    assert result["action"] != "delivery"
