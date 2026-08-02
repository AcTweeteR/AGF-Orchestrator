import json

from test_project_registry import repo

from agf_orchestrator import cli


def test_project_commands_use_explicit_registry(tmp_path, monkeypatch, capsys):
    root, _ = repo(tmp_path)
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["project", "add", "--name", "alpha", "--repository", str(root)]) == 0
    capsys.readouterr()
    assert cli.main(["project", "list", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output[0]["name"] == "alpha"
    assert cli.main(["project", "verify", "--project", "alpha", "--json"]) == 0


def test_project_remove_does_not_touch_repository(tmp_path, monkeypatch):
    root, _ = repo(tmp_path)
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["project", "add", "--name", "alpha", "--repository", str(root)]) == 0
    assert cli.main(["project", "remove", "--project", "alpha"]) == 0
    assert (root / "file.txt").read_text() == "before"
