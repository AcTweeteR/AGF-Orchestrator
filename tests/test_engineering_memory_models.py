import json
from pathlib import Path

import pytest

from agf_orchestrator.engineering_memory_models import (
    MemoryEntryType,
    MemorySensitivity,
    MemoryValidationError,
    memory_entry_hash,
    memory_from_dict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "memory"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_memory_entry_round_trips_and_hashes_deterministically():
    entry = memory_from_dict(load_fixture("valid_entry.json"))

    assert entry.entry_type is MemoryEntryType.INVARIANT
    assert entry.sensitivity is MemorySensitivity.INTERNAL
    assert entry.to_dict() == load_fixture("valid_entry.json")
    assert memory_entry_hash(entry) == entry.content_sha256


def test_transcripts_are_rejected():
    with pytest.raises(MemoryValidationError, match="transcript-shaped"):
        memory_from_dict(load_fixture("invalid_transcript.json"))


def test_unknown_fields_and_secret_shaped_values_are_rejected():
    payload = load_fixture("valid_entry.json")
    payload["transcript"] = "not permitted"
    with pytest.raises(MemoryValidationError, match="missing or contains unknown"):
        memory_from_dict(payload)

    payload = load_fixture("valid_entry.json")
    payload["summary"] = "token: do-not-store"
    with pytest.raises(MemoryValidationError, match="secret-shaped"):
        memory_from_dict(payload)


def test_project_and_supersession_identity_are_bounded():
    payload = load_fixture("valid_entry.json")
    payload["project_id"] = "other-project"
    with pytest.raises(MemoryValidationError, match="project_id is invalid"):
        memory_from_dict(payload)

    payload = load_fixture("valid_entry.json")
    payload["supersedes_entry_id"] = payload["entry_id"]
    with pytest.raises(MemoryValidationError, match="cannot supersede itself"):
        memory_from_dict(payload)
