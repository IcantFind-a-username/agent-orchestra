"""Versioned preferences; these records cannot grant execution or billing permission."""

import json
import sqlite3
import time
import uuid
from typing import cast

from orchestragent.serialization import dumps
from orchestragent.store import Conflict, Store


class SettingsStore:
    """Policy history with optimistic concurrency and an append-only audit trail."""

    def __init__(self, store: Store, provider_ids: tuple[str, ...] = ("mock",)):
        if not provider_ids:
            raise ValueError("At least one provider id is required")
        self.store = store
        self.provider_ids = tuple(provider_ids)
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS settings_revisions (
                revision INTEGER PRIMARY KEY, policy TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS settings_proposals (
                id TEXT PRIMARY KEY, base_revision INTEGER NOT NULL,
                summary TEXT NOT NULL, policy TEXT NOT NULL, applied_revision INTEGER,
                FOREIGN KEY(base_revision) REFERENCES settings_revisions(revision),
                FOREIGN KEY(applied_revision) REFERENCES settings_revisions(revision))""")
            db.execute("""CREATE TABLE IF NOT EXISTS settings_audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                at REAL NOT NULL, data TEXT NOT NULL)""")
            db.execute(
                "INSERT OR IGNORE INTO settings_revisions VALUES(1,?)",
                (dumps(self._default_policy()),),
            )

    def _default_policy(self) -> dict[str, object]:
        return {
            "scheduler_provider": self.provider_ids[0],
            "scheduler_model": "",
            "instructions": "",
            "providers": [
                {"id": provider, "model": "", "instructions": ""} for provider in self.provider_ids
            ],
        }

    @staticmethod
    def snapshot(db: sqlite3.Connection) -> dict[str, object]:
        row = db.execute(
            "SELECT revision,policy FROM settings_revisions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Settings must be initialized before snapshotting")
        return {"revision": row["revision"], "mode": "mock", "policy": json.loads(row["policy"])}

    def read(self) -> dict[str, object]:
        with self.store.transaction() as db:
            return self.snapshot(db)

    def _validate(self, policy: object) -> dict[str, object]:
        if not isinstance(policy, dict) or set(policy) != {
            "scheduler_provider",
            "scheduler_model",
            "instructions",
            "providers",
        }:
            raise ValueError("Policy requires exactly the documented preference fields")
        if policy["scheduler_provider"] not in self.provider_ids:
            raise ValueError("Unknown scheduler provider")
        for field, limit in (("scheduler_model", 128), ("instructions", 4096)):
            if not isinstance(policy[field], str) or len(policy[field]) > limit:
                raise ValueError(f"Invalid or oversized {field}")
        providers = policy["providers"]
        if not isinstance(providers, list) or len(providers) != len(self.provider_ids):
            raise ValueError("Policy requires each registered provider exactly once")
        seen: set[str] = set()
        for provider in providers:
            if not isinstance(provider, dict) or set(provider) != {"id", "model", "instructions"}:
                raise ValueError("Provider requires exactly id, model, and instructions")
            provider_id = provider["id"]
            if not isinstance(provider_id, str) or provider_id not in self.provider_ids:
                raise ValueError("Unknown provider")
            if provider_id in seen:
                raise ValueError("Duplicate provider")
            seen.add(provider_id)
            for field, limit in (("model", 128), ("instructions", 4096)):
                if not isinstance(provider[field], str) or len(provider[field]) > limit:
                    raise ValueError(f"Invalid or oversized provider {field}")
        encoded = dumps(policy)
        if len(encoded.encode("utf-8")) > 12000:
            raise ValueError("Policy exceeds 12000 UTF-8 bytes")
        return cast(dict[str, object], json.loads(encoded))

    def update(self, base_revision: int, policy: object) -> dict[str, object]:
        if type(base_revision) is not int or base_revision < 1:
            raise ValueError("base_revision must be a positive integer")
        validated = self._validate(policy)
        with self.store.transaction() as db:
            current = self.snapshot(db)
            if current["revision"] != base_revision:
                raise Conflict("Settings changed; reload the current revision")
            revision = base_revision + 1
            db.execute("INSERT INTO settings_revisions VALUES(?,?)", (revision, dumps(validated)))
            db.execute(
                "INSERT INTO settings_audit(kind,at,data) VALUES(?,?,?)",
                (
                    "updated",
                    time.time(),
                    dumps({"base_revision": base_revision, "revision": revision}),
                ),
            )
            return self.snapshot(db)

    @staticmethod
    def validate_instruction(instruction: str) -> None:
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2048:
            raise ValueError("Instruction must contain 1 to 2048 characters")

    def save_proposal(
        self,
        base_revision: int,
        policy: object,
        summary: str,
        *,
        mode: str = "mock",
    ) -> dict[str, object]:
        """Persist a validated preview, without applying it or changing permissions."""
        if type(base_revision) is not int or base_revision < 1:
            raise ValueError("base_revision must be a positive integer")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1024:
            raise ValueError("Proposal summary must contain 1 to 1024 characters")
        if mode not in ("mock", "real"):
            raise ValueError("Invalid proposal mode")
        validated = self._validate(policy)
        with self.store.transaction() as db:
            current = self.snapshot(db)
            if current["revision"] != base_revision:
                raise Conflict("Settings changed; create a new preview from current settings")
            proposal_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO settings_proposals VALUES(?,?,?,?,NULL)",
                (
                    proposal_id,
                    base_revision,
                    summary,
                    dumps(validated),
                ),
            )
            db.execute(
                "INSERT INTO settings_audit(kind,at,data) VALUES(?,?,?)",
                (
                    "proposed",
                    time.time(),
                    dumps({"id": proposal_id, "base_revision": base_revision, "mode": mode}),
                ),
            )
            return {
                "id": proposal_id,
                "base_revision": base_revision,
                "mode": mode,
                "summary": summary,
                "policy": validated,
            }

    def propose(self, instruction: str) -> dict[str, object]:
        self.validate_instruction(instruction)
        current = self.read()
        policy = cast(dict[str, object], current["policy"])
        previous = cast(str, policy["instructions"])
        policy["instructions"] = f"{previous}\n{instruction}" if previous else instruction
        summary = (
            "Preview: append the full sentence to global instructions. "
            "No semantic interpretation, model change, or permission change."
        )
        return self.save_proposal(cast(int, current["revision"]), policy, summary)

    def apply(self, proposal_id: str) -> dict[str, object]:
        if not isinstance(proposal_id, str) or len(proposal_id) > 128:
            raise ValueError("Invalid proposal ID")
        with self.store.transaction() as db:
            proposal = db.execute(
                "SELECT * FROM settings_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
            if proposal is None:
                raise KeyError("Settings proposal not found")
            if proposal["applied_revision"] is not None:
                row = db.execute(
                    "SELECT * FROM settings_revisions WHERE revision=?",
                    (proposal["applied_revision"],),
                ).fetchone()
                return {
                    "revision": row["revision"],
                    "mode": "mock",
                    "policy": json.loads(row["policy"]),
                }
            current = self.snapshot(db)
            if current["revision"] != proposal["base_revision"]:
                raise Conflict("Proposal is stale; create a new preview from current settings")
            revision = proposal["base_revision"] + 1
            db.execute("INSERT INTO settings_revisions VALUES(?,?)", (revision, proposal["policy"]))
            db.execute(
                "UPDATE settings_proposals SET applied_revision=? WHERE id=?",
                (
                    revision,
                    proposal_id,
                ),
            )
            db.execute(
                "INSERT INTO settings_audit(kind,at,data) VALUES(?,?,?)",
                (
                    "applied",
                    time.time(),
                    dumps({"id": proposal_id, "revision": revision}),
                ),
            )
            return self.snapshot(db)
