from types import SimpleNamespace

import pytest

from agf_orchestrator.campaign_daemon import (
    CampaignDaemon,
    CampaignDriverSpec,
    CanonicalBindingError,
    RetryableCanonicalBindingError,
)
from agf_orchestrator.campaign_runner import CampaignStore, make_initial_state

TARGET = "a" * 40


def _state(*, lineage_binding="lineage-main", policy_binding=None, authority_generation=None):
    return make_initial_state(
        project_id="project-ai-fund", campaign_id="campaign-ai-fund",
        session_id="session-a610d1e887d0c9ac8d7e", phase="R7",
        operation_id="operation-r7", target_sha=TARGET,
        lineage_binding=lineage_binding, retry_budget=3,
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


def test_installed_authority_requires_campaign_bindings(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"), artifact_hashes={},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.AuthorityContext.resolve_runtime",
        lambda _project_id, _root: SimpleNamespace(policy_hash="p" * 64, generation_number=2),
    )
    with pytest.raises(CanonicalBindingError, match="policy or authority"):
        CampaignDaemon(state_dir)._validate_canonical_binding(_state(), state_dir)


def test_bound_campaign_rejects_removed_authority_selector(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"), artifact_hashes={},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    monkeypatch.setattr(
        "agf_orchestrator.campaign_daemon.AuthorityContext.resolve_runtime",
        lambda _project_id, _root: None,
    )
    with pytest.raises(CanonicalBindingError, match="authority binding"):
        CampaignDaemon(state_dir)._validate_canonical_binding(
            _state(policy_binding="p" * 64, authority_generation=2), state_dir
        )


def test_stale_reconciliation_target_is_rejected(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"),
        artifact_hashes={"canonical_target": "b" * 40},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    with pytest.raises(CanonicalBindingError, match="target evidence"):
        CampaignDaemon(state_dir)._validate_canonical_binding(_state(), state_dir)


def test_canonical_lineage_binding_mismatch_is_rejected(tmp_path, monkeypatch):
    session = SimpleNamespace(
        project_id="project-ai-fund", base_sha=TARGET,
        status=SimpleNamespace(value="READY"), artifact_hashes={},
    )
    state_dir = _patch_binding(monkeypatch, tmp_path, session)
    with pytest.raises(CanonicalBindingError, match="lineage binding"):
        CampaignDaemon(state_dir)._validate_canonical_binding(
            _state(lineage_binding="ai-fund:main:" + "b" * 40), state_dir
        )


def test_transient_authority_evidence_uses_retry_not_terminal_block(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    store = CampaignStore(state_dir, "project-ai-fund", "campaign-ai-fund")
    store.create(_state())
    daemon = CampaignDaemon(state_dir)
    daemon.register(
        CampaignDriverSpec(
            "project-ai-fund", "campaign-ai-fund", str(state_dir),
            ("/bin/true",), ("/bin/true",), 1,
        )
    )
    monkeypatch.setattr(
        daemon, "_validate_canonical_binding",
        lambda _state, _state_dir: (_ for _ in ()).throw(
            RetryableCanonicalBindingError("authority evidence is temporarily unavailable")
        ),
    )
    daemon.run_forever(max_loops=1)
    assert store.load().status.value == "RETRY_BACKOFF"
