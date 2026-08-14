import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator.historical_evidence import (
    EvidenceStatus,
    HistoricalEvidenceError,
    load_historical_evidence,
)


def payload(project="project-ec392dd7e95cf253", evidence_type="incident", count=0):
    value = {
        "schema_version": "1.0",
        "project_id": project,
        "evidence_type": evidence_type,
        "status": (
            EvidenceStatus.VERIFIED_ZERO.value
            if count == 0
            else EvidenceStatus.VERIFIED_EVENTS.value
        ),
        "count": count,
        "coverage_start": "2026-08-13T12:28:22+00:00",
        "coverage_end": "2026-08-14T12:28:22+00:00",
        "definition_version": "1.0",
        "source_refs": ["source-ledger"],
        "source_hashes": ["a" * 64],
        "policy_hash": "b" * 64,
        "constitution_id": "constitution-v1",
        "authority_generation": 1,
        "generated_at": "2026-08-14T12:28:22+00:00",
        "provenance": "owner-controller:test",
        "coverage_complete": True,
        "completeness_basis": "owner-completeness-v1:test owner completeness assertion",
    }
    value["evidence_hash"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return value


def envelope(payload):
    return {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": "owner-key-1",
        "public_key_fingerprint": (
            "d23e23484571f256610658dd2b851ef3e4144dbd03827b8a66ee421c93ffe42a"
        ),
        "payload_hash": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "signature": base64.b64encode(b"invalid").decode(),
    }


def test_missing_history_is_unknown(tmp_path):
    assert (
        load_historical_evidence(
            "project-ec392dd7e95cf253", "incident", state_root=tmp_path
        )
        is None
    )


def test_tampered_or_invalid_signed_history_fails_closed(tmp_path):
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    document = payload()
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": envelope(document)}), encoding="utf-8"
    )
    with pytest.raises(HistoricalEvidenceError, match="signature"):
        load_historical_evidence("project-ec392dd7e95cf253", "incident", state_root=tmp_path)


def test_zero_and_event_counts_are_distinct_in_schema(tmp_path):
    zero = payload()
    events = payload(evidence_type="rollback", count=2)
    assert zero["status"] == "VERIFIED_ZERO"
    assert events["status"] == "VERIFIED_EVENTS"


def test_cross_project_payload_is_rejected_before_binding(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.historical_evidence.verify_envelope", lambda payload, envelope: None
    )
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    document = payload(project="project-1111111111111111")
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": envelope(document)}), encoding="utf-8"
    )
    with pytest.raises(HistoricalEvidenceError, match="project"):
        load_historical_evidence("project-ec392dd7e95cf253", "incident", state_root=tmp_path)


def test_stale_or_narrow_coverage_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.historical_evidence.verify_envelope", lambda payload, envelope: None
    )
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    document = payload()
    document["coverage_start"] = "2026-08-14T00:00:00+00:00"
    document["coverage_end"] = "2026-08-14T00:01:00+00:00"
    document["evidence_hash"] = hashlib.sha256(
        json.dumps({key: value for key, value in document.items() if key != "evidence_hash"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": envelope(document)}), encoding="utf-8"
    )
    assert load_historical_evidence(
        "project-ec392dd7e95cf253", "incident", state_root=tmp_path,
        required_start="2026-08-13T12:28:22+00:00",
    ) is None


def test_valid_ed25519_record_is_consumed_with_pinned_test_anchor(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    fingerprint = hashlib.sha256(public).hexdigest()
    root = tmp_path / "owner-root"
    root.mkdir(mode=0o700)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode())
    (root / "anchor.json").write_text(
        json.dumps({
            "schema_version": "1.0", "signature_scheme": "Ed25519",
            "key_id": "owner-key-1", "fingerprint": fingerprint,
        })
    )
    root.chmod(0o700)
    monkeypatch.setattr("agf_orchestrator.owner_authority.DEFAULT_ROOT", root)
    monkeypatch.setattr(
        "agf_orchestrator.owner_authority.PINNED_OWNER_FINGERPRINT", fingerprint
    )
    now = datetime.now(UTC).replace(microsecond=0)
    document = payload()
    document["coverage_start"] = (now - timedelta(hours=1)).isoformat()
    document["coverage_end"] = now.isoformat()
    document["generated_at"] = now.isoformat()
    document["evidence_hash"] = hashlib.sha256(
        json.dumps({key: value for key, value in document.items() if key != "evidence_hash"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    signed = {
        "signature_scheme": "Ed25519", "signature_version": "1", "key_id": "owner-key-1",
        "public_key_fingerprint": fingerprint,
        "payload_hash": hashlib.sha256(canonical).hexdigest(),
        "signature": base64.b64encode(key.sign(canonical)).decode(),
    }
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": signed}), encoding="utf-8"
    )
    verified = load_historical_evidence(
        "project-ec392dd7e95cf253", "incident", state_root=tmp_path,
        required_start=(now - timedelta(minutes=30)).isoformat(),
    )
    assert verified is not None
    assert verified.status is EvidenceStatus.VERIFIED_ZERO
