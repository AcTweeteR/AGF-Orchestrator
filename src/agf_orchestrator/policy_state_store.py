"""Transactional external policy state shared by owner controller and verifier."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator


class PolicyStateError(RuntimeError):
    """Raised when transactional policy state is missing or inconsistent."""


@dataclass(frozen=True)
class KillSwitchSnapshot:
    """Verified snapshot of the sole transactional kill-switch authority."""

    active: bool
    generation: int
    event_id: str
    changed_at: str
    reason: str

    @classmethod
    def disabled(cls) -> "KillSwitchSnapshot":
        return cls(False, 0, "stop-bootstrap", "", "")


SCHEMA = "1"
_DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policies (
  project_id TEXT NOT NULL, policy_id TEXT NOT NULL, policy_hash TEXT NOT NULL,
  version TEXT NOT NULL, schema_version TEXT NOT NULL, compatibility TEXT NOT NULL,
  signature TEXT NOT NULL, key_id TEXT NOT NULL, artifact_json TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(project_id, policy_id, policy_hash)
);
CREATE TABLE IF NOT EXISTS activations (
  project_id TEXT NOT NULL, operation_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL,
  policy_hash TEXT NOT NULL, previous_policy_hash TEXT NOT NULL,
  activation_time TEXT NOT NULL, signature TEXT NOT NULL, record_json TEXT NOT NULL,
  generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS active_state (
  project_id TEXT PRIMARY KEY, active_policy_id TEXT, active_policy_hash TEXT,
  active_activation_id TEXT, generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rollback_records (
  project_id TEXT NOT NULL, rollback_operation_id TEXT PRIMARY KEY,
  superseded_policy_hash TEXT NOT NULL, restored_policy_hash TEXT,
  tombstone_hash TEXT NOT NULL, signature TEXT NOT NULL, record_json TEXT NOT NULL,
  generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_journal (
  operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation_type TEXT NOT NULL,
  generation INTEGER NOT NULL, payload_hash TEXT NOT NULL, committed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_project_generation
  ON operation_journal(project_id, generation);
CREATE TABLE IF NOT EXISTS authority_state (
  project_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
  kill_switch_active INTEGER NOT NULL CHECK(kill_switch_active IN (0, 1)),
  operation_id TEXT NOT NULL, changed_at TEXT NOT NULL, reason TEXT NOT NULL
);
"""


