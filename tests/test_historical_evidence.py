import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator.delivery import _load_prospective_evidence
from agf_orchestrator.historical_evidence import (
    EvidenceStatus,
    HistoricalEvidenceError,
    load_historical_baseline,
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
        "baseline_id": "baseline-test-00000000",
        "coverage_before_baseline": "UNKNOWN",
        "evidence_generation": 1,
        "predecessor_evidence_hash": None,
        "renewal_operation_id": "historical-renewal-initial",
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


def test_generated_before_coverage_start_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.historical_evidence.verify_envelope", lambda payload, envelope: None
    )
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    document = payload()
    document["generated_at"] = "2026-08-13T12:00:00+00:00"
    document["evidence_hash"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in document.items() if key != "evidence_hash"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()
    ).hexdigest()
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": envelope(document)}), encoding="utf-8"
    )
    assert load_historical_evidence(
        "project-ec392dd7e95cf253", "incident", state_root=tmp_path
    ) is None


def test_symlinked_historical_namespace_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    namespace = tmp_path / "historical-evidence"
    os.symlink(outside, namespace)
    with pytest.raises(HistoricalEvidenceError, match="symlinks"):
        load_historical_evidence(
            "project-ec392dd7e95cf253", "incident", state_root=tmp_path
        )


def test_symlinked_predecessor_namespace_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agf_orchestrator.historical_evidence.verify_envelope", lambda payload, envelope: None
    )
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    document = payload()
    document.update({
        "evidence_generation": 2,
        "predecessor_evidence_hash": "a" * 64,
        "renewal_operation_id": "historical-renewal-test-00000000",
    })
    document["evidence_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in document.items() if k != "evidence_hash"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, path / "history")
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": {}}), encoding="utf-8"
    )
    with pytest.raises(HistoricalEvidenceError, match="unavailable|symlinks"):
        load_historical_evidence(
            "project-ec392dd7e95cf253", "incident", state_root=tmp_path
        )


def test_generation_two_requires_signed_predecessor_and_journal(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    fingerprint = hashlib.sha256(public).hexdigest()
    root = tmp_path / "owner-root"
    root.mkdir(mode=0o700)
    (root / "owner-public.key").write_text(base64.b64encode(public).decode())
    (root / "anchor.json").write_text(json.dumps({
        "schema_version": "1.0", "signature_scheme": "Ed25519",
        "key_id": "owner-key-1", "fingerprint": fingerprint,
    }))
    monkeypatch.setattr("agf_orchestrator.owner_authority.DEFAULT_ROOT", root)
    monkeypatch.setattr("agf_orchestrator.owner_authority.PINNED_OWNER_FINGERPRINT", fingerprint)

    def signed(value):
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return {
            "signature_scheme": "Ed25519", "signature_version": "1", "key_id": "owner-key-1",
            "public_key_fingerprint": fingerprint,
            "payload_hash": hashlib.sha256(canonical).hexdigest(),
            "signature": base64.b64encode(key.sign(canonical)).decode(),
        }

    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    (path / "history").mkdir(parents=True)
    previous = payload()
    now = datetime.now(UTC).replace(microsecond=0)
    previous["coverage_start"] = (now - timedelta(hours=2)).isoformat()
    previous["coverage_end"] = (now - timedelta(hours=1)).isoformat()
    previous["generated_at"] = previous["coverage_end"]
    previous["evidence_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in previous.items() if k != "evidence_hash"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    previous_hash = previous["evidence_hash"]
    now = now.isoformat()
    current = payload()
    current.update({
        "coverage_start": previous["coverage_end"], "coverage_end": now,
        "generated_at": now, "evidence_generation": 2,
        "predecessor_evidence_hash": previous_hash,
        "renewal_operation_id": "historical-renewal-test-00000000",
    })
    current["evidence_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in current.items() if k != "evidence_hash"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    (path / "history" / f"incident-{previous_hash}.json").write_text(
        json.dumps({"payload": previous, "envelope": signed(previous)}), encoding="utf-8"
    )
    journal = {
        "status": "COMMITTED", "operation_id": current["renewal_operation_id"],
        "project_id": current["project_id"], "baseline_id": current["baseline_id"],
        "evidence_type": "incident", "evidence_generation": 2,
        "predecessor_evidence_hashes": {"incident": previous_hash},
        "policy_hash": current["policy_hash"], "constitution_id": current["constitution_id"],
        "authority_generation": 1, "evidence_hashes": {"incident": current["evidence_hash"]},
    }
    (path / "renewal-journal.json").write_text(
        json.dumps({"payload": journal, "envelope": signed(journal)}), encoding="utf-8"
    )
    (path / "incident.json").write_text(
        json.dumps({"payload": current, "envelope": signed(current)}), encoding="utf-8"
    )
    assert load_historical_evidence(
        "project-ec392dd7e95cf253", "incident", state_root=tmp_path
    ).evidence_generation == 2


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
    monkeypatch.setattr(
        "agf_orchestrator.historical_evidence.verify_current_baseline_bindings",
        lambda baseline: None,
    )
    now = datetime.now(UTC).replace(microsecond=0)
    baseline_payload = {
        "schema_version": "1.0",
        "baseline_id": "baseline-test-00000000",
        "project_id": "project-ec392dd7e95cf253",
        "coverage_start": (now - timedelta(hours=1)).isoformat(),
        "policy_hash": "b" * 64,
        "constitution_id": "constitution-v1",
        "authority_generation": 1,
        "generated_at": now.isoformat(),
        "provenance": "owner-controller:test",
    }
    baseline_bytes = json.dumps(
        baseline_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    baseline_envelope = {
        "signature_scheme": "Ed25519", "signature_version": "1", "key_id": "owner-key-1",
        "public_key_fingerprint": fingerprint,
        "payload_hash": hashlib.sha256(baseline_bytes).hexdigest(),
        "signature": base64.b64encode(key.sign(baseline_bytes)).decode(),
    }
    path = tmp_path / "historical-evidence" / "project-ec392dd7e95cf253"
    path.mkdir(parents=True)
    (path / "baseline.json").write_text(
        json.dumps({"payload": baseline_payload, "envelope": baseline_envelope}),
        encoding="utf-8",
    )
    legacy_baseline = load_historical_baseline(
        "project-ec392dd7e95cf253", state_root=tmp_path
    )
    assert legacy_baseline.baseline_id == "baseline-test-00000000"
    assert legacy_baseline.authoritative is False
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
    (path / "incident.json").write_text(
        json.dumps({"payload": document, "envelope": signed}), encoding="utf-8"
    )
    verified = load_historical_evidence(
        "project-ec392dd7e95cf253", "incident", state_root=tmp_path,
        required_start=(now - timedelta(minutes=30)).isoformat(),
    )
    assert verified is not None
    assert verified.status is EvidenceStatus.VERIFIED_ZERO
    monkeypatch.setattr(
        "agf_orchestrator.delivery.load_historical_baseline",
        lambda project_id, **kwargs: load_historical_baseline(project_id, state_root=tmp_path),
    )
    monkeypatch.setattr(
        "agf_orchestrator.delivery.load_historical_evidence",
        lambda project_id, evidence_type, **kwargs: load_historical_evidence(
            project_id, evidence_type, state_root=tmp_path
        ),
    )
    prospective = _load_prospective_evidence(
        "project-ec392dd7e95cf253", "incident", now.isoformat(), 86400
    )
    assert prospective is None
