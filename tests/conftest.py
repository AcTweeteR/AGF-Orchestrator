import os
from pathlib import Path

import pytest
from provider_test_support import verify_envelope

from agf_orchestrator import provider_intelligence

# Module-level fixtures in the provider tests are created during collection;
# install the generated test trust root before that collection happens.
provider_intelligence.verify_envelope = verify_envelope


@pytest.fixture(autouse=True)
def isolate_llm_environment():
    """Prevent a developer's ignored .env from leaking between tests."""
    keys = ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")
    original = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def isolate_external_agf_state(monkeypatch, tmp_path):
    """Keep tests away from the owner's canonical external state root."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


@pytest.fixture(autouse=True)
def install_test_owner_verifier(monkeypatch):
    """Use a generated Ed25519 owner fixture only inside the test harness."""
    monkeypatch.setattr(provider_intelligence, "verify_envelope", verify_envelope)
