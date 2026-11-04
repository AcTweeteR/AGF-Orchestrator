"""Owner-signed AFTER-THE-FACT GitHub merge records cannot authorize delivery.

Disposable Git history; no GitHub network or actual Owner key needed.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator import observed_merge
from agf_orchestrator.observed_merge import (
    ObservedMergeError,
    ObservedMergeStore,
    verify_observed_merge_chain,
)
from agf_orchestrator.owner_authority import canonical_bytes
from tools import owner_ed25519_authority as controller

PROJECT = "project-0123456789abcdef"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, text=True,
    ).stdout.strip()


@pytest.fixture
def witnessed(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    git(root, "config", "user.name", "AGF QA")
    git(root, "config", "user.email", "agf@example.invalid")
    (root / "README.md").write_text("baseline\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    start = git(root, "rev-parse", "HEAD")
    records = {}
    previous = start
    for index in (218, 219, 228):
        (root / "README.md").write_text(f"merged PR {index}\n")
        git(root, "add", "README.md")
        git(root, "commit", "-m", f"Merge PR {index}")
        sha = git(root, "rev-parse", "HEAD")
        records[index] = (previous, sha)
        previous = sha
    project = SimpleNamespace(
        project_id=PROJECT, origin_url="https://github.com/AcTweeteR/AGF-Orchestrator.git",
        repository_root=str(root), default_branch="main",
        status=SimpleNamespace(value="ACTIVE"), current_head_sha=previous,
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key()
    def sign(payload, *_args):
        return {
            "signature": base64.b64encode(key.sign(canonical_bytes(payload))).decode()
        }
    def verify(payload, envelope):
        public.verify(base64.b64decode(envelope["signature"]), canonical_bytes(payload))

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(observed_merge, "verify_envelope", verify)
    monkeypatch.setattr(controller, "ProjectRegistry", lambda *_: SimpleNamespace(
        verify_read_only=lambda *_: project,
    ))
    monkeypatch.setattr(controller, "_generation_root", lambda: tmp_path)
    monkeypatch.setattr(controller, "sign_envelope", sign)
    real_run = subprocess.run
    def run(command, *args, **kwargs):
        if command[0:3] == ["gh", "pr", "view"]:
            number = int(command[3])
            if number not in records:
                raise subprocess.CalledProcessError(1, command)
            response = {
                "number": number, "state": "MERGED",
                "mergeCommit": {"oid": records[number][1]},
                "baseRefName": "main", "mergedBy": {"login": "AcTweeteR"},
                "mergedAt": "2026-11-04T19:00:00Z",
            }
            return SimpleNamespace(stdout=json.dumps(response), returncode=0)
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(controller.subprocess, "run", run)
    return root, project, records, run


def test_observed_main_chain_is_exact_and_cryptographically_signed(witnessed):
    _, project, commits, _ = witnessed
    store = ObservedMergeStore(Path.home() / ".agf-orchestrator")
    for number in (218, 219, 228):
        result = controller.acknowledge_observed_merge(PROJECT, number)
        assert result["status"] == "RECORDED_PAST_MERGE_NOT_AUTHORIZED"
        assert result["target_sha"] == commits[number][1]
        assert controller.acknowledge_observed_merge(PROJECT, number)["status"] == (
            "ALREADY_RECORDED"
        )
    assert verify_observed_merge_chain(store, project, commits[218][0], commits[228][1])
    assert len(store.all(PROJECT)) == 3
    for item in store.all(PROJECT):
        item.verify_project(project)
        assert item.owner_payload["decision"] == "ACKNOWLEDGE_ALREADY_MERGED"
        assert item.provenance == "OWNER_POSTHOC_GITHUB_MERGE"
        assert "AUTHORIZE_EXTERNAL_ADVANCE" not in json.dumps(item.__dict__)


def test_missing_middle_or_wrong_target_fails_closed(witnessed):
    _, project, commits, _ = witnessed
    store = ObservedMergeStore(Path.home() / ".agf-orchestrator")
    controller.acknowledge_observed_merge(PROJECT, 218)
    controller.acknowledge_observed_merge(PROJECT, 228)
    assert not verify_observed_merge_chain(store, project, commits[218][0], commits[228][1])
    assert not verify_observed_merge_chain(store, project, commits[218][0], "0" * 40)
    controller.acknowledge_observed_merge(PROJECT, 219)
    assert verify_observed_merge_chain(store, project, commits[218][0], commits[228][1])


def test_tampered_signed_record_is_not_usable(witnessed):
    _, project, commits, _ = witnessed
    store = ObservedMergeStore(Path.home() / ".agf-orchestrator")
    result = controller.acknowledge_observed_merge(PROJECT, 218)
    path = store.root / PROJECT / (result["evidence_id"] + ".json")
    document = json.loads(path.read_text())
    document["owner_payload"]["decision"] = "AUTHORIZE_EXTERNAL_ADVANCE"
    path.write_text(json.dumps(document))
    with pytest.raises(ObservedMergeError):
        store.get(PROJECT, result["evidence_id"])
    with pytest.raises(ObservedMergeError):
        verify_observed_merge_chain(store, project, commits[218][0], commits[228][1])


@pytest.mark.parametrize("field,value", [
    ("state", "OPEN"),
    ("baseRefName", "develop"),
    ("mergedBy", {"login": "attacker"}),
    ("mergeCommit", {"oid": "f" * 40}),
])
def test_unverified_github_merge_never_gets_signed(witnessed, field, value, monkeypatch):
    _, project, _, run = witnessed
    real_run = controller.subprocess.run
    def invalid(command, *args, **kwargs):
        if command[0:3] == ["gh", "pr", "view"]:
            data = json.loads(run(command, *args, **kwargs).stdout)
            data[field] = value
            return SimpleNamespace(stdout=json.dumps(data), returncode=0)
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(controller.subprocess, "run", invalid)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        controller.acknowledge_observed_merge(PROJECT, 218)
    assert ObservedMergeStore(Path.home() / ".agf-orchestrator").all(PROJECT) == ()


def test_record_cannot_substitute_project_or_first_parent(witnessed):
    root, project, commits, _ = witnessed
    store = ObservedMergeStore(Path.home() / ".agf-orchestrator")
    controller.acknowledge_observed_merge(PROJECT, 218)
    item = store.all(PROJECT)[0]
    with pytest.raises(ObservedMergeError, match="project"):
        item.verify_project(SimpleNamespace(
            **{**project.__dict__, "project_id": "project-ffffffffffffffff"},
        ))
    with pytest.raises(ObservedMergeError, match="first parent"):
        # Check against an unrelated (but existing) SHA while retaining valid signature:
        with patch.object(observed_merge, "_git", side_effect=[
            "a" * 40, item.target_sha,
        ]):
            item.verify_project(project)


def test_owner_controller_reuses_chain_for_provider_rebind(witnessed, monkeypatch):
    _, project, commits, _ = witnessed
    for number in (218, 219, 228):
        controller.acknowledge_observed_merge(PROJECT, number)
    old = SimpleNamespace(target_sha=commits[218][0])
    new = SimpleNamespace(target_sha=commits[228][1])
    monkeypatch.setattr(controller, "ExternalAdvancementStore", lambda *_:
        SimpleNamespace(root=Path.home() / "no-authorized-events"))
    with patch.object(
        controller, "DeliveryIntentStore",
        side_effect=AssertionError("complete owner-observed chain must be sufficient"),
    ):
        controller._verify_legitimate_target_advancement(
            PROJECT, project, old, new, new.target_sha,
        )


@pytest.mark.parametrize("url", [
    "https://github.com.evil.org/AcTweeteR/AGF-Orchestrator.git",
    "https://github.com@evil.org/AcTweeteR/AGF-Orchestrator.git",
    "https://evil.org/github.com/AcTweeteR/AGF-Orchestrator.git",
    "http://github.com/AcTweeteR/AGF-Orchestrator.git",
    "https://github.com:444/AcTweeteR/AGF-Orchestrator.git",
    "https://github.com/AcTweeteR/%2fsecret.git",
    "https://github.com/AcTweeteR/AGF-Orchestrator.git?token=bad",
    "https://github.com/AcTweeteR/../other.git",
])
def test_observed_url_rejects_host_and_path_confusion(url):
    with pytest.raises(ObservedMergeError):
        observed_merge.github_repository_coordinates(url)


def test_observed_url_accepts_only_explicit_exact_https_host():
    assert observed_merge.github_repository_coordinates(
        "https://github.com/AcTweeteR/AGF-Orchestrator.git"
    ) == ("AcTweeteR", "AGF-Orchestrator")
