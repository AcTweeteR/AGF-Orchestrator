"""Configuration-to-adapter binding, with isolated owner evidence and no live provider."""

import json
from types import SimpleNamespace

import pytest
from test_provider_intelligence import NOW, PROJECT, TARGET, TEST_KEY, candidate, state

import agf_orchestrator.cli as cli
from agf_orchestrator.adapters.codex import CodexAdapter
from agf_orchestrator.adapters.openhands import OpenHandsSDKAdapter
from agf_orchestrator.architect_planning import ArchitectPlanningError
from agf_orchestrator.provider_intelligence import ProviderIntelligenceStore, sign_state


def configuration(tmp_path, monkeypatch, interface):
    root = ProviderIntelligenceStore(tmp_path, signing_key=TEST_KEY, staging=True)
    intelligence = root.for_project(PROJECT)
    provider_id = f"provider-{interface}"
    value = state(candidates=(candidate(provider_id=provider_id),),
                  provider_interfaces=((provider_id, interface),))
    intelligence.save(sign_state(value, TEST_KEY, staging=True))
    monkeypatch.setattr(cli, "ProviderIntelligenceStore", lambda: root)
    monkeypatch.setattr(cli, "SessionStore", lambda: SimpleNamespace(
        state_dir=tmp_path, load=lambda _: SimpleNamespace(project_id=PROJECT),
    ))
    monkeypatch.setattr(cli, "ProjectRegistry", lambda: SimpleNamespace(
        get=lambda _: SimpleNamespace(project_id=PROJECT, repository_root=str(tmp_path)),
    ))
    monkeypatch.setattr(cli, "_git_output", lambda *args: TARGET)
    monkeypatch.setattr(cli, "resolve_authority", lambda _: SimpleNamespace(policy_snapshot={}))
    # This test isolates the CLI adapter-construction boundary. The durable
    # owner signature and state validation remain real; runtime root/executable
    # authorization belongs to the existing authority tests.
    monkeypatch.setattr(cli, "_validate_provider_intelligence_runtime",
                        lambda value, project, target, snapshot, now:
                        value.validate(now=now, target_sha=target))
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    args = SimpleNamespace(session_command="assess", session="session-timeout-test",
                           architect_config=str(intelligence.path))
    return args, intelligence, provider_id


@pytest.mark.parametrize("interface,adapter_type", [
    ("codex", CodexAdapter), ("openhands", OpenHandsSDKAdapter),
])
def test_verified_gate_timeout_reaches_real_adapter_constructor(
    tmp_path, monkeypatch, interface, adapter_type,
):
    args, _, provider_id = configuration(tmp_path, monkeypatch, interface)
    architect = cli._architect_from_config(args)
    adapter = architect.providers[provider_id].adapter
    assert isinstance(adapter, adapter_type)
    assert adapter_type().timeout == 300.0
    assert adapter.timeout == 90.0
    if interface == "openhands":
        assert adapter.allow_llm_env is False


@pytest.mark.parametrize("interface", ["codex", "openhands"])
def test_tampered_timeout_configuration_never_constructs_or_invokes_provider(
    tmp_path, monkeypatch, interface,
):
    args, intelligence, _ = configuration(tmp_path, monkeypatch, interface)
    payload = json.loads(intelligence.path.read_text())
    payload["gate_evidence"] = [
        [name, "bounded-timeout-seconds:300;True" if name == "budget_eligible" else evidence]
        for name, evidence in payload["gate_evidence"]
    ]
    intelligence.path.write_text(json.dumps(payload))

    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration must fail before adapter construction")

    monkeypatch.setattr(cli, "CodexAdapter", forbidden)
    monkeypatch.setattr(cli, "OpenHandsSDKAdapter", forbidden)
    with pytest.raises(ArchitectPlanningError, match="architect config is invalid"):
        cli._architect_from_config(args)
