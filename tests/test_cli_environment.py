import os

from agf_orchestrator import cli


def test_load_cli_environment_reads_existing_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)
    (tmp_path / ".env").write_text("AGF_TEST_DOTENV=from-dotenv\n")

    cli.load_cli_environment()

    assert os.environ["AGF_TEST_DOTENV"] == "from-dotenv"


def test_load_cli_environment_continues_when_dotenv_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_existing_environment_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("AGF_TEST_DOTENV=from-dotenv\n")
    monkeypatch.setenv("AGF_TEST_DOTENV", "from-environment")

    cli.load_cli_environment()

    assert os.environ["AGF_TEST_DOTENV"] == "from-environment"
