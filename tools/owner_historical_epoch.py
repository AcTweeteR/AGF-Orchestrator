"""Independent Owner-only historical epoch rollover.

A new prospective baseline starts TODAY. All unobserved time before its start
remains UNKNOWN. No event ledger is reconstructed, edited or backdated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agf_orchestrator.historical_epochs import (
    _assert_archive,
    signed_hash,
)
from agf_orchestrator.historical_evidence import load_historical_baseline
from agf_orchestrator.locking import project_lock
from agf_orchestrator.owner_authority import (
    PINNED_OWNER_FINGERPRINT,
    canonical_bytes,
    verify_envelope,
)
from agf_orchestrator.project_registry import ProjectRegistry


def rollover_prospective_baseline(
    project_id: str, operation_id: str,
) -> dict[str, object]:
    """Owner-sign a NEW prospective epoch without changing an old signed fact."""
    from tools import owner_ed25519_authority as owner

    if (
        not re.fullmatch(r"project-[0-9a-f]{16}", project_id)
        or not re.fullmatch(r"historical-baseline-epoch-[a-zA-Z0-9-]{8,100}", operation_id)
    ):
        raise RuntimeError("historical epoch operation identity is invalid")
    state = owner._migration_state_dir()
    with project_lock(state, project_id, "owner-historical-epoch-rollover"):
        project = ProjectRegistry(state).verify_read_only(project_id)
        if project.status.value != "ACTIVE" or project.default_branch != "main":
            raise RuntimeError("historical epoch needs an ACTIVE canonical main project")
        authority = owner.resolve_authority(project_id)
        if any(
            getattr(authority, name, None) is None
            for name in ("constitution", "policy", "snapshot")
        ):
            raise RuntimeError("historical epoch Owner policy is unavailable")
        root = Path(project.repository_root)
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        head = result.stdout.strip()
        if head != project.current_head_sha:
            raise RuntimeError("historical epoch target binding differs")
        branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if branch != "main":
            raise RuntimeError("historical epoch must start on canonical main")
        directory = state / "historical-evidence" / project_id
        baseline_path = directory / "baseline.json"
        if directory.is_symlink() or baseline_path.is_symlink():
            raise RuntimeError("historical epoch active namespace has symlinks")
        if not baseline_path.is_file():
            raise RuntimeError("historical predecessor baseline is absent")
        try:
            document = json.loads(baseline_path.read_text(encoding="utf-8"))
            prior = document["payload"]
            verify_envelope(prior, document["envelope"])
        except (ValueError, KeyError, OSError, TypeError) as exc:
            raise RuntimeError("historical predecessor signature is invalid") from exc
        old_generation = prior.get("baseline_generation")
        old_sha = prior.get("target_sha")
        if (
            prior.get("project_id") != project_id
            or prior.get("target_identity") != project.origin_url
            or prior.get("owner_fingerprint") != PINNED_OWNER_FINGERPRINT
            or type(old_generation) is not int
            or not 1 <= old_generation < 64
            or not isinstance(old_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", old_sha)
        ):
            raise RuntimeError("historical predecessor bindings are invalid")
        ancestor = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", old_sha, head],
            capture_output=True, timeout=12,
        )
        if ancestor.returncode != 0:
            raise RuntimeError("historical predecessor SHA is not in canonical history")
        now = owner._now()
        if datetime.fromisoformat(now.replace("Z", "+00:00")) <= (
            datetime.fromisoformat(prior["coverage_start"].replace("Z", "+00:00"))
        ):
            raise RuntimeError("historical epoch timestamp did not advance")
        if (
            old_sha == head and
            (datetime.now(UTC) - datetime.fromisoformat(
                prior["generated_at"].replace("Z", "+00:00")
            )).total_seconds() < 86400
        ):
            raise RuntimeError("current historical epoch is still fresh; no reset")
        status_path = state / "campaign-daemon" / "status.json"
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("campaigns_active") != 0 or status.get("campaigns_waiting") != 0:
                raise RuntimeError("cannot rotate risk evidence during an active campaign")
        prior_hash = signed_hash(prior)
        archive_base = state / "historical-evidence-archives" / project_id
        archive = archive_base / prior_hash
        stage = directory.parent / f".{project_id}-epoch-{old_generation + 1}-stage"
        if (
            archive_base.is_symlink() or archive.is_symlink() or stage.is_symlink()
            or archive.exists() or stage.exists()
        ):
            raise RuntimeError("historical epoch staging or archive is already present")
        if any(path.is_symlink() for path in directory.rglob("*")):
            raise RuntimeError("historical epoch cannot archive symlinked files")
        generation = int(authority.snapshot["generation"])
        epoch_id = "baseline-" + hashlib.sha256(
            canonical_bytes({
                "project_id": project_id, "target_sha": head,
                "operation_id": operation_id,
            }),
        ).hexdigest()[:16]
        source_set = [
            f"ledger:{project_id}:ledgers/rollback-ledger.json",
            f"ledger:{project_id}:ledgers/incident-ledger.json",
        ]
        ledgers = {}
        for kind in ("rollback", "incident"):
            ledgers[kind] = {
                "schema_version": "1.0", "project_id": project_id,
                "baseline_id": epoch_id, "evidence_type": kind,
                "coverage_before_baseline": "UNKNOWN", "coverage_start": now,
                "coverage_status_from_baseline": "AUTHORITATIVE",
                "qualifying_event_definition": "agf-historical-events-v1",
                "records": [], "provenance": "external-owner-controller:prospective-baseline",
            }
        source_hashes = [signed_hash(ledgers[k]) for k in ("rollback", "incident")]
        payload = {
            "schema_version": "1.0", "baseline_id": epoch_id, "project_id": project_id,
            "target_identity": project.origin_url, "target_sha": head,
            "coverage_start": now,
            "policy_id": authority.policy.policy_id,
            "policy_hash": authority.policy.policy_hash,
            "policy_generation": generation,
            "constitution_id": authority.constitution.constitution_id,
            "constitution_record_hash": authority.constitution.record_hash,
            "authority_generation": generation,
            "owner_fingerprint": PINNED_OWNER_FINGERPRINT,
            "evidence_definition_version": "agf-historical-events-v1",
            "source_set": source_set, "source_hashes": source_hashes,
            "baseline_generation": old_generation + 1,
            "predecessor_baseline_hash": prior_hash,
            "operation_id": operation_id, "generated_at": now,
            "provenance": (
                "external-owner-controller:prospective-baseline;"
                "pre-baseline=UNKNOWN;unobserved-gap=UNKNOWN"
            ),
        }
        journal = {
            "schema_version": "1.0", "status": "COMMITTED",
            "project_id": project_id, "baseline_id": epoch_id,
            "baseline_hash": signed_hash(payload),
            "baseline_generation": old_generation + 1,
            "operation_id": operation_id, "target_sha": head,
            "source_hashes": source_hashes, "created_at": now,
        }
        activation = {
            "schema_version": "1.0", "status": "COMMITTED",
            "project_id": project_id, "baseline_id": epoch_id,
            "baseline_hash": signed_hash(payload),
            "journal_hash": signed_hash(journal),
            "source_hashes": source_hashes, "operation_id": operation_id,
            "committed_at": owner._now(),
        }
        stage.mkdir(mode=0o700)
        try:
            for kind, record in (
                ("baseline", payload), ("baseline-journal", journal),
                ("baseline-activation", activation),
            ):
                owner._atomic_write(stage / (kind + ".json"), {
                    "payload": record,
                    "envelope": owner.sign_envelope(record, owner._generation_root()),
                })
            for kind, ledger in ledgers.items():
                owner._atomic_write(
                    stage / "ledgers" / f"{kind}-ledger.json", ledger
                )
            archive_base.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Old active directory is atomically relocated intact, never edited.
            os.rename(directory, archive)
            try:
                _assert_archive(
                    root=state, project_id=project_id,
                    archive_hash=prior_hash, successor=payload,
                )
                os.rename(stage, directory)
                verified = load_historical_baseline(project_id, state_root=state)
                if (
                    verified is None or verified.baseline_id != epoch_id
                    or verified.baseline_generation != old_generation + 1
                ):
                    raise RuntimeError("new historical baseline did not verify")
            except Exception:
                # Restore the original active epoch on any post-swap failure.
                if directory.exists():
                    os.rename(directory, stage)
                if not directory.exists():
                    os.rename(archive, directory)
                raise
        except Exception:
            # Persist a failed staged candidate for audit, not as active evidence.
            # The predecessor is always restored before returning failure.
            raise
        return {
            "status": "COMMITTED_NEW_PROSPECTIVE_EPOCH",
            "project_id": project_id, "baseline_id": epoch_id,
            "baseline_generation": old_generation + 1,
            "predecessor_baseline_hash": prior_hash,
            "coverage_start": now, "coverage_before_baseline": "UNKNOWN",
            "unobserved_gap": "UNKNOWN",
            "target_sha": head,
            "archived_predecessor": str(archive),
        }
