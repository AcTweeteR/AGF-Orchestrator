"""Verify signed, immutable past prospective-baseline epochs.

An epoch switch NEVER fills a gap in risk coverage. Old baselines remain signed
historical observations, but only the newest baseline can qualify deliveries.
This module is verification-only; signing and switching live outside the runtime.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .owner_authority import OwnerAuthorityError, verify_envelope

_HEX = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^project-[0-9a-f]{16}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TYPES = ("rollback", "incident")


class HistoricalEpochError(ValueError):
    """Historical epoch chain is absent, broken, or tampered with."""


def signed_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _load_signed(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise HistoricalEpochError("historical epoch signed record is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) != {"payload", "envelope"}:
            raise HistoricalEpochError("historical epoch signed record is malformed")
        payload = document["payload"]
        if not isinstance(payload, dict):
            raise HistoricalEpochError("historical epoch payload is malformed")
        verify_envelope(payload, document["envelope"])
        return payload
    except (OwnerAuthorityError, OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise HistoricalEpochError("historical epoch signature or record is invalid") from exc


def _assert_archive(
    *,
    root: Path, project_id: str, archive_hash: str, successor: dict[str, Any],
) -> dict[str, Any]:
    folder = root / "historical-evidence-archives" / project_id / archive_hash
    ancestor = folder.parent.parent
    if any(p.is_symlink() for p in (ancestor, folder.parent, folder, folder / "ledgers")):
        raise HistoricalEpochError("historical archive includes an unsafe symlink")
    prior = _load_signed(folder / "baseline.json")
    if signed_hash(prior) != archive_hash:
        raise HistoricalEpochError("historical epoch predecessor hash differs")
    previous_generation = successor["baseline_generation"] - 1
    if (
        prior.get("project_id") != project_id
        or prior.get("baseline_generation") != previous_generation
        or prior.get("target_identity") != successor.get("target_identity")
        or prior.get("owner_fingerprint") != successor.get("owner_fingerprint")
        or prior.get("evidence_definition_version") != successor.get("evidence_definition_version")
        or not _SHA.fullmatch(str(prior.get("target_sha", "")))
        or not isinstance(prior.get("source_set"), list)
        or len(prior["source_set"]) != len(_TYPES)
        or not isinstance(prior.get("source_hashes"), list)
        or len(prior["source_hashes"]) != len(_TYPES)
    ):
        raise HistoricalEpochError("historical epoch identity or generation differs")
    try:
        old_time = datetime.fromisoformat(prior["coverage_start"].replace("Z", "+00:00"))
        new_time = datetime.fromisoformat(successor["coverage_start"].replace("Z", "+00:00"))
        if old_time.tzinfo is None or new_time.tzinfo is None or old_time >= new_time:
            raise HistoricalEpochError("historical coverage intervals overlap or reverse")
    except (TypeError, KeyError, ValueError) as exc:
        raise HistoricalEpochError("historical epoch coverage timestamps are invalid") from exc
    journal = _load_signed(folder / "baseline-journal.json")
    activation = _load_signed(folder / "baseline-activation.json")
    if (
        journal.get("status") != "COMMITTED"
        or journal.get("project_id") != project_id
        or journal.get("baseline_id") != prior.get("baseline_id")
        or journal.get("operation_id") != prior.get("operation_id")
        or journal.get("target_sha") != prior.get("target_sha")
        or journal.get("baseline_hash") != archive_hash
        or journal.get("baseline_generation") != previous_generation
        or journal.get("source_hashes") != prior["source_hashes"]
        or activation.get("status") != "COMMITTED"
        or activation.get("project_id") != project_id
        or activation.get("baseline_id") != prior.get("baseline_id")
        or activation.get("operation_id") != prior.get("operation_id")
        or activation.get("baseline_hash") != archive_hash
        or activation.get("journal_hash") != signed_hash(journal)
        or activation.get("source_hashes") != prior["source_hashes"]
    ):
        raise HistoricalEpochError("historical epoch journal or activation is invalid")
    for kind, ref, expected_hash in zip(_TYPES, prior["source_set"], prior["source_hashes"]):
        if ref != f"ledger:{project_id}:ledgers/{kind}-ledger.json":
            raise HistoricalEpochError("historical epoch ledger reference is not bounded")
        file = folder / "ledgers" / f"{kind}-ledger.json"
        if file.is_symlink() or not file.is_file():
            raise HistoricalEpochError("historical epoch ledger is missing")
        try:
            content = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HistoricalEpochError("historical epoch ledger is invalid") from exc
        if (
            signed_hash(content) != expected_hash
            or content.get("project_id") != project_id
            or content.get("baseline_id") != prior.get("baseline_id")
            or content.get("evidence_type") != kind
            or content.get("coverage_before_baseline") != "UNKNOWN"
        ):
            raise HistoricalEpochError("historical epoch ledger hash or binding differs")
    return prior


def verify_archived_epoch_chain(
    state_root: str | Path, project_id: str, current: dict[str, Any],
) -> None:
    """Verify all historical predecessors without asserting gap coverage.

    Exact archive paths are derived only from signed hashes. The historical
    records may bind older target SHAs, so their old policy freshness is not
    reinterpreted as a current authorization.
    """
    if not _PROJECT.fullmatch(project_id):
        raise HistoricalEpochError("historical epoch project ID is invalid")
    if current.get("project_id") != project_id:
        raise HistoricalEpochError("historical epoch current project differs")
    generation = current.get("baseline_generation")
    if type(generation) is not int or not 1 <= generation <= 64:
        raise HistoricalEpochError("historical epoch generation is invalid")
    if generation == 1:
        if current.get("predecessor_baseline_hash") is not None:
            raise HistoricalEpochError("first epoch cannot have a predecessor")
        return
    root = Path(state_root).expanduser().resolve()
    record = current
    while record["baseline_generation"] > 1:
        predecessor_hash = record.get("predecessor_baseline_hash")
        if not isinstance(predecessor_hash, str) or not _HEX.fullmatch(predecessor_hash):
            raise HistoricalEpochError("historical predecessor reference is invalid")
        record = _assert_archive(
            root=root, project_id=project_id,
            archive_hash=predecessor_hash, successor=record,
        )
    if record.get("predecessor_baseline_hash") is not None:
        raise HistoricalEpochError("historical epoch chain does not end at the first baseline")
