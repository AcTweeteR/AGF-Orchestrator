"""Verified local Architect is evidence-driven, never an auth or cost bypass."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agf_orchestrator.adapters.local_architect import (
    LOCAL_ARCHITECT_MODEL,
    LOCAL_ARCHITECT_PROVIDER_ID,
    LocalArchitectAdapter,
    local_architect_provenance,
)
from agf_orchestrator.capability_profiles import CapabilityStatus
from agf_orchestrator.capability_selection import CapabilityCandidate, SelectionGates
from agf_orchestrator.model_gateway import GatewayResult, ModelGatewayError
from agf_orchestrator.provider_intelligence import (
    ARCHITECT_REQUIREMENTS,
    ProviderIntelligenceError,
    build_state,
    make_profile,
    state_from_dict,
)


def _local_state(interface="omniroute-local", privacy=True, profile_version=1):
    project_id = "project-efc8e8ef7be7050b"
    profile = make_profile(
        project_id=project_id,
        provider_id=LOCAL_ARCHITECT_PROVIDER_ID,
        provenance_source=local_architect_provenance(),
        observed_at="2026-11-04T17:00:00Z",
        expires_at="2026-11-05T17:00:00Z",
        capability_results={
            item: CapabilityStatus.SUPPORTED for item in ARCHITECT_REQUIREMENTS
        },
        profile_version=profile_version,
    )
    gates = SelectionGates(True, privacy, True, True, True, True)
    evidence = (
        ("policy_eligible", "active-policy:merge-policy-adr-0003:"+"a"*64),
        ("privacy_eligible",
         "omniroute-loopback-only-v1;read-only-canary;"+str(privacy)),
        ("independence_eligible", "architect-advisory;reviewer-separate-stage;True"),
        ("budget_eligible", "bounded-timeout-seconds:90;True"),
        ("health_eligible", "invocation-verified:True"),
        ("empirical_evidence_eligible", "deterministic-canary-sha256:"+"b"*64),
    )
    return build_state(
        project_id=project_id, target_sha="f"*40,
        constitution_id="constitution-agf-v1", constitution_record_hash="c"*64,
        observed_at="2026-11-04T17:00:00Z",
        expires_at="2026-11-05T17:00:00Z",
        candidates=(CapabilityCandidate(profile, priority=0),),
        provider_interfaces=((LOCAL_ARCHITECT_PROVIDER_ID, interface),),
        gates=gates, gate_evidence=evidence, policy_generation=3,
    )


def test_local_provider_requires_exact_own_identity_and_prefix():
    value = _local_state()
    value.validate(now="2026-11-04T18:00:00Z", target_sha="f"*40)
    with pytest.raises(ProviderIntelligenceError, match="privacy"):
        _local_state(interface="codex").validate()
    with pytest.raises(ProviderIntelligenceError, match="interface"):
        _local_state(interface="arbitrary").validate()


def test_local_cannot_borrow_other_provider_eligibility():
    value = _local_state()
    payload = value.to_dict()
    payload["provider_interfaces"] = [
        [LOCAL_ARCHITECT_PROVIDER_ID, "omniroute-local"],
        ["provider-codex", "codex"],
    ]
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)
    payload = value.to_dict()
    payload["gate_evidence"] = [
        [name, "codex-safe-environment-v1;read-only-canary;True"
         if name == "privacy_eligible" else content]
        for name, content in payload["gate_evidence"]
    ]
    with pytest.raises(ProviderIntelligenceError):
        state_from_dict(payload)


def test_local_transport_accepts_exact_model_and_read_only_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AGF_OMNIROUTE_KEY", "test-credential")
    reply = GatewayResult(
        model=LOCAL_ARCHITECT_MODEL, provider="ollama",
        content='{"proposed_outcome":"NO_JUSTIFIED_WORK"}',
        prompt_tokens=22, completion_tokens=15,
    )
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider",
    ) as gateway:
        gateway.return_value.complete.return_value = reply
        result = LocalArchitectAdapter().execute(
            "Return only JSON", str(tmp_path), sandbox="read-only",
        )
        assert gateway.call_args.kwargs == {"token": "test-credential"}
        arguments = gateway.return_value.complete.call_args.kwargs
        assert arguments["eligible_models"] == frozenset({LOCAL_ARCHITECT_MODEL})
        assert arguments["verified_zero_cost"] is True
    assert result.invocation_verified
    assert result.execution_observation["monetary_cost_eur"] == 0
    assert "test-credential" not in repr(result)
    assert result.final_message == reply.content
    with pytest.raises(ValueError):
        LocalArchitectAdapter(model="openai/paid")
    with pytest.raises(ValueError):
        LocalArchitectAdapter(timeout=300)
    assert LocalArchitectAdapter().execute(
        "unsafe", str(tmp_path), sandbox="workspace-write",
    ).human_required


def test_local_transport_failure_never_falls_back_or_claims_invocation(tmp_path):
    with patch(
        "agf_orchestrator.adapters.local_architect.ModelGatewayProvider.complete",
        side_effect=ModelGatewayError("unavailable gateway"),
    ) as complete:
        result = LocalArchitectAdapter().execute("canary", str(tmp_path))
    complete.assert_called_once()
    assert result.invocation_verified is False
    assert result.transport_error == "LOCAL_ARCHITECT_GATEWAY_FAILED"
    assert result.final_message is None


def test_source_bound_provenance_is_reproducible_without_secret():
    assert local_architect_provenance().startswith(
        "runtime-canary:omniroute-local:ollama/qwen3.5:9b-q4_K_M:"
    )
    assert len(local_architect_provenance().split(":")[-1]) == 64


def test_signed_local_architect_routes_through_verified_config(
    tmp_path, monkeypatch,
):
    import agf_orchestrator.cli as cli
    from agf_orchestrator.provider_intelligence import (
        ProviderIntelligenceStore,
        sign_state,
    )

    key = b"temporary-owner-fixture-signing-key-not-production"
    identity = "project-efc8e8ef7be7050b"
    store = ProviderIntelligenceStore(
        tmp_path, signing_key=key, staging=True,
    )
    local = store.for_project(identity)
    local.save(sign_state(_local_state(), key, staging=True))

    monkeypatch.setattr(cli, "ProviderIntelligenceStore", lambda: store)
    monkeypatch.setattr(cli, "SessionStore", lambda: SimpleNamespace(
        state_dir=tmp_path,
        load=lambda _: SimpleNamespace(project_id=identity),
    ))
    monkeypatch.setattr(cli, "ProjectRegistry", lambda: SimpleNamespace(
        get=lambda _: SimpleNamespace(project_id=identity, repository_root=str(tmp_path)),
    ))
    monkeypatch.setattr(cli, "_git_output", lambda *_: "f"*40)
    monkeypatch.setattr(cli, "resolve_authority", lambda *_: SimpleNamespace(
        policy_snapshot={},
    ))
    monkeypatch.setattr(
        cli, "_validate_provider_intelligence_runtime",
        lambda state, project, sha, snapshot, now: state.validate(
            now=now, target_sha=sha,
        ),
    )
    monkeypatch.setattr(cli, "_now", lambda: "2026-11-04T18:00:00Z")
    cfg = SimpleNamespace(
        session="session-test", session_command="assess",
        architect_config=str(local.path),
    )
    architect = cli._architect_from_config(cfg)
    assert isinstance(
        architect.providers[LOCAL_ARCHITECT_PROVIDER_ID].adapter,
        LocalArchitectAdapter,
    )
    assert architect.providers[LOCAL_ARCHITECT_PROVIDER_ID].adapter.timeout == 90

    # Mutating the bound profile or its signature may never enable model use.
    payload = json.loads(local.path.read_text())
    payload["provider_interfaces"][0][1] = "codex"
    local.path.write_text(json.dumps(payload))
    with pytest.raises(Exception):
        cli._architect_from_config(cfg)