class PolicyStateStore:
    """SQLite store; mutation methods are intended only for the owner controller."""

    def __init__(self, state_dir: Path, *, read_only: bool = False) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "policy-state.sqlite3"
        self.read_only = read_only

    def initialize(self) -> None:
        if self.read_only:
            raise PolicyStateError("read-only policy store cannot initialize")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        with self._connection() as connection:
            connection.executescript(_DDL)
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema', ?)", (SCHEMA,)
            )
            connection.commit()
        os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PolicyStateError("read-only policy store cannot mutate")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prepare(self, policy: dict[str, Any], policy_hash: str) -> None:
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO policies
                (project_id, policy_id, policy_hash, version, schema_version,
                 compatibility, signature, key_id, artifact_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    policy["project_id"], policy["policy_id"], policy_hash,
                    policy["version"], policy["schema_version"], policy["compatibility"],
                    policy["signature"], policy["key_id"], _json(policy), _now(),
                ),
            )

    def bootstrap_authority(self, project_id: str, *, generation: int) -> None:
        """Create the initial inactive authority state from verified policy state."""
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO authority_state
                   (project_id, generation, kill_switch_active, operation_id, changed_at, reason)
                   VALUES (?, ?, 0, 'bootstrap', ?, 'initial authoritative state')""",
                (project_id, generation, _now()),
            )

    def authority_snapshot(self, project_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            try:
                row = connection.execute(
                    "SELECT * FROM authority_state WHERE project_id=?", (project_id,)
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return None
                raise PolicyStateError("authority state is unreadable") from exc
            return None if row is None else dict(row)

    def set_kill_switch(
        self,
        project_id: str,
        *,
        operation_id: str,
        active: bool,
        reason: str,
        authorization: dict[str, Any],
        expected_generation: int | None = None,
    ) -> int:
        """Atomically advance the owner-controlled authority generation."""
        self.initialize()
        with self.transaction() as connection:
            self._check_unused(connection, operation_id)
            self._verify_owner_operation(
                authorization, project_id, operation_id, active, reason
            )
            row = connection.execute(
                "SELECT generation, kill_switch_active FROM authority_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise PolicyStateError("authority state is not bootstrapped")
            current = int(row[0])
            if expected_generation is not None and current != expected_generation:
                raise PolicyStateError("stale authority generation")
            generation = current + 1
            payload = _json({"active": active, "reason": reason, "generation": generation})
            connection.execute(
                """UPDATE authority_state SET generation=?, kill_switch_active=?,
                   operation_id=?, changed_at=?, reason=? WHERE project_id=?""",
                (generation, int(active), operation_id, _now(), reason, project_id),
            )
            connection.execute(
                """INSERT INTO operation_journal
                   (operation_id, project_id, operation_type, generation,
                    payload_hash, committed_at)
                   VALUES (?, ?, 'kill_switch', ?, ?, ?)""",
                (operation_id, project_id, generation, _hash_text(payload), _now()),
            )
            return generation

    def reserve_delivery(
        self, project_id: str, *, operation_id: str, expected_generation: int
    ) -> None:
        """Durably reserve one decision before entering the final commit lock."""
        self.initialize()
        with self.transaction() as connection:
            self._check_unused(connection, operation_id)
            row = connection.execute(
                "SELECT generation, kill_switch_active FROM authority_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise PolicyStateError("authority state is not bootstrapped")
            if int(row[0]) != expected_generation:
                raise PolicyStateError("authorization generation is stale")
            if int(row[1]):
                raise PolicyStateError("kill switch is active")
            connection.execute(
                """INSERT INTO operation_journal
                   (operation_id, project_id, operation_type, generation,
                    payload_hash, committed_at)
                   VALUES (?, ?, 'delivery_reserved', ?, ?, ?)""",
                (operation_id, project_id, expected_generation,
                 _hash_text(operation_id), _now()),
            )

    @contextmanager
    def delivery_transaction(
        self,
        project_id: str,
        *,
        operation_id: str,
        expected_generation: int,
        commit_token: str,
    ) -> Iterator[sqlite3.Connection]:
        """Hold the authority write lock through the irreversible delivery commit."""
        if self.read_only:
            raise PolicyStateError("read-only policy store cannot authorize delivery")
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT generation, kill_switch_active FROM authority_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise PolicyStateError("authority state is not bootstrapped")
            if int(row[0]) != expected_generation:
                raise PolicyStateError("authorization generation is stale")
            if int(row[1]):
                raise PolicyStateError("kill switch is active")
            journal = connection.execute(
                "SELECT generation, operation_type, payload_hash "
                "FROM operation_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if journal is None or journal[1] != "delivery_committing":
                raise PolicyStateError("delivery authorization is not in commit state")
            if int(journal[0]) != expected_generation:
                raise PolicyStateError("authorization generation is stale")
            if journal[2] != commit_token:
                raise PolicyStateError("delivery commit token is invalid")
            yield connection
            connection.execute(
                "UPDATE operation_journal SET operation_type='delivery_committed', "
                "committed_at=? WHERE operation_id=?", (_now(), operation_id)
            )

    def begin_delivery_commit(
        self, project_id: str, *, operation_id: str, expected_generation: int
    ) -> str:
        """Durably enter a non-replayable commit state before Git mutation."""
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT generation, kill_switch_active FROM authority_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            journal = connection.execute(
                "SELECT generation, operation_type FROM operation_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None or journal is None or journal[1] != "delivery_reserved":
                raise PolicyStateError("delivery authorization is not reservable")
            if int(row[0]) != expected_generation or int(journal[0]) != expected_generation:
                raise PolicyStateError("authorization generation is stale")
            if int(row[1]):
                raise PolicyStateError("kill switch is active")
            commit_token = uuid.uuid4().hex
            connection.execute(
                "UPDATE operation_journal SET operation_type='delivery_committing', "
                "payload_hash=? WHERE operation_id=?", (commit_token, operation_id)
            )
            return commit_token

    def activate(
        self,
        project_id: str,
        policy: dict[str, Any],
        activation: dict[str, Any],
        *,
        expected_generation: int | None = None,
        failure_hook: Callable[[str, sqlite3.Connection], None] | None = None,
    ) -> int:
        self.initialize()
        with self.transaction() as connection:
            self._check_unused(connection, activation["operation_id"])
            row = connection.execute(
                "SELECT generation, active_policy_hash FROM active_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is not None and row[1] is not None:
                raise PolicyStateError("active policy already exists")
            if connection.execute(
                "SELECT 1 FROM rollback_records WHERE project_id=? AND superseded_policy_hash=?",
                (project_id, activation["policy_hash"]),
            ).fetchone():
                raise PolicyStateError("policy artifact was superseded by rollback")
            generation = 1 if row is None else int(row[0]) + 1
            current_generation = 0 if row is None else int(row[0])
            if expected_generation is not None and current_generation != expected_generation:
                raise PolicyStateError("stale generation")
            payload_hash = activation["policy_hash"]
            policy_row = connection.execute(
                "SELECT 1 FROM policies WHERE project_id=? AND policy_id=? AND policy_hash=?",
                (project_id, policy["policy_id"], payload_hash),
            ).fetchone()
            if policy_row is None:
                raise PolicyStateError("policy artifact is not prepared")
            if failure_hook:
                failure_hook("before_journal", connection)
            connection.execute(
                """INSERT INTO operation_journal
                VALUES (?, ?, 'activate', ?, ?, ?)""",
                (activation["operation_id"], project_id, generation, payload_hash, _now()),
            )
            if failure_hook:
                failure_hook("after_journal", connection)
            connection.execute(
                """INSERT INTO activations
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, activation["operation_id"], activation["policy_id"],
                    activation["policy_hash"], activation["previous_policy_hash"],
                    activation["activation_time"], activation["signature"],
                    _json(activation), generation,
                ),
            )
            if failure_hook:
                failure_hook("after_activation", connection)
            connection.execute(
                """INSERT INTO active_state
                (project_id, active_policy_id, active_policy_hash, active_activation_id, generation)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                  active_policy_id=excluded.active_policy_id,
                  active_policy_hash=excluded.active_policy_hash,
                  active_activation_id=excluded.active_activation_id,
                  generation=excluded.generation""",
                (
                    project_id, activation["policy_id"], activation["policy_hash"],
                    activation["operation_id"], generation,
                ),
            )
            if failure_hook:
                failure_hook("before_commit", connection)
            return generation

    def rollback(
        self,
        project_id: str,
        rollback: dict[str, Any],
        *,
        expected_generation: int | None = None,
        expected_active_policy_hash: str | None = None,
        failure_hook: Callable[[str, sqlite3.Connection], None] | None = None,
    ) -> int:
        self.initialize()
        with self.transaction() as connection:
            self._check_unused(connection, rollback["operation_id"])
            row = connection.execute(
                "SELECT generation, active_policy_hash FROM active_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None or row[1] is None:
                raise PolicyStateError("no active policy to rollback")
            if expected_generation is not None and int(row[0]) != expected_generation:
                raise PolicyStateError("stale generation")
            if (expected_active_policy_hash is not None
                    and row[1] != expected_active_policy_hash):
                raise PolicyStateError("stale active policy")
            generation = int(row[0]) + 1
            restored_hash = rollback.get("restored_policy_hash")
            if restored_hash is None:
                raise PolicyStateError("rollback target is missing")
            target = connection.execute(
                "SELECT 1 FROM policies WHERE project_id=? AND policy_hash=?",
                (project_id, restored_hash),
            ).fetchone()
            if target is None and restored_hash != "" :
                # The constitutional fallback is pinned evidence, not an ADR-0003 artifact.
                if rollback.get("rollback_target", {}).get("policy_hash") != restored_hash:
                    raise PolicyStateError("rollback target is not pinned")
            if failure_hook:
                failure_hook("before_tombstone", connection)
            connection.execute(
                """INSERT INTO rollback_records
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, rollback["operation_id"], rollback["superseded_policy_hash"],
                    rollback.get("restored_policy_hash"), rollback["tombstone_hash"],
                    rollback["signature"], _json(rollback), generation,
                ),
            )
            if failure_hook:
                failure_hook("after_tombstone", connection)
            connection.execute(
                "INSERT INTO operation_journal VALUES (?, ?, 'rollback', ?, ?, ?)",
                (rollback["operation_id"], project_id, generation,
                 rollback["tombstone_hash"], _now()),
            )
            if failure_hook:
                failure_hook("after_journal", connection)
            connection.execute(
                """UPDATE active_state SET active_policy_id=NULL, active_policy_hash=NULL,
                   active_activation_id=NULL, generation=? WHERE project_id=?""",
                (generation, project_id),
            )
            if failure_hook:
                failure_hook("before_commit", connection)
            return generation

    def snapshot(self, project_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM active_state WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            state = dict(row)
            if state["active_policy_hash"] is None:
                rollback = connection.execute(
                    """SELECT record_json FROM rollback_records WHERE project_id=?
                       ORDER BY generation DESC LIMIT 1""", (project_id,)
                ).fetchone()
                state["rollback"] = None if rollback is None else json.loads(rollback[0])
                return state
            policy = connection.execute(
                """SELECT artifact_json FROM policies WHERE project_id=? AND policy_hash=?""",
                (project_id, state["active_policy_hash"]),
            ).fetchone()
            activation = connection.execute(
                "SELECT record_json FROM activations WHERE operation_id=?",
                (state["active_activation_id"],),
            ).fetchone()
            if policy is None or activation is None:
                raise PolicyStateError("active policy state is inconsistent")
            state["policy"] = json.loads(policy[0])
            state["activation"] = json.loads(activation[0])
            return state

    def latest_policy(self, project_id: str, policy_id: str) -> dict[str, Any] | None:
        if not self.read_only:
            self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """SELECT artifact_json FROM policies WHERE project_id=? AND policy_id=?
                   ORDER BY created_at DESC LIMIT 1""", (project_id, policy_id)
            ).fetchone()
            return None if row is None else json.loads(row[0])

    def _check_unused(self, connection: sqlite3.Connection, operation_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM operation_journal WHERE operation_id=?", (operation_id,)
        ).fetchone():
            raise PolicyStateError("operation identity has already been consumed")

    def _verify_owner_operation(
        self, authorization: dict[str, Any], project_id: str,
        operation_id: str, active: bool, reason: str,
    ) -> None:
        required = {"project_id", "operation_id", "active", "reason", "key_id", "signature"}
        if set(authorization) != required or authorization["project_id"] != project_id:
            raise PolicyStateError("owner authorization is invalid")
        if (
            authorization["operation_id"] != operation_id
            or authorization["active"] is not active
            or authorization["reason"] != reason
            or authorization["key_id"] != "owner-key-1"
        ):
            raise PolicyStateError("owner authorization is invalid")
        signature = authorization["signature"]
        if not isinstance(signature, str):
            raise PolicyStateError("owner authorization is invalid")
        unsigned = {key: value for key, value in authorization.items() if key != "signature"}
        try:
            import base64
            import hashlib
            import hmac

            key_path = self.state_dir / "constitution-authority" / "owner.key"
            key = base64.b64decode(key_path.read_text(encoding="ascii"), validate=True)
            expected = hmac.new(key, _json_bytes(unsigned), hashlib.sha256).hexdigest()
        except (OSError, UnicodeError, ValueError) as exc:
            raise PolicyStateError("owner authorization is invalid") from exc
        if not hmac.compare_digest(signature, expected):
            raise PolicyStateError("owner authorization is invalid")

    def _connection(self) -> sqlite3.Connection:
        if self.read_only:
            if not self.path.exists():
                raise PolicyStateError("policy state store is missing")
            uri = f"file:{self.path}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        else:
            connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if not self.read_only:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        return connection


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_bytes(value: dict[str, Any]) -> bytes:
    return _json(value).encode("utf-8")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
