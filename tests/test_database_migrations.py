import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from modules.storage.sqlite import SQLiteMigrationRunner


def _migration(version: int, filename: str, sql: str) -> tuple[int, str, str, str]:
    return version, filename, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()


def test_packaged_migrations_create_schema_once(tmp_path):
    db_path = str(tmp_path / "cyber_autoagent.db")
    runner = SQLiteMigrationRunner(db_path)

    runner.migrate()
    runner.migrate()

    with sqlite3.connect(db_path) as conn:
        applied = conn.execute(
            "SELECT version, filename FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert applied == [(1, "0001_initial_schema.sql"), (2, "0002_operation_model_metrics.sql")]
    assert {"operations", "plans", "tasks", "operation_model_metrics"}.issubset(tables)


def test_concurrent_startup_applies_each_migration_once(tmp_path):
    db_path = str(tmp_path / "concurrent.db")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: SQLiteMigrationRunner(db_path).migrate(), range(2)))

    assert results == [None, None]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2


def test_migrations_are_applied_in_version_order(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ordered.db")
    migrations = [
        _migration(2, "0002_second.sql", "ALTER TABLE example ADD COLUMN value TEXT;"),
        _migration(1, "0001_first.sql", "CREATE TABLE example (id INTEGER PRIMARY KEY);"),
    ]
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: migrations))

    SQLiteMigrationRunner(db_path).migrate()

    with sqlite3.connect(db_path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        columns = [row[1] for row in conn.execute("PRAGMA table_info(example)")]
    assert versions == [1, 2]
    assert columns == ["id", "value"]


def test_failed_migration_rolls_back_schema_and_ledger(tmp_path, monkeypatch):
    db_path = str(tmp_path / "failed.db")
    sql = "CREATE TABLE should_rollback (id INTEGER); INVALID SQL;"
    monkeypatch.setattr(
        SQLiteMigrationRunner,
        "_load_migrations",
        staticmethod(lambda: [_migration(1, "0001_failed.sql", sql)]),
    )

    with pytest.raises(sqlite3.DatabaseError):
        SQLiteMigrationRunner(db_path).migrate()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'"
        ).fetchone() is None
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == []


def test_changed_applied_migration_is_rejected(tmp_path, monkeypatch):
    db_path = str(tmp_path / "changed.db")
    initial = _migration(1, "0001_initial.sql", "CREATE TABLE example (id INTEGER);")
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: [initial]))
    SQLiteMigrationRunner(db_path).migrate()

    changed = _migration(1, "0001_initial.sql", "CREATE TABLE example (id TEXT);")
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: [changed]))

    with pytest.raises(RuntimeError, match="Applied migration 0001 has changed"):
        SQLiteMigrationRunner(db_path).migrate()


def test_unknown_applied_migration_is_rejected(tmp_path):
    db_path = str(tmp_path / "unknown.db")
    SQLiteMigrationRunner(db_path).migrate()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at) VALUES (?, ?, ?, ?)",
            (9999, "9999_unknown.sql", "checksum", "now"),
        )

    with pytest.raises(RuntimeError, match="unknown migration versions"):
        SQLiteMigrationRunner(db_path).migrate()


def test_transaction_control_in_migration_is_rejected(tmp_path, monkeypatch):
    migration = _migration(1, "0001_bad.sql", "BEGIN; CREATE TABLE example (id INTEGER); COMMIT;")
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: [migration]))

    with pytest.raises(RuntimeError, match="must not contain transaction control"):
        SQLiteMigrationRunner(str(tmp_path / "transaction.db")).migrate()


def test_statement_splitter_handles_quoted_semicolons_and_rejects_incomplete_sql():
    statements = SQLiteMigrationRunner._split_statements(
        "CREATE TABLE example (value TEXT); INSERT INTO example VALUES ('a;b');"
    )

    assert len(statements) == 2
    with pytest.raises(RuntimeError, match="incomplete SQL statement"):
        SQLiteMigrationRunner._split_statements("CREATE TABLE incomplete (id INTEGER)")
