"""External owner controller for the one-time Ed25519 trust-anchor ceremony.

This file is intentionally outside the AGF runtime package.  It is the only
component in this repository allowed to load the legacy HMAC owner secret or
the Ed25519 private key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agf_orchestrator.authority_context import AuthorityContext, resolve_authority
from agf_orchestrator.authority_generation import (
    AuthorityComponent,
    AuthorityGenerationStore,
    GenerationStatus,
    _atomic_write,
    build_generation,
)
from agf_orchestrator.constitution import ConstitutionAuthority, canonical_json
from agf_orchestrator.delivery_reconciliation import DeliveryIntentStore
from agf_orchestrator.historical_evidence import (
    EvidenceStatus,
    HistoricalEvidenceError,
    load_historical_baseline,
    load_historical_evidence,
    verify_current_bindings,
)
from agf_orchestrator.historical_evidence import _parse as _parse_historical_evidence
from agf_orchestrator.locking import project_lock
from agf_orchestrator.owner_authority import (
    PINNED_OWNER_FINGERPRINT,
    canonical_bytes,
    load_pinned_anchor,
    verify_envelope,
)
from agf_orchestrator.policy_authority import PolicyAuthority
from agf_orchestrator.policy_state_store import PolicyStateStore
from agf_orchestrator.project_registry import ProjectRegistry, parse_remote_url
from agf_orchestrator.provider_intelligence import ProviderIntelligenceStore, state_from_dict

_PROJECT_ID = re.compile(r"^project-[0-9a-f]{16}$")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _legacy_key(state_dir: Path) -> bytes:
    authority = state_dir / "constitution-authority"
    path = authority / "owner.key"
    if authority.is_symlink() or path.is_symlink() or authority.stat().st_mode & 0o077:
        raise RuntimeError("legacy owner authority permissions are invalid")
    key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    if len(key) < 32:
        raise RuntimeError("legacy owner authority is invalid")
    return key


def _private_key(root: Path) -> Ed25519PrivateKey:
    path = root / "owner-private.key"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("owner Ed25519 private-key permissions are invalid")
    try:
        value = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
        return Ed25519PrivateKey.from_private_bytes(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("owner Ed25519 private key is unavailable") from exc


def sign_envelope(payload: object, root: Path) -> dict[str, str]:
    """Sign one payload for an external owner operation; never returns key material."""
    private = _private_key(root)
    public = private.public_key().public_bytes_raw()
    payload_bytes = canonical_bytes(payload)
    return {
        "signature_scheme": "Ed25519",
        "signature_version": "1",
        "key_id": "owner-ed25519-1",
        "public_key_fingerprint": hashlib.sha256(public).hexdigest(),
        "payload_hash": hashlib.sha256(payload_bytes).hexdigest(),
        "signature": base64.b64encode(private.sign(payload_bytes)).decode(),
    }


def _candidate_path(project_id: str, candidate: Path) -> Path:
    requested = candidate.expanduser()
    state_root = (Path.home() / ".agf-orchestrator").resolve()
    expected_parent = state_root / "capability-intelligence" / project_id
    if any(part.is_symlink() for part in [requested, *requested.parents]):
        raise RuntimeError("provider candidate path must not contain symlinks")
    resolved = requested.resolve(strict=True)
    if resolved.parent != expected_parent or resolved.is_symlink():
        raise RuntimeError("provider candidate must remain in its project namespace")
    return resolved


def _activate_provider_candidate(
    project_id: str, candidate: Path, *, allow_renewal: bool = False
) -> dict[str, str]:
    """Owner-sign a provider candidate produced by the read-only runtime step."""
    candidate = _candidate_path(project_id, candidate)
    state = state_from_dict(json.loads(candidate.read_text(encoding="utf-8")))
    if state.project_id != project_id:
        raise RuntimeError("provider candidate project binding is invalid")
    project = ProjectRegistry().verify(project_id)
    target_sha = subprocess.run(
        ["git", "-C", project.repository_root, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    authority = resolve_authority(project_id)
    if authority.policy is None or authority.constitution is None:
        raise RuntimeError("current project authority is unavailable")
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state.validate(now=now, target_sha=target_sha)
    if state.constitution_id != authority.constitution.constitution_id:
        raise RuntimeError("provider candidate Constitution binding is stale")
    if state.constitution_record_hash != authority.constitution.record_hash:
        raise RuntimeError("provider candidate Constitution hash is stale")
    expected_generation = (
        authority.context.generation_number
        if authority.context is not None
        else authority.policy_snapshot.get("generation")
        if isinstance(authority.policy_snapshot, dict)
        else None
    )
    if state.policy_generation != expected_generation:
        raise RuntimeError("provider candidate policy generation is stale")
    evidence = dict(state.gate_evidence)
    expected_policy_evidence = (
        f"active-policy:{authority.policy.policy_id}:{authority.policy.policy_hash}"
    )
    if evidence.get("policy_eligible") != expected_policy_evidence:
        raise RuntimeError("provider candidate policy evidence is stale")
    unsigned_signed = state.__class__(
        **{
            **state.__dict__,
            "signing_key_id": "owner-ed25519-1",
            "signature": None,
            "state_sha256": "0" * 64,
        }
    )
    envelope = sign_envelope(unsigned_signed._unsigned(), Path.home() / ".agf-owner-root")
    unsigned_signed = state.__class__(
        **{**unsigned_signed.__dict__, "signature": envelope}
    )
    state_hash = hashlib.sha256(
        json.dumps(
            unsigned_signed._unsigned(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    signed = unsigned_signed.__class__(**{**unsigned_signed.__dict__, "state_sha256": state_hash})
    store = ProviderIntelligenceStore().for_project(project_id)
    store._ensure_safe_path()
    store._save_locked(signed, allow_renewal=allow_renewal)
    return {"project_id": project_id, "state_sha256": signed.state_sha256}


def _verify_legitimate_target_advancement(
    project_id: str, project, previous, proposed, actual_target_sha: str
) -> None:
    """Require repository-derived delivery evidence for a provider target advance."""
    if proposed.target_sha == previous.target_sha:
        return
    if proposed.target_sha != actual_target_sha:
        raise RuntimeError("provider renewal target is not the current repository HEAD")
    try:
        subprocess.run(
            [
                "git", "-C", project.repository_root, "merge-base", "--is-ancestor",
                previous.target_sha, proposed.target_sha,
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("provider renewal target advancement is not a valid descendant") from exc
    store = DeliveryIntentStore()
    directory = store.root / project_id
    if store.root.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("provider renewal target advancement lacks delivery evidence")
    matches = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".receipt.json") or path.is_symlink():
            continue
        delivery_id = path.stem
        intent = store.get(project_id, delivery_id)
        if intent is None:
            continue
        if (
            intent.base_sha == previous.target_sha
            and intent.candidate_sha == proposed.target_sha
            and intent.target_branch == project.default_branch
            and parse_remote_url(intent.repository_identity).identity
            == parse_remote_url(project.origin_url).identity
        ):
            receipt = store.observe(project_id, delivery_id, project.repository_root)
            if receipt.observed_sha != proposed.target_sha:
                raise RuntimeError("provider renewal delivery receipt target differs")
            matches.append((intent, receipt))
    if len(matches) != 1:
        raise RuntimeError("provider renewal requires one authoritative delivery receipt")


def activate_provider_candidate(project_id: str, candidate: Path) -> dict[str, str]:
    with project_lock(Path.home() / ".agf-orchestrator", project_id, "provider-activate"):
        return _activate_provider_candidate(project_id, candidate, allow_renewal=False)


def renew_provider_candidate(project_id: str, candidate: Path) -> dict[str, str]:
    """Owner-activate only a newly observed, higher-version provider candidate."""
    with project_lock(Path.home() / ".agf-orchestrator", project_id, "provider-renew"):
        store = ProviderIntelligenceStore().for_project(project_id)
        previous = store._load_for_owner_recovery()
        candidate_path = _candidate_path(project_id, candidate)
        proposed = state_from_dict(json.loads(candidate_path.read_text(encoding="utf-8")))
        if proposed.project_id != previous.project_id:
            raise RuntimeError("provider renewal binding differs from active evidence")
        if proposed.target_sha != previous.target_sha:
            project = ProjectRegistry().verify(project_id)
            actual_target_sha = subprocess.run(
                ["git", "-C", project.repository_root, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            _verify_legitimate_target_advancement(
                project_id, project, previous, proposed, actual_target_sha
            )
        old_versions = {
            item.profile.profile_id: item.profile.profile_version
            for item in previous.candidates
        }
        new_versions = {
            item.profile.profile_id: item.profile.profile_version
            for item in proposed.candidates
        }
        if not old_versions or any(
            new_versions.get(key, 0) <= value for key, value in old_versions.items()
        ):
            raise RuntimeError("provider renewal requires higher profile versions")
        _require_fresh_profile_observations(previous, proposed)
        if proposed.observed_at <= previous.observed_at:
            raise RuntimeError("provider renewal requires a fresh observation")
        result = _activate_provider_candidate(project_id, candidate_path, allow_renewal=True)
        current = store.load()
        if current.state_sha256 == previous.state_sha256:
            raise RuntimeError("provider renewal did not create new evidence")
        return {**result, "previous_state_sha256": previous.state_sha256, "renewed": "true"}


def create_prospective_baseline(
    project_id: str, operation_id: str, *, target_sha: str | None = None
) -> dict[str, object]:
    """Create one signed prospective historical-evidence checkpoint.

    This is intentionally an external owner operation.  Runtime historical
    verification has no mutation path and cannot call this function.
    """
    if not _PROJECT_ID.fullmatch(project_id) or not operation_id.startswith(
        "historical-baseline-"
    ):
        raise RuntimeError("baseline identity is invalid")
    state_dir = _migration_state_dir()
    with project_lock(state_dir, project_id, "historical-baseline-create"):
        registry = ProjectRegistry(state_dir)
        project = registry.verify_read_only(project_id)
        if project.status.value != "ACTIVE":
            raise RuntimeError("project registration is not ACTIVE")
        authority = resolve_authority(project_id)
        if authority.constitution is None or authority.policy is None or authority.snapshot is None:
            raise RuntimeError("current project authority is unavailable")
        root = Path(project.repository_root)
        observed_sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        ).stdout.strip()
        if project.current_head_sha != observed_sha:
            raise RuntimeError("registered target SHA is stale")
        if target_sha is not None and target_sha != observed_sha:
            raise RuntimeError("requested target SHA does not match the registered target")
        directory = state_dir / "historical-evidence" / project_id
        baseline_path = directory / "baseline.json"
        journal_path = directory / "baseline-journal.json"
        activation_path = directory / "baseline-activation.json"
        ledger_directory = directory / "ledgers"
        ledger_paths = {
            evidence_type: ledger_directory / f"{evidence_type}-ledger.json"
            for evidence_type in ("rollback", "incident")
        }
        if directory.is_symlink() or ledger_directory.is_symlink():
            raise RuntimeError("historical baseline namespace must not use symlinks")
        if baseline_path.exists():
            try:
                existing = load_historical_baseline(project_id, state_root=state_dir)
            except HistoricalEvidenceError as exc:
                raise RuntimeError("existing prospective baseline is invalid") from exc
            if existing is None or existing.operation_id != operation_id:
                raise RuntimeError("conflicting active prospective baseline exists")
            if (
                existing.target_sha != observed_sha
                or existing.target_identity != project.origin_url
            ):
                raise RuntimeError("idempotent baseline request binding differs")
            return {
                "status": "ALREADY_COMMITTED",
                "project_id": project_id,
                "baseline_id": existing.baseline_id,
                "operation_id": operation_id,
                "target_sha": observed_sha,
                "coverage_start": existing.coverage_start,
                "baseline_generation": existing.baseline_generation,
            }
        if journal_path.exists():
            journal = _read_object(journal_path).get("payload", {})
            if journal.get("operation_id") == operation_id:
                if journal.get("status") == "PREPARED":
                    raise RuntimeError("incomplete baseline activation requires explicit recovery")
                raise RuntimeError("baseline operation has already been consumed")
            raise RuntimeError("conflicting baseline journal exists")
        if any(path.exists() or path.is_symlink() for path in ledger_paths.values()):
            raise RuntimeError("conflicting prospective evidence ledger exists")
        generation = int(authority.snapshot["generation"])
        now = _now()
        baseline_id = "baseline-" + hashlib.sha256(
            canonical_bytes(
                {
                    "project_id": project_id,
                    "target_sha": observed_sha,
                    "operation_id": operation_id,
                }
            )
        ).hexdigest()[:16]
        source_set = [
            f"ledger:{project_id}:ledgers/rollback-ledger.json",
            f"ledger:{project_id}:ledgers/incident-ledger.json",
        ]
        ledger_payloads = {
            "rollback": {
                "schema_version": "1.0",
                "project_id": project_id,
                "baseline_id": baseline_id,
                "evidence_type": "rollback",
                "coverage_before_baseline": "UNKNOWN",
                "coverage_start": now,
                "coverage_status_from_baseline": "AUTHORITATIVE",
                "qualifying_event_definition": "agf-historical-events-v1",
                "records": [],
                "provenance": "external-owner-controller:prospective-baseline",
            },
            "incident": {
                "schema_version": "1.0",
                "project_id": project_id,
                "baseline_id": baseline_id,
                "evidence_type": "incident",
                "coverage_before_baseline": "UNKNOWN",
                "coverage_start": now,
                "coverage_status_from_baseline": "AUTHORITATIVE",
                "qualifying_event_definition": "agf-historical-events-v1",
                "records": [],
                "provenance": "external-owner-controller:prospective-baseline",
            },
        }
        source_hashes = [
            _object_hash(ledger_payloads["rollback"]),
            _object_hash(ledger_payloads["incident"]),
        ]
        payload = {
            "schema_version": "1.0",
            "baseline_id": baseline_id,
            "project_id": project_id,
            "target_identity": project.origin_url,
            "target_sha": observed_sha,
            "coverage_start": now,
            "policy_id": authority.policy.policy_id,
            "policy_hash": authority.policy.policy_hash,
            "policy_generation": generation,
            "constitution_id": authority.constitution.constitution_id,
            "constitution_record_hash": authority.constitution.record_hash,
            "authority_generation": generation,
            "owner_fingerprint": PINNED_OWNER_FINGERPRINT,
            "evidence_definition_version": "agf-historical-events-v1",
            "source_set": source_set,
            "source_hashes": source_hashes,
            "baseline_generation": 1,
            "predecessor_baseline_hash": None,
            "operation_id": operation_id,
            "generated_at": now,
            "provenance": "external-owner-controller:prospective-baseline;pre-baseline=UNKNOWN",
        }
        envelope = sign_envelope(payload, _generation_root())
        baseline_hash = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        journal_payload = {
            "schema_version": "1.0",
            "status": "PREPARED",
            "project_id": project_id,
            "baseline_id": baseline_id,
            "baseline_hash": baseline_hash,
            "baseline_generation": 1,
            "operation_id": operation_id,
            "target_sha": observed_sha,
            "source_hashes": source_hashes,
            "created_at": now,
        }
        _atomic_write(
            journal_path,
            {
                "payload": journal_payload,
                "envelope": sign_envelope(journal_payload, _generation_root()),
            },
        )
        for evidence_type, ledger_payload in ledger_payloads.items():
            _atomic_write(ledger_paths[evidence_type], ledger_payload)
        _atomic_write(baseline_path, {"payload": payload, "envelope": envelope})
        journal_payload = {**journal_payload, "status": "COMMITTED"}
        _atomic_write(
            journal_path,
            {
                "payload": journal_payload,
                "envelope": sign_envelope(journal_payload, _generation_root()),
            },
        )
        activation_payload = {
            "schema_version": "1.0",
            "status": "COMMITTED",
            "project_id": project_id,
            "baseline_id": baseline_id,
            "baseline_hash": baseline_hash,
            "journal_hash": _object_hash(journal_payload),
            "source_hashes": source_hashes,
            "operation_id": operation_id,
            "committed_at": _now(),
        }
        _atomic_write(
            activation_path,
            {
                "payload": activation_payload,
                "envelope": sign_envelope(activation_payload, _generation_root()),
            },
        )
        try:
            verified = load_historical_baseline(project_id, state_root=state_dir)
        except HistoricalEvidenceError as exc:
            raise RuntimeError("prospective baseline post-commit verification failed") from exc
        if verified is None or verified.baseline_id != baseline_id:
            raise RuntimeError("prospective baseline verification failed")
        return {
            "status": "COMMITTED",
            "project_id": project_id,
            "baseline_id": baseline_id,
            "operation_id": operation_id,
            "target_sha": observed_sha,
            "coverage_start": now,
            "baseline_generation": 1,
            "authority_generation": generation,
            "pre_baseline": "UNKNOWN",
            "post_baseline": "AUTHORITATIVE",
        }


def renew_prospective_evidence(project_id: str, operation_id: str) -> dict[str, object]:
    """Inspect append-only prospective ledgers and sign their current observation."""
    if not _PROJECT_ID.fullmatch(project_id) or not operation_id.startswith(
        "historical-renewal-"
    ):
        raise RuntimeError("historical renewal identity is invalid")
    state_dir = _migration_state_dir()
    with project_lock(state_dir, project_id, "historical-evidence-renew"):
        project = ProjectRegistry(state_dir).verify_read_only(project_id)
        if project.status.value != "ACTIVE":
            raise RuntimeError("project registration is not ACTIVE")
        authority = resolve_authority(project_id)
        if authority.constitution is None or authority.policy is None or authority.snapshot is None:
            raise RuntimeError("current project authority is unavailable")
        baseline = load_historical_baseline(project_id, state_root=state_dir)
        if baseline is None or not baseline.authoritative:
            raise RuntimeError("an authoritative prospective baseline is required")
        observed_sha = subprocess.run(
            ["git", "-C", project.repository_root, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, shell=False,
        ).stdout.strip()
        if observed_sha != baseline.target_sha or project.current_head_sha != observed_sha:
            raise RuntimeError("prospective evidence target SHA is stale")
        current = {}
        for evidence_type in ("rollback", "incident"):
            try:
                current[evidence_type] = load_historical_evidence(
                    project_id, evidence_type, state_root=state_dir, max_age_seconds=10**9
                )
            except HistoricalEvidenceError:
                # An append-only ledger may have advanced since the last signed
                # observation. Preserve the signed predecessor, but do not use
                # its stale source snapshot as current evidence.
                path = state_dir / "historical-evidence" / project_id / f"{evidence_type}.json"
                try:
                    document = _read_object(path)
                    verify_envelope(document["payload"], document["envelope"])
                    current[evidence_type] = _parse_historical_evidence(
                        document["payload"], evidence_type, expected_project_id=project_id
                    )
                    _verify_committed_renewal_state(
                        state_dir, project_id, evidence_type, current[evidence_type]
                    )
                except (
                    OSError,
                    KeyError,
                    TypeError,
                    ValueError,
                    HistoricalEvidenceError,
                ) as fallback_exc:
                    raise RuntimeError("existing prospective evidence is invalid") from fallback_exc
        existing_generations = {
            item.evidence_generation for item in current.values() if item is not None
        }
        present = [item is not None for item in current.values()]
        if any(present) and not all(present):
            raise RuntimeError("rollback and incident evidence state is asymmetric")
        if len(existing_generations) > 1:
            raise RuntimeError("rollback and incident evidence generations disagree")
        if current and all(item is not None and item.renewal_operation_id == operation_id
                            for item in current.values()):
            return {"status": "ALREADY_COMMITTED", "project_id": project_id,
                    "operation_id": operation_id,
                    "evidence_generation": next(iter(existing_generations), 1),
                    "baseline_id": baseline.baseline_id}
        generation = next(iter(existing_generations), 0) + 1
        predecessors = {
            key: (value.evidence_hash if value is not None else None)
            for key, value in current.items()
        }
        now = _now()
        coverage_start = (
            next(iter(current.values())).coverage_end
            if current and all(value is not None for value in current.values())
            else baseline.coverage_start
        )
        if datetime.fromisoformat(now.replace("Z", "+00:00")) <= datetime.fromisoformat(
            coverage_start.replace("Z", "+00:00")
        ):
            raise RuntimeError("prospective evidence interval has not advanced")
        directory = state_dir / "historical-evidence" / project_id
        journal_path = directory / "renewal-journal.json"
        activation_path = directory / "evidence-activation.json"
        ledger_directory = directory / "ledgers"
        history_dir = directory / "history"
        if any(
            path.is_symlink()
            for path in (directory, ledger_directory, history_dir, journal_path, activation_path)
        ):
            raise RuntimeError("historical evidence namespace must not use symlinks")
        evidence_payloads = {}
        source_hashes = {}
        for evidence_type in ("rollback", "incident"):
            source_path = directory / "ledgers" / f"{evidence_type}-ledger.json"
            if source_path.is_symlink() or not source_path.is_file():
                raise RuntimeError("authoritative prospective ledger is unavailable")
            ledger = _read_object(source_path)
            if (
                ledger.get("project_id") != project_id
                or ledger.get("baseline_id") != baseline.baseline_id
                or ledger.get("evidence_type") != evidence_type
                or ledger.get("coverage_before_baseline") != "UNKNOWN"
                or ledger.get("coverage_status_from_baseline") != "AUTHORITATIVE"
                or not isinstance(ledger.get("records"), list)
            ):
                raise RuntimeError("authoritative prospective ledger binding is invalid")
            records = ledger["records"]
            for record in records:
                if not isinstance(record, dict) or not any(
                    isinstance(record.get(key), str) and record[key]
                    for key in ("event_id", "record_id", "id")
                ):
                    raise RuntimeError("qualifying ledger record lacks an event identity")
            source_ref = f"ledger:{project_id}:ledgers/{evidence_type}-ledger.json"
            source_hash = _object_hash(ledger)
            source_hashes[evidence_type] = source_hash
            count = len(records)
            evidence_payloads[evidence_type] = {
                "schema_version": "1.0", "project_id": project_id,
                "evidence_type": evidence_type,
                "status": EvidenceStatus.VERIFIED_ZERO.value if count == 0
                else EvidenceStatus.VERIFIED_EVENTS.value,
                "count": count, "baseline_id": baseline.baseline_id,
                "coverage_before_baseline": "UNKNOWN", "coverage_start": coverage_start,
                "coverage_end": now, "evidence_generation": generation,
                "predecessor_evidence_hash": predecessors[evidence_type],
                "renewal_operation_id": operation_id,
                "definition_version": "agf-historical-events-v1",
                "source_refs": [source_ref], "source_hashes": [source_hash],
                "policy_hash": authority.policy.policy_hash,
                "constitution_id": authority.constitution.constitution_id,
                "authority_generation": int(authority.snapshot["generation"]),
                "generated_at": now,
                "provenance": "external-owner-controller:post-baseline;pre-baseline=UNKNOWN",
                "coverage_complete": True,
                "completeness_basis": "owner-completeness-v1:prospective-ledger-inspection",
            }
            payload = evidence_payloads[evidence_type]
            payload["evidence_hash"] = _object_hash(payload)
        journal_payload = {
            "status": "PREPARED", "operation_id": operation_id, "project_id": project_id,
            "baseline_id": baseline.baseline_id, "evidence_type": "rollback+incident",
            "evidence_generation": generation,
            "predecessor_evidence_hashes": predecessors,
            "evidence_hashes": {
                key: value["evidence_hash"] for key, value in evidence_payloads.items()
            },
            "source_hashes": source_hashes, "policy_hash": authority.policy.policy_hash,
            "constitution_id": authority.constitution.constitution_id,
            "authority_generation": int(authority.snapshot["generation"]),
            "coverage_start": coverage_start, "coverage_end": now,
        }
        _atomic_write(
            journal_path,
            {
                "payload": journal_payload,
                "envelope": sign_envelope(journal_payload, _generation_root()),
            },
        )
        for evidence_type, previous in current.items():
            if previous is not None:
                old_path = directory / f"{evidence_type}.json"
                history_path = history_dir / f"{evidence_type}-{previous.evidence_hash}.json"
                if history_path.exists() or history_path.is_symlink():
                    raise RuntimeError("historical evidence predecessor already exists")
                _atomic_write(history_path, _read_object(old_path))
        for evidence_type, payload in evidence_payloads.items():
            _atomic_write(
                directory / f"{evidence_type}.json",
                {
                    "payload": payload,
                    "envelope": sign_envelope(payload, _generation_root()),
                },
            )
        journal_payload = {**journal_payload, "status": "COMMITTED"}
        _atomic_write(
            journal_path,
            {
                "payload": journal_payload,
                "envelope": sign_envelope(journal_payload, _generation_root()),
            },
        )
        activation_payload = {
            "status": "COMMITTED", "project_id": project_id,
            "baseline_id": baseline.baseline_id, "operation_id": operation_id,
            "evidence_generation": generation,
            "evidence_hashes": journal_payload["evidence_hashes"],
            "source_hashes": source_hashes, "committed_at": _now(),
        }
        _atomic_write(
            activation_path,
            {
                "payload": activation_payload,
                "envelope": sign_envelope(activation_payload, _generation_root()),
            },
        )
        for evidence_type in ("rollback", "incident"):
            verified = load_historical_evidence(project_id, evidence_type, state_root=state_dir)
            if verified is None:
                raise RuntimeError("prospective evidence post-commit verification failed")
        return {"status": "COMMITTED", "project_id": project_id,
                "operation_id": operation_id, "baseline_id": baseline.baseline_id,
                "evidence_generation": generation,
                "rollback": evidence_payloads["rollback"]["status"],
                "incident": evidence_payloads["incident"]["status"]}


def _require_fresh_profile_observations(previous, proposed) -> None:
    old_observations = {
        item.profile.profile_id: item.profile.observed_at for item in previous.candidates
    }
    new_observations = {
        item.profile.profile_id: item.profile.observed_at for item in proposed.candidates
    }
    if set(old_observations) != set(new_observations) or any(
        datetime.fromisoformat(new_observations[key].replace("Z", "+00:00"))
        <= datetime.fromisoformat(old_observations[key].replace("Z", "+00:00"))
        for key in old_observations
    ):
        raise RuntimeError("provider renewal requires fresh profile observations")


def _verify_committed_renewal_state(
    state_dir: Path, project_id: str, evidence_type: str, evidence: object
) -> None:
    """Permit stale-source predecessor use only after committed-state proof."""
    directory = state_dir / "historical-evidence" / project_id
    activation_path = directory / "evidence-activation.json"
    journal_path = directory / "renewal-journal.json"
    history_dir = directory / "history"
    evidence_paths = tuple(directory / f"{kind}.json" for kind in ("rollback", "incident"))
    if any(
        path.is_symlink()
        for path in (directory, activation_path, journal_path, history_dir, *evidence_paths)
    ):
        raise RuntimeError("historical evidence namespace must not use symlinks")
    activation = _read_object(activation_path)
    verify_envelope(activation["payload"], activation["envelope"])
    activation_payload = activation["payload"]
    verify_current_bindings(evidence, expected_project_id=project_id)
    if (
        activation_payload.get("status") != "COMMITTED"
        or activation_payload.get("project_id") != project_id
        or activation_payload.get("baseline_id") != evidence.baseline_id
        or activation_payload.get("operation_id") != evidence.renewal_operation_id
        or activation_payload.get("evidence_generation") != evidence.evidence_generation
        or activation_payload.get("evidence_hashes", {}).get(evidence_type)
        != evidence.evidence_hash
        or activation_payload.get("source_hashes", {}).get(evidence_type)
        != evidence.source_hashes[0]
    ):
        raise RuntimeError("incomplete prospective evidence activation")
    journal = _read_object(journal_path)
    verify_envelope(journal["payload"], journal["envelope"])
    journal_payload = journal["payload"]
    if (
        journal_payload.get("status") != "COMMITTED"
        or journal_payload.get("project_id") != project_id
        or journal_payload.get("baseline_id") != evidence.baseline_id
        or journal_payload.get("operation_id") != evidence.renewal_operation_id
        or journal_payload.get("evidence_generation") != evidence.evidence_generation
        or journal_payload.get("evidence_hashes", {}).get(evidence_type)
        != evidence.evidence_hash
        or journal_payload.get("source_hashes", {}).get(evidence_type)
        != evidence.source_hashes[0]
        or journal_payload.get("policy_hash") != evidence.policy_hash
        or journal_payload.get("constitution_id") != evidence.constitution_id
        or journal_payload.get("authority_generation") != evidence.authority_generation
        or journal_payload.get("predecessor_evidence_hashes", {}).get(evidence_type)
        != evidence.predecessor_evidence_hash
    ):
        raise RuntimeError("incomplete prospective evidence journal")


def _migration_state_dir() -> Path:
    return Path.home() / ".agf-orchestrator"


def _object_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _write_object(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_write(path, value)


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"authority artifact is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("authority artifact must be an object")
    return value


def _monotonic_floor(store: AuthorityGenerationStore, project_id: str) -> int:
    store._recover_metadata(project_id)
    path = store._floor_path(project_id)
    if not path.exists():
        return 0
    value = _read_object(path)
    if (
        value.get("schema_version") != "1.0"
        or value.get("project_id") != project_id
        or not isinstance(value.get("generation_number"), int)
        or value["generation_number"] < 0
    ):
        raise RuntimeError("authority generation floor is invalid")
    return value["generation_number"]


def _generation_root() -> Path:
    root = Path.home() / ".agf-owner-root"
    anchor, _ = load_pinned_anchor()
    if anchor["fingerprint"] != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("pinned owner fingerprint changed")
    return root


def _verified_legacy_transition(project_id: str, policy, constitution, generation: int) -> dict:
    transition_path = Path.home() / ".agf-owner-root" / "transition.json"
    transition = _read_object(transition_path)
    signature = transition.pop("legacy_signature", None)
    if not isinstance(signature, str):
        raise RuntimeError("legacy authority transition signature is missing")
    expected = hmac.new(
        _legacy_key(_migration_state_dir()), canonical_json(transition), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError("legacy authority transition signature is invalid")
    if (
        transition.get("project_id") != project_id
        or transition.get("policy_id") != policy.policy_id
        or transition.get("policy_hash") != policy.policy_hash
        or transition.get("constitution_id") != constitution.constitution_id
        or transition.get("constitution_record_hash") != constitution.record_hash
        or transition.get("policy_generation") != generation
        or transition.get("new_authority", {}).get("fingerprint") != PINNED_OWNER_FINGERPRINT
    ):
        raise RuntimeError("legacy authority transition binding is invalid")
    return {**transition, "legacy_signature": signature}


def _legacy_components(project_id: str) -> tuple[dict[str, dict], int]:
    state_dir = _migration_state_dir()
    project = ProjectRegistry().get(project_id)
    constitution = ConstitutionAuthority().resolve(project_id)
    policy = PolicyAuthority().resolve(project_id)
    snapshot = PolicyStateStore(state_dir, read_only=True).snapshot(project_id)
    if snapshot is None or not isinstance(snapshot.get("activation"), dict):
        raise RuntimeError("current HMAC policy state is unavailable")
    current_generation = int(snapshot.get("generation", 0))
    if current_generation < 1:
        raise RuntimeError("current HMAC policy generation is invalid")
    constitution_record = _read_object(
        state_dir
        / "projects"
        / project_id
        / "constitution"
        / f"{constitution.constitution_id}.json"
    )
    if (
        _object_hash(constitution_record) != constitution.record_hash
        or constitution_record.get("project_id") != project_id
        or constitution_record.get("constitution_id") != constitution.constitution_id
    ):
        raise RuntimeError("legacy Constitution artifact is not the verified record")
    if (
        _object_hash(snapshot["policy"]) != policy.policy_hash
        or snapshot.get("active_policy_id") != policy.policy_id
        or snapshot.get("active_policy_hash") != policy.policy_hash
        or _object_hash(snapshot["activation"]) != policy.activation_hash
        or snapshot["activation"].get("project_id") != project_id
        or snapshot["activation"].get("policy_id") != policy.policy_id
        or snapshot["activation"].get("policy_hash") != policy.policy_hash
    ):
        raise RuntimeError("legacy policy artifacts are not the verified active state")
    _verified_legacy_transition(project_id, policy, constitution, current_generation)
    kill_switch = PolicyStateStore(state_dir, read_only=True).authority_snapshot(project_id)
    return {
        "constitution": constitution_record,
        "policy": snapshot["policy"],
        "activation": snapshot["activation"],
        "rollback": {
            "project_id": project_id,
            "status": "PINNED_BASELINE",
            "rollback_target": policy.rollback_target,
            "policy_id": policy.policy_id,
            "policy_hash": policy.policy_hash,
            "generation": current_generation,
            "kill_switch": kill_switch,
        },
        "registration": project.to_dict(),
        "provider_intelligence": {
            "project_id": project_id,
            "status": "PENDING_PROVIDER_INTELLIGENCE",
            "policy_generation": current_generation,
        },
    }, current_generation


def _build_prepared_generation(project_id: str, generation_id: str, operation_id: str):
    state_dir = _migration_state_dir()
    values, current_generation = _legacy_components(project_id)
    components = []
    names = (
        "constitution",
        "policy",
        "activation",
        "rollback",
        "registration",
        "provider_intelligence",
    )
    for name in names:
        value = values[name]
        relative = Path("authority-generations") / project_id / generation_id / f"{name}.json"
        _write_object(state_dir / relative, value)
        artifact_hash = _object_hash(value)
        components.append(
            AuthorityComponent(
                name=name,
                generation_id=generation_id,
                artifact_hash=artifact_hash,
                scheme="Ed25519",
                project_id=project_id,
                semantic_hash=artifact_hash,
                artifact_path=str(relative),
                artifact_signature=sign_envelope(value, _generation_root()),
            )
        )
    transition = _verified_legacy_transition(
        project_id,
        PolicyAuthority().resolve(project_id),
        ConstitutionAuthority().resolve(project_id),
        current_generation,
    )
    generation = build_generation(
        generation_id=generation_id,
        project_id=project_id,
        scheme="Ed25519",
        owner_key_id="owner-ed25519-1",
        owner_fingerprint=PINNED_OWNER_FINGERPRINT,
        constitution_id=values["constitution"]["constitution_id"],
        constitution_hash=_object_hash(values["constitution"]),
        policy_hash=_object_hash(values["policy"]),
        operation_id=operation_id,
        status=GenerationStatus.VERIFIED,
        components=tuple(components),
        predecessor_id=f"legacy-hmac-generation-{current_generation}",
        predecessor_hash=_object_hash(transition),
    )
    return replace(generation, signature=sign_envelope(generation._unsigned(), _generation_root()))


def prepare_ed25519_generation(project_id: str, operation_id: str) -> dict[str, object]:
    state_dir = _migration_state_dir()
    project = ProjectRegistry().get(project_id)
    if project.status.value != "ACTIVE":
        raise RuntimeError("project registration is not ACTIVE")
    policy = PolicyAuthority().resolve(project_id)
    if policy.policy_id != "merge-policy-adr-0003":
        raise RuntimeError("unexpected current policy")
    snapshot = PolicyStateStore(state_dir, read_only=True).snapshot(project_id)
    current_number = int(snapshot.get("generation", 0) if snapshot else 0)
    store = AuthorityGenerationStore(state_dir)
    for candidate_path in store._directory(project_id).glob("generation-*.json"):
        candidate = store.load(project_id, candidate_path.stem)
        if candidate.operation_id != operation_id:
            continue
        if candidate.status is GenerationStatus.ACTIVE:
            return {
                "status": "ALREADY_ACTIVE",
                "project_id": project_id,
                "generation_id": candidate.generation_id,
                "generation_number": candidate.generation_number,
                "manifest_hash": candidate.manifest_hash,
                "operation_id": operation_id,
            }
        if candidate.status is not GenerationStatus.SUPERSEDED:
            return {
                "status": "PREPARED",
                "project_id": project_id,
                "generation_id": candidate.generation_id,
                "generation_number": candidate.generation_number,
                "manifest_hash": candidate.manifest_hash,
                "operation_id": operation_id,
            }
        raise RuntimeError("authority operation identity has already been consumed")
    current_number = max(current_number, _monotonic_floor(store, project_id))
    generation_id = f"generation-{current_number + 1}"
    existing_path = store._generation_path(project_id, generation_id)
    if existing_path.exists():
        raise RuntimeError("next authority generation is already prepared")
    generation = _build_prepared_generation(project_id, generation_id, operation_id)
    store._save_prepared_owner_controlled(generation)
    return {
        "status": "PREPARED",
        "project_id": project_id,
        "generation_id": generation_id,
        "generation_number": generation.generation_number,
        "manifest_hash": generation.manifest_hash,
        "operation_id": operation_id,
    }


def _verify_prepared_generation(project_id: str, generation_id: str):
    state_dir = _migration_state_dir()
    store = AuthorityGenerationStore(state_dir)
    generation = store.load(project_id, generation_id)
    if generation.scheme != "Ed25519" or generation.owner_fingerprint != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("prepared generation owner binding is invalid")
    artifacts = AuthorityContext._verify_artifacts(
        generation, artifact_root=state_dir, artifacts=None
    )
    context = AuthorityContext(
        project_id=project_id,
        generation_id=generation.generation_id,
        generation_number=generation.generation_number,
        scheme=generation.scheme,
        manifest_hash=generation.manifest_hash,
        constitution_hash=generation.constitution_hash,
        policy_hash=generation.policy_hash,
        components={item.name: item.to_dict() for item in generation.components},
        artifacts=artifacts,
    )
    if context.generation_id != generation_id or context.scheme != "Ed25519":
        raise RuntimeError("prepared generation context is inconsistent")
    return generation, context


def verify_ed25519_generation(project_id: str, generation_id: str) -> dict[str, object]:
    generation, _ = _verify_prepared_generation(project_id, generation_id)
    directory = _migration_state_dir() / "authority-generations" / project_id
    selector_path = directory / "active.json"
    floor_path = directory / "generation-floor.json"
    selector = _read_object(selector_path) if selector_path.exists() else None
    floor = _read_object(floor_path) if floor_path.exists() else {"generation_number": 0}
    readiness = {
        "schema_version": "1.0", "project_id": project_id, "generation_id": generation_id,
        "generation_number": generation.generation_number,
        "manifest_hash": generation.manifest_hash,
        "component_hashes": {item.name: item.artifact_hash for item in generation.components},
        "predecessor_id": generation.predecessor_id,
        "predecessor_hash": generation.predecessor_hash,
        "current_selector": selector, "current_floor": floor.get("generation_number", 0),
        "owner_fingerprint": PINNED_OWNER_FINGERPRINT, "operation_id": generation.operation_id,
        "verified_at": _now(), "verification": "PASS",
    }
    _write_object(
        directory / generation_id / "readiness.json",
        {**readiness, "signature": sign_envelope(readiness, _generation_root())},
    )
    return {"status": "GENERATION_READY_FOR_CUTOVER", **readiness}


def cutover_ed25519_generation(project_id: str, generation_id: str) -> dict[str, object]:
    state_dir = _migration_state_dir()
    AuthorityGenerationStore._validate_project_id(project_id)
    AuthorityGenerationStore._validate_generation_id(generation_id)
    directory = state_dir / "authority-generations" / project_id / generation_id
    readiness_record = _read_object(directory / "readiness.json")
    signature = readiness_record.pop("signature", None)
    if not isinstance(signature, dict):
        raise RuntimeError("generation readiness signature is missing")
    from agf_orchestrator.owner_authority import verify_envelope

    verify_envelope(readiness_record, signature)
    generation, _ = _verify_prepared_generation(project_id, generation_id)
    expected_components = {item.name: item.artifact_hash for item in generation.components}
    if (
        readiness_record.get("project_id") != project_id
        or readiness_record.get("generation_id") != generation.generation_id
        or readiness_record.get("generation_number") != generation.generation_number
        or readiness_record.get("manifest_hash") != generation.manifest_hash
        or readiness_record.get("component_hashes") != expected_components
        or readiness_record.get("predecessor_id") != generation.predecessor_id
        or readiness_record.get("predecessor_hash") != generation.predecessor_hash
        or readiness_record.get("operation_id") != generation.operation_id
        or readiness_record.get("owner_fingerprint") != PINNED_OWNER_FINGERPRINT
    ):
        raise RuntimeError("generation readiness is not bound to prepared generation")
    authority_dir = state_dir / "authority-generations" / project_id
    selector_path = authority_dir / "active.json"
    floor_path = authority_dir / "generation-floor.json"
    selector = _read_object(selector_path) if selector_path.exists() else None
    floor = _read_object(floor_path) if floor_path.exists() else {"generation_number": 0}
    if selector and selector.get("generation_id") == generation_id:
        return {"status": "ALREADY_ACTIVE", "generation_id": generation_id}
    if (
        selector != readiness_record["current_selector"]
        or floor.get("generation_number", 0) != readiness_record["current_floor"]
    ):
        raise RuntimeError("generation readiness is stale")
    active_unsigned = build_generation(
        **{
            **generation.__dict__,
            "status": GenerationStatus.ACTIVE,
            "manifest_hash": "0" * 64,
            "signature": None,
        }
    )
    active_signature = sign_envelope(active_unsigned._unsigned(), _generation_root())
    AuthorityGenerationStore(state_dir)._activate_owner_controlled(
        project_id, generation_id, active_signature=active_signature
    )
    active, context = _verify_prepared_generation(project_id, generation_id)
    if active.status is not GenerationStatus.ACTIVE or context.scheme != "Ed25519":
        raise RuntimeError("post-cutover authority verification failed")
    return {"status": "CUTOVER_COMPLETE", "generation_id": generation_id,
            "generation_number": active.generation_number, "manifest_hash": active.manifest_hash,
            "scheme": context.scheme}


def prepare_root(project_id: str, operation_id: str, root: Path) -> dict[str, str]:
    constitution = ConstitutionAuthority().resolve(project_id)
    policy = PolicyAuthority().resolve(project_id)
    state_dir = Path.home() / ".agf-orchestrator"
    legacy_key = _legacy_key(state_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    private_path = root / "owner-private.key"
    public_path = root / "owner-public.key"
    anchor_path = root / "anchor.json"
    transition_path = root / "transition.json"
    if not private_path.exists():
        raise RuntimeError("owner Ed25519 root must be provisioned by the owner")
    private = _private_key(root)
    public_bytes = private.public_key().public_bytes_raw()
    public_fingerprint = hashlib.sha256(public_bytes).hexdigest()
    if public_fingerprint != PINNED_OWNER_FINGERPRINT:
        raise RuntimeError("pre-provisioned owner root does not match pinned authority")
    key_id = "owner-ed25519-1"
    if public_path.exists() or anchor_path.exists() or transition_path.exists():
        raise RuntimeError("owner Ed25519 root metadata already exists")
    public_path.write_text(base64.b64encode(public_bytes).decode() + "\n")
    os.chmod(public_path, 0o644)
    anchor = {
        "schema_version": "1.0",
        "signature_scheme": "Ed25519",
        "key_id": key_id,
        "fingerprint": public_fingerprint,
    }
    anchor_path.write_text(json.dumps(anchor, sort_keys=True) + "\n")
    os.chmod(anchor_path, 0o644)
    unsigned = {
        "schema_version": "1.0",
        "operation_id": operation_id,
        "previous_authority": {"scheme": "HMAC-SHA256", "key_id": "owner-key-1"},
        "new_authority": {
            "scheme": "Ed25519",
            "key_id": key_id,
            "fingerprint": public_fingerprint,
        },
        "project_id": project_id,
        "constitution_id": constitution.constitution_id,
        "constitution_record_hash": constitution.record_hash,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "policy_generation": 3,
        "created_at": _now(),
    }
    transition = {
        **unsigned,
        "legacy_signature": hmac.new(
            legacy_key, canonical_json(unsigned), hashlib.sha256
        ).hexdigest(),
    }
    transition_path.write_text(json.dumps(transition, sort_keys=True) + "\n")
    os.chmod(transition_path, 0o600)
    return {"operation_id": operation_id, "key_id": key_id, "fingerprint": public_fingerprint}


def main() -> int:
    parser = argparse.ArgumentParser(description="external owner Ed25519 authority controller")
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--prepare-generation", action="store_true")
    parser.add_argument("--verify-generation")
    parser.add_argument("--cutover-generation")
    parser.add_argument("--renew-provider-candidate")
    parser.add_argument("--create-prospective-baseline", action="store_true")
    parser.add_argument("--renew-prospective-evidence", action="store_true")
    parser.add_argument("--target-sha")
    args = parser.parse_args()
    if args.prepare_generation:
        result = prepare_ed25519_generation(args.project, args.operation_id)
    elif args.verify_generation:
        result = verify_ed25519_generation(args.project, args.verify_generation)
    elif args.cutover_generation:
        result = cutover_ed25519_generation(args.project, args.cutover_generation)
    elif args.renew_provider_candidate:
        result = renew_provider_candidate(args.project, Path(args.renew_provider_candidate))
    elif args.create_prospective_baseline:
        result = create_prospective_baseline(
            args.project, args.operation_id, target_sha=args.target_sha
        )
    elif args.renew_prospective_evidence:
        result = renew_prospective_evidence(args.project, args.operation_id)
    else:
        result = prepare_root(args.project, args.operation_id, Path.home() / ".agf-owner-root")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
