"""Real ephemeral Owner signatures prove prospective historical epoch rollover.

No test writes into a real user authority or uses production Owner key material.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator import historical_epochs, historical_evidence
from agf_orchestrator.owner_authority import canonical_bytes
from tools import owner_ed25519_authority as controller
from tools import owner_historical_epoch as epoch

PROJECT = "project-0123456789abcdef"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True,
        check=True, text=True,
    ).stdout.strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "config", "user.name", "AGF Test")
    git(repo, "config", "user.email", "tester@example.invalid")
    (repo / "README.md").write_text("Original historical baseline\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "old baseline")
    old_sha = git(repo, "rev-parse", "HEAD")
    origin = "file:///tmp/agf-historical-baseline-epoch-tests.git"
    registry = SimpleNamespace(
        status=SimpleNamespace(value="ACTIVE"), default_branch="main",
        repository_root=str(repo), current_head_sha=old_sha, origin_url=origin,
    )
    authority = SimpleNamespace(
        constitution=SimpleNamespace(
            constitution_id="constitution-v1", record_hash="c" * 64,
        ),
        policy=SimpleNamespace(policy_id="merge-policy-adr-0003", policy_hash="a" * 64),
        snapshot={"generation": 15},
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key()
    fp = hashlib.sha256(public.public_bytes_raw()).hexdigest()

    def sign(payload, _root):
        data = canonical_bytes(payload)
        return {
            "signature": base64.b64encode(key.sign(data)).decode(),
            "payload_hash": hashlib.sha256(data).hexdigest(),
        }

    def verify(payload, envelope):
        data = canonical_bytes(payload)
        if hashlib.sha256(data).hexdigest() != envelope["payload_hash"]:
            raise ValueError("tampered owner payload")
        public.verify(base64.b64decode(envelope["signature"]), data)

    def registry_api(*_args):
        return SimpleNamespace(
            verify_read_only=lambda *_: registry,
            get=lambda *_: registry,
        )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("AGF_STATE_DIR", str(tmp_path / ".agf-orchestrator"))
    monkeypatch.setattr(controller, "ProjectRegistry", registry_api)
    monkeypatch.setattr(epoch, "ProjectRegistry", registry_api)
    monkeypatch.setattr(historical_evidence, "ProjectRegistry", registry_api)
    monkeypatch.setattr(controller, "resolve_authority", lambda *_: authority)
    monkeypatch.setattr(epoch.owner if hasattr(epoch, "owner") else controller,
                        "resolve_authority", lambda *_: authority)
    monkeypatch.setattr(historical_evidence, "resolve_authority", lambda *_: authority)
    monkeypatch.setattr(controller, "_generation_root", lambda: tmp_path / "owner-root")
    monkeypatch.setattr(controller, "sign_envelope", sign)
    monkeypatch.setattr(historical_evidence, "verify_envelope", verify)
    monkeypatch.setattr(historical_epochs, "verify_envelope", verify)
    monkeypatch.setattr(epoch, "verify_envelope", verify)
    monkeypatch.setattr(controller, "verify_envelope", verify)
    monkeypatch.setattr(controller, "PINNED_OWNER_FINGERPRINT", fp)
    monkeypatch.setattr(epoch, "PINNED_OWNER_FINGERPRINT", fp)
    monkeypatch.setattr(
        historical_evidence.owner_authority, "PINNED_OWNER_FINGERPRINT", fp,
    )
    now = datetime.now(UTC).replace(microsecond=0)
    def shift(seconds):
        monkeypatch.setattr(
            controller, "_now",
            lambda: (now + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        )
    shift(-20)
    first = controller.create_prospective_baseline(
        PROJECT, "historical-baseline-first-test", target_sha=old_sha,
    )
    assert first["status"] == "COMMITTED"
    (repo / "README.md").write_text("New current target\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "move forward in main")
    registry.current_head_sha = git(repo, "rev-parse", "HEAD")
    return tmp_path / ".agf-orchestrator", repo, registry, shift


def test_rollover_preserves_signed_predecessor_and_unknown_gap(setup):
    state, _repo, registry, shift = setup
    shift(-10)
    result = epoch.rollover_prospective_baseline(PROJECT, "historical-baseline-epoch-second-test")
    assert result["status"] == "COMMITTED_NEW_PROSPECTIVE_EPOCH"
    assert result["baseline_generation"] == 2
    assert result["unobserved_gap"] == "UNKNOWN"
    assert result["target_sha"] == registry.current_head_sha
    active = historical_evidence.load_historical_baseline(PROJECT, state_root=state)
    assert active is not None
    assert active.baseline_generation == 2
    archive = Path(result["archived_predecessor"])
    previous = json.loads((archive / "baseline.json").read_text())["payload"]
    current = json.loads(
        (state / "historical-evidence" / PROJECT / "baseline.json").read_text()
    )["payload"]
    assert previous["target_sha"] != current["target_sha"]
    assert current["predecessor_baseline_hash"] == historical_epochs.signed_hash(previous)
    assert previous["baseline_generation"] == 1
    assert "unobserved-gap=UNKNOWN" in current["provenance"]
    assert not (state / "historical-evidence" / PROJECT / "rollback.json").exists()

    shift(-4)
    renewed = controller.renew_prospective_evidence(
        PROJECT, "historical-renewal-epoch-second-test",
    )
    assert renewed["status"] == "COMMITTED"
    for kind in ("incident", "rollback"):
        item = historical_evidence.load_historical_evidence(PROJECT, kind, state_root=state)
        assert item is not None
        assert item.status == historical_evidence.EvidenceStatus.VERIFIED_ZERO
        assert item.coverage_before_baseline == "UNKNOWN"
        assert item.coverage_start == current["coverage_start"]


def test_modifying_archived_predecessor_fails_closed(setup):
    state, _repo, _registry, shift = setup
    shift(-10)
    result = epoch.rollover_prospective_baseline(PROJECT, "historical-baseline-epoch-tamper-test")
    archive = Path(result["archived_predecessor"])
    record = archive / "baseline.json"
    content = json.loads(record.read_text())
    content["payload"]["target_sha"] = "e" * 40
    record.write_text(json.dumps(content))
    with pytest.raises(historical_evidence.HistoricalEvidenceError, match="predecessor"):
        historical_evidence.load_historical_baseline(PROJECT, state_root=state)


def test_missing_archived_predecessor_fails_closed(setup):
    state, _repo, _registry, shift = setup
    shift(-10)
    result = epoch.rollover_prospective_baseline(PROJECT, "historical-baseline-epoch-absent-test")
    (Path(result["archived_predecessor"]) / "baseline-activation.json").unlink()
    with pytest.raises(historical_evidence.HistoricalEvidenceError, match="predecessor"):
        historical_evidence.load_historical_baseline(PROJECT, state_root=state)


def test_rollover_refuses_active_campaign_without_mutation(setup):
    state, _repo, registry, shift = setup
    original = (state / "historical-evidence" / PROJECT / "baseline.json").read_bytes()
    folder = state / "campaign-daemon"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(json.dumps({
        "campaigns_active": 1, "campaigns_waiting": 0,
    }))
    shift(-10)
    with pytest.raises(RuntimeError, match="active campaign"):
        epoch.rollover_prospective_baseline(PROJECT, "historical-baseline-epoch-inflight-test")
    assert original == (state / "historical-evidence" / PROJECT / "baseline.json").read_bytes()


def test_failure_before_activation_restores_original_baseline(setup, monkeypatch):
    state, _repo, _registry, shift = setup
    original_path = state / "historical-evidence" / PROJECT / "baseline.json"
    original = original_path.read_bytes()
    shift(-10)
    def fail_archive(**_kwargs):
        raise ValueError("simulated archival verification failure")
    monkeypatch.setattr(epoch, "_assert_archive", fail_archive)
    with pytest.raises(ValueError, match="simulated"):
        epoch.rollover_prospective_baseline(PROJECT, "historical-baseline-epoch-crash-test")
    assert original_path.read_bytes() == original
    assert not (state / "historical-evidence-archives" / PROJECT).joinpath(
        historical_epochs.signed_hash(json.loads(original)["payload"])
    ).exists()


def test_owner_operation_rejects_arbitrary_identity(setup):
    for invalid_project in ("../../.ssh", "project-unsafe", "project-foo/../../"):
        with pytest.raises(RuntimeError, match="identity"):
            epoch.rollover_prospective_baseline(
                invalid_project, "historical-baseline-epoch-invalid-test",
            )
    with pytest.raises(RuntimeError, match="identity"):
        epoch.rollover_prospective_baseline(PROJECT, "bad")



def test_three_signed_epochs_have_complete_predecessor_chain(setup):
    state, repo, registry, shift = setup
    shift(-10)
    first_rollover = epoch.rollover_prospective_baseline(
        PROJECT, "historical-baseline-epoch-second-chain-test",
    )
    assert first_rollover["baseline_generation"] == 2
    (repo / "README.md").write_text("Third canonical main target\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "third historical epoch")
    registry.current_head_sha = git(repo, "rev-parse", "HEAD")
    shift(-5)
    third = epoch.rollover_prospective_baseline(
        PROJECT, "historical-baseline-epoch-third-chain-test",
    )
    assert third["baseline_generation"] == 3
    active = historical_evidence.load_historical_baseline(PROJECT, state_root=state)
    assert active is not None and active.baseline_generation == 3
    assert (Path(first_rollover["archived_predecessor"]) / "baseline.json").is_file()
    assert (Path(third["archived_predecessor"]) / "baseline.json").is_file()


def test_replay_of_fresh_epoch_cannot_reset_history(setup):
    state, repo, registry, shift = setup
    shift(-10)
    epoch.rollover_prospective_baseline(
        PROJECT, "historical-baseline-epoch-no-reset-test",
    )
    active = state / "historical-evidence" / PROJECT / "baseline.json"
    before = active.read_bytes()
    with pytest.raises(RuntimeError, match="still fresh|timestamp did not advance"):
        epoch.rollover_prospective_baseline(
            PROJECT, "historical-baseline-epoch-no-reset-test",
        )
    assert active.read_bytes() == before


def test_project_target_staleness_blocks_epoch_before_write(setup):
    state, repo, registry, shift = setup
    shift(-10)
    registry.current_head_sha = "b" * 40
    original = (state / "historical-evidence" / PROJECT / "baseline.json").read_bytes()
    with pytest.raises(RuntimeError, match="target binding"):
        epoch.rollover_prospective_baseline(
            PROJECT, "historical-baseline-epoch-target-stale-test",
        )
    assert original == (
        state / "historical-evidence" / PROJECT / "baseline.json"
    ).read_bytes()



def test_documented_owner_cli_imports_epoch_controller_when_executed_as_script():
    """The actual owner CLI is invoked as a FILE, not a package module."""
    import sys

    repo = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable, str(repo / "tools" / "owner_ed25519_authority.py"),
            "--project", "invalid-id",
            "--operation-id", "historical-baseline-epoch-import-qa",
            "--rollover-prospective-baseline",
        ],
        cwd=repo, capture_output=True, text=True, timeout=20,
    )
    assert process.returncode != 0
    assert "historical epoch operation identity is invalid" in process.stderr
    assert "ModuleNotFoundError" not in process.stderr



def test_signed_baseline_outlives_snapshot_ttl_without_relaxing_event_freshness(
    setup, monkeypatch,
):
    """Simulate three days later; target and Owner bindings remain unchanged."""
    from datetime import datetime as ActualDatetime

    from agf_orchestrator.delivery import _load_prospective_evidence

    state, _repo, _registry, shift = setup
    shift(-10)
    created = epoch.rollover_prospective_baseline(
        PROJECT, "historical-baseline-epoch-durable-anchor-test",
    )
    shift(-4)
    signed = controller.renew_prospective_evidence(
        PROJECT, "historical-renewal-durable-anchor-test",
    )
    assert signed["status"] == "COMMITTED"
    original = historical_evidence.datetime

    class FutureTime(original):
        @classmethod
        def now(cls, tz=None):
            return ActualDatetime.now(tz) + timedelta(days=3)

    monkeypatch.setattr(historical_evidence, "datetime", FutureTime)
    assert historical_evidence.load_historical_baseline(
        PROJECT, state_root=state, max_age_seconds=86400,
    ) is None
    durable = historical_evidence.load_historical_baseline(
        PROJECT, state_root=state, max_age_seconds=None,
    )
    assert durable is not None
    assert durable.baseline_id == created["baseline_id"]
    assert durable.authoritative
    assert historical_evidence.load_historical_evidence(
        PROJECT, "rollback", state_root=state, max_age_seconds=3600,
    ) is None
    assert historical_evidence.load_historical_evidence(
        PROJECT, "incident", state_root=state, max_age_seconds=3600,
    ) is None
    # Production delivery never uses the old signed zero count as a fresh fact.
    monkeypatch.setattr(
        "agf_orchestrator.delivery.load_historical_baseline",
        lambda project_id, **kwargs:
            historical_evidence.load_historical_baseline(
                project_id, state_root=state, **kwargs
            ),
    )
    monkeypatch.setattr(
        "agf_orchestrator.delivery.load_historical_evidence",
        lambda project_id, kind, **kwargs:
            historical_evidence.load_historical_evidence(
                project_id, kind, state_root=state, **kwargs
            ),
    )
    future_decision = FutureTime.now(UTC).replace(microsecond=0).isoformat()
    assert _load_prospective_evidence(
        PROJECT, "incident", future_decision, 3600
    ) is None


def test_future_dated_signed_anchor_is_never_accepted_even_without_age_limit(
    setup, monkeypatch,
):
    from datetime import datetime as ActualDatetime

    state, _repo, _registry, shift = setup
    shift(-10)
    epoch.rollover_prospective_baseline(
        PROJECT, "historical-baseline-epoch-future-anchor-test",
    )
    original = historical_evidence.datetime

    class PastTime(original):
        @classmethod
        def now(cls, tz=None):
            return ActualDatetime.now(tz) - timedelta(days=1)

    monkeypatch.setattr(historical_evidence, "datetime", PastTime)
    assert historical_evidence.load_historical_baseline(
        PROJECT, state_root=state, max_age_seconds=None
    ) is None
