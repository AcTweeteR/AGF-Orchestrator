import json

from test_project_registry import repo

from agf_orchestrator import cli


def test_session_start_show_and_inbox_json(tmp_path, monkeypatch, capsys):
    root, _ = repo(tmp_path)
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["project", "add", "--name", "alpha", "--repository", str(root)]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            ["session", "start", "--project", "alpha", "--goal", "Add a contributor link", "--json"]
        )
        == 0
    )
    session = json.loads(capsys.readouterr().out)
    assert session["status"] == "READY"
    assert cli.main(["session", "show", "--session", session["session_id"], "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["session_id"] == session["session_id"]
    assert cli.main(["inbox", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_session_doctor_and_archive_are_observational(tmp_path, monkeypatch, capsys):
    root, _ = repo(tmp_path)
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["project", "add", "--name", "alpha", "--repository", str(root)]) == 0
    capsys.readouterr()
    assert cli.main([
        "session", "start", "--project", "alpha", "--goal", "Bounded goal", "--json"
    ]) == 0
    session_id = json.loads(capsys.readouterr().out)["session_id"]
    assert cli.main([
        "session", "doctor", "--project", "alpha", "--session", session_id, "--json"
    ]) == 0
    findings = json.loads(capsys.readouterr().out)
    assert {item["check"] for item in findings} >= {"workspace-trust", "recovery-lineage"}
    assert cli.main([
        "session", "archive", "--project", "alpha", "--session", session_id, "--json"
    ]) == 0
    archive = json.loads(capsys.readouterr().out)
    assert archive["session_id"] == session_id
    assert cli.main([
        "session", "doctor", "--project", "missing", "--session", session_id, "--json"
    ]) == 2
    capsys.readouterr()
