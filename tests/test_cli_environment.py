import os
from types import SimpleNamespace

from agf_orchestrator import cli


def test_exact_agf_dotenv_loads(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    agf_root.mkdir()
    (agf_root / ".env").write_text("AGF_TEST_DOTENV=from-agf\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert os.environ["AGF_TEST_DOTENV"] == "from-agf"


def test_explicit_agf_env_file_overrides_default(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    agf_root.mkdir()
    (agf_root / ".env").write_text("AGF_TEST_DOTENV=from-default\n")
    explicit = tmp_path / "explicit.env"
    explicit.write_text("AGF_TEST_DOTENV=from-explicit\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.setenv("AGF_ENV_FILE", str(explicit))
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)
    monkeypatch.setattr(cli, "ProjectRegistry", lambda: SimpleNamespace(list=lambda: []))

    cli.load_cli_environment()

    assert os.environ["AGF_TEST_DOTENV"] == "from-explicit"


def test_existing_environment_wins_over_dotenv(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    agf_root.mkdir()
    (agf_root / ".env").write_text("AGF_TEST_DOTENV=from-dotenv\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.setenv("AGF_TEST_DOTENV", "from-environment")

    cli.load_cli_environment()

    assert os.environ["AGF_TEST_DOTENV"] == "from-environment"


def test_target_repository_dotenv_is_ignored(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    target = tmp_path / "target"
    agf_root.mkdir()
    target.mkdir()
    (target / ".env").write_text("AGF_TEST_DOTENV=from-target\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.chdir(target)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_parent_directory_dotenv_is_ignored(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    child = tmp_path / "child"
    agf_root.mkdir()
    child.mkdir()
    (tmp_path / ".env").write_text("AGF_TEST_DOTENV=from-parent\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.chdir(child)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_symlink_escape_is_rejected(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    outside = tmp_path / "outside.env"
    link = agf_root / ".env"
    agf_root.mkdir()
    outside.write_text("AGF_TEST_DOTENV=escaped\n")
    link.symlink_to(outside)
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_managed_project_path_is_rejected(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    managed = tmp_path / "managed"
    explicit = managed / ".env"
    agf_root.mkdir()
    managed.mkdir()
    explicit.write_text("AGF_TEST_DOTENV=managed\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.setenv("AGF_ENV_FILE", str(explicit))
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)
    monkeypatch.setattr(
        cli,
        "ProjectRegistry",
        lambda: SimpleNamespace(
            list=lambda: [SimpleNamespace(repository_root=str(managed))]
        ),
    )

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_missing_dotenv_is_allowed(tmp_path, monkeypatch):
    agf_root = tmp_path / "AGF"
    agf_root.mkdir()
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_DOTENV", raising=False)

    cli.load_cli_environment()

    assert "AGF_TEST_DOTENV" not in os.environ


def test_dotenv_secret_is_not_logged_or_reported(tmp_path, monkeypatch, capsys):
    agf_root = tmp_path / "AGF"
    agf_root.mkdir()
    secret = "do-not-print-this-secret"
    (agf_root / ".env").write_text(f"AGF_TEST_SECRET={secret}\n")
    monkeypatch.setattr(cli, "AGF_PACKAGE_ROOT", agf_root)
    monkeypatch.delenv("AGF_ENV_FILE", raising=False)
    monkeypatch.delenv("AGF_TEST_SECRET", raising=False)

    cli.load_cli_environment()

    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
