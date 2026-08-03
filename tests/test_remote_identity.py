import socket

import pytest

from agf_orchestrator.remote_identity import RemoteIdentityError, canonical_remote_identity


def test_https_spellings_are_equivalent():
    assert canonical_remote_identity("https://github.com/AcTweeteR/AI-Skills-Compilation.git") == (
        "github.com/AcTweeteR/AI-Skills-Compilation"
    )
    assert canonical_remote_identity("https://github.com/AcTweeteR/AI-Skills-Compilation") == (
        "github.com/AcTweeteR/AI-Skills-Compilation"
    )


def test_https_ssh_and_hostname_case_are_equivalent():
    expected = "github.com/AcTweeteR/AI-Skills-Compilation"
    assert canonical_remote_identity(
        "git@github.com:AcTweeteR/AI-Skills-Compilation.git"
    ) == expected
    assert canonical_remote_identity(
        "ssh://git@github.com/AcTweeteR/AI-Skills-Compilation"
    ) == expected
    assert canonical_remote_identity(
        "HTTPS://GITHUB.COM/AcTweeteR/AI-Skills-Compilation"
    ) == expected


def test_different_repository_and_host_do_not_match():
    base = canonical_remote_identity("https://github.com/example/project.git")
    assert base != canonical_remote_identity("https://github.com/example/other.git")
    assert base != canonical_remote_identity("https://gitlab.com/example/project.git")


def test_ports_are_part_of_identity():
    assert canonical_remote_identity("https://github.com:443/example/project") != (
        canonical_remote_identity("https://github.com/example/project")
    )
    assert canonical_remote_identity("ssh://git@github.com:22/example/project") != (
        canonical_remote_identity("ssh://git@github.com/example/project")
    )


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:password@github.com/example/project.git",
        "https://token@github.com/example/project.git",
        "https://github.com/example/project.git?token=secret",
        "https://github.com/example/project.git#fragment",
        "git@github.com:project.git",
        "git@github.com:../example/project.git",
        "custom://github.com/example/project",
    ],
)
def test_unsafe_or_malformed_remotes_are_rejected(remote):
    with pytest.raises(RemoteIdentityError):
        canonical_remote_identity(remote)


def test_canonicalization_never_contacts_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail)
    assert canonical_remote_identity("git@github.com:example/project.git") == (
        "github.com/example/project"
    )
