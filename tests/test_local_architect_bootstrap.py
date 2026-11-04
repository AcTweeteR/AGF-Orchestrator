"""Local Architect capability bootstrapping never invokes paid Codex."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agf_orchestrator import cli
from agf_orchestrator.adapters.codex import CodexProcessResult
from agf_orchestrator.adapters.local_architect import LOCAL_ARCHITECT_PROVIDER_ID
from agf_orchestrator.provider_intelligence import (
    ProviderIntelligenceStore,
    state_from_dict,
)

PROJECT = "project-efc8e8ef7be7050b"
HEAD = "a" * 40


def test_real_schema_canary_selects_only_local_provider(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    root.mkdir()
    store = ProviderIntelligenceStore(
        tmp_path, staging=True, signing_key=b"test-only-key-not-for-production"
    )
    selected = store.for_project(PROJECT)
    candidate_path = selected.root / "local-architect-candidate.json"

    monkeypatch.setattr(cli, "ProviderIntelligenceStore", lambda: store)
    monkeypatch.setattr(cli, "ProjectRegistry", lambda: SimpleNamespace(
        verify=lambda _: SimpleNamespace(
            project_id=PROJECT, repository_root=str(root)
        ),
    ))
    monkeypatch.setattr(cli, "_git_output", lambda *_: HEAD)
    authority = SimpleNamespace(
        policy=SimpleNamespace(
            policy_id="merge-policy-adr-0003", policy_hash="b"*64,
        ),
        constitution=SimpleNamespace(
            constitution_id="constitution-agf-v1", record_hash="c"*64,
        ),
        policy_snapshot={"generation":3, "active_policy_hash":"b"*64},
        context=SimpleNamespace(generation_number=3),
    )
    monkeypatch.setattr(cli, "resolve_authority", lambda _: authority)

    def no_codex():
        raise AssertionError("Codex must never be selected for local Architect")
    monkeypatch.setattr(cli, "resolve_codex_executable", no_codex)

    class FakeLocal:
        timeout = 90.0
        def __init__(self, *, timeout=90.0):
            self.timeout = timeout
        def execute(self, prompt, repository, *, sandbox):
            assert sandbox == "read-only"
            assert repository == str(root)
            assert HEAD in prompt
            return CodexProcessResult(
                "local canary", 0, "", "", invocation_verified=True,
                final_message=json.dumps({
                    "head_sha":HEAD,
                    "repository_name":"project",
                    "reasoning_answer":"6",
                    "context_marker":"AGF-CONTEXT-CANARY-7f3d",
                    "summary":"Canary checked",
                }),
            )
    monkeypatch.setattr(cli, "LocalArchitectAdapter", FakeLocal)
    args = SimpleNamespace(
        project=PROJECT, intelligence_command="bootstrap-architect",
        renew=True, candidate_output=str(candidate_path),
        json=True, interface="omniroute-local",
    )
    assert cli.run_provider_intelligence(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["probe_pass"] is True
    assert result["gates"]["privacy_eligible"] is True
    assert result["gates"]["empirical_evidence_eligible"] is True
    candidate = state_from_dict(json.loads(candidate_path.read_text()))
    assert candidate.provider_interfaces == (
        (LOCAL_ARCHITECT_PROVIDER_ID, "omniroute-local"),
    )
    assert candidate.candidates[0].profile.provider_id == LOCAL_ARCHITECT_PROVIDER_ID
    assert candidate.target_sha == HEAD
    assert not selected.path.exists(), "unsigned canary must not auto-activate provider"
