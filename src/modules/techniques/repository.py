"""Transactional global technique lifecycle and controller-owned use receipts."""

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from modules.storage.sqlite import SQLiteMigrationRunner


class TechniqueRepository:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        SQLiteMigrationRunner(path).migrate()

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def get(self, revision: str) -> dict[str, Any] | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM technique_revisions WHERE revision_id = ?", (revision,)).fetchone()
            return dict(row) if row else None

    def revisions(self) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM technique_revisions ORDER BY created_at")]

    def publish(self, revision: str, lineage: str, digest: str, origin: str, active: bool, collection: str) -> None:
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM technique_revisions WHERE revision_id = ?", (revision,)).fetchone():
                return
            conn.execute(
                "UPDATE technique_revisions SET active = 0, current_revision = 0, "
                "inactive_reason = 'superseded', pending_sync = 1 WHERE lineage = ? AND current_revision = 1",
                (lineage,),
            )
            conn.execute(
                "INSERT INTO technique_revisions "
                "(revision_id, lineage, source_sha256, origin, active, inactive_reason, "
                "document_collection, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision,
                    lineage,
                    digest,
                    origin,
                    int(active),
                    "" if active else "module_inactive",
                    collection,
                    time.time(),
                ),
            )

    def synced(self, revision: str, collection: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE technique_revisions SET pending_sync = 0, document_collection = ? WHERE revision_id = ?",
                (collection, revision),
            )

    def use(self, operation: str, task: str, revision: str, use_context: dict | None = None) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT active FROM technique_revisions WHERE revision_id = ?", (revision,)).fetchone()
            if row is None or not row[0]:
                raise ValueError("Cannot use an inactive or unknown technique")
            conn.execute(
                "INSERT OR IGNORE INTO technique_uses(operation_id, task_uid, revision_id, created_at, use_context) "
                "VALUES (?, ?, ?, ?, ?)",
                (operation, task, revision, time.time(), json.dumps(use_context or {}, sort_keys=True)),
            )

    def pending_uses(self, operation: str, task: str) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM technique_uses WHERE operation_id = ? AND task_uid = ? AND score IS NULL",
                    (operation, task),
                )
            ]

    def feedback(self, operation: str, task: str, revision: str, score: int, reason: str) -> dict | None:
        if type(score) is not int or score not in {-1, 0, 1} or not reason.strip():
            raise ValueError("Feedback requires a -1, 0, or +1 score and a reason")
        with self.transaction() as conn:
            before = conn.execute(
                "SELECT active FROM technique_revisions WHERE revision_id = ?", (revision,)
            ).fetchone()
            updated = conn.execute(
                "UPDATE technique_uses SET score = ?, reason = ? "
                "WHERE operation_id = ? AND task_uid = ? AND revision_id = ? AND score IS NULL",
                (score, reason, operation, task, revision),
            ).rowcount
            if not updated:
                return
            conn.execute(
                "UPDATE technique_revisions SET score_count = score_count + 1, score_total = score_total + ?, "
                "pending_sync = 1 WHERE revision_id = ?",
                (score, revision),
            )
            conn.execute(
                "UPDATE technique_revisions SET active = 0, inactive_reason = 'negative_feedback' "
                "WHERE revision_id = ? AND score_count >= 3 AND score_total * 1.0 / score_count <= -0.5",
                (revision,),
            )

            row = dict(conn.execute("SELECT * FROM technique_revisions WHERE revision_id = ?", (revision,)).fetchone())
            return {**row, "deactivated": bool(before[0]) and not bool(row["active"])}

    def feedback_reasons(self, revision: str) -> list[str]:
        with self.transaction() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT reason FROM technique_uses WHERE revision_id = ? AND reason IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 5",
                    (revision,),
                )
            ]

    def scored_uses(self, operation: str, task: str) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM technique_uses WHERE operation_id = ? AND task_uid = ? AND score IS NOT NULL",
                    (operation, task),
                )
            ]

    def lease(self, key: str, owner: str, seconds: int = 300) -> bool:
        with self.transaction() as conn:
            conn.execute("DELETE FROM technique_build_leases WHERE expires_at <= ?", (time.time(),))
            conn.execute(
                "INSERT OR IGNORE INTO technique_build_leases VALUES (?, ?, ?)",
                (key, owner, time.time() + seconds),
            )
            row = conn.execute("SELECT owner FROM technique_build_leases WHERE request_hash = ?", (key,)).fetchone()
            return row[0] == owner

    def release(self, key: str, owner: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM technique_build_leases WHERE request_hash = ? AND owner = ?", (key, owner))

    def renew(self, key: str, owner: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE technique_build_leases SET expires_at = ? WHERE request_hash = ? AND owner = ?",
                (time.time() + 300, key, owner),
            )
