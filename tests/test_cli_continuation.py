import json

from test_task_dependencies import prepared

from agf_orchestrator import cli


def test_public_continue_waits_without_loading_stale_architect_config(
    tmp_path, monkeypatch, capsys,
):
    _, _, _, session, _, _ = prepared(tmp_path, monkeypatch, single=True, integrated=False)
    code = cli.main([
        "session", "continue", "--session", session.session_id,
        "--architect-config", str(tmp_path / "missing.json"),
        "--execute", "--confirm-execution", "--confirm-delivery", "--json",
    ])
    assert code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "WAIT"
    assert result["action"] == "external-integration"
    assert not result["objective_completed"]
    assert len(result["steps"]) == 1


def test_public_continue_requires_explicit_execution_confirmation(tmp_path, monkeypatch, capsys):
    _, _, manager, session, _, _ = prepared(tmp_path, monkeypatch, single=True, integrated=False)
    before = manager.get(session.session_id).to_dict()
    assert cli.main(["session", "continue", "--session", session.session_id, "--json"]) == 2
    assert "confirmation" in capsys.readouterr().err
    assert manager.get(session.session_id).to_dict() == before
