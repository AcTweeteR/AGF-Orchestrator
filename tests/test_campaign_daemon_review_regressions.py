from types import SimpleNamespace

import pytest

from agf_orchestrator.campaign_daemon import (
    CampaignDaemon,
    CanonicalBindingError,
)
from agf_orchestrator.campaign_runner import make_initial_state

TARGET = "a" * 40


def _state(*, policy_binding=None, authority_generation=None):
    return make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET,
        lineage_binding="ai-fund:main:" + TARGET, retry_budget=3,
        policy_binding=policy_binding, authority_generation=authority_generation,
    )


def _patch_binding(monkeypatch, tmp_path, session):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "projects.json").write_text("{}")
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.ProjectRegistry.verify_read_only",
        lambda _registry, _project: SimpleNamespace(
            status=SimpleNamespace(value="ACTIVE"), name="ai-fund",
            repository_root=str(tmp_path), default_branch="main", current_head_sha=TARGET,
        ),
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon._git",
        lambda _root, *args: "main" if args[:2] == ("branch", "--show-current") else TARGET,
    )
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.SessionStore.load",
        lambda _store, _session_id: session,
    )
    return state_dir


def test_normal_session_without_reconciliation_target_is_valid(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"), artifact_hashes={},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    CampaignDaemon(state_dir)._validate_canonical_binding(_state(), state_dir)


def test_legacy_authority_path_without_generation_is_valid(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"), artifact_hashes={},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.AuthorityContext.resolve_runtime",
        lambda _project_id, _root: None,
    )
    CampaignDaemon(state_dir)._validate_canonical_binding(_state(), state_dir)


def test_stale_reconciliation_target_is_rejected(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"),
        artifact_hashes={"canonical_target": "b" * 40},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    with pytest.raises(CanonicalBindingError, match="target evidence"):
        CampaignDaemon(state_dir)._validate_canonical_binding(_state(), state_dir)
