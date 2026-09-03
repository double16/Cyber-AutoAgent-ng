import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest

from modules.storage.sqlite import SQLiteMigrationRunner


def _migration(version: int, filename: str, sql: str) -> tuple[int, str, str, str]:
    return version, filename, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()


def test_packaged_migrations_create_schema_once(tmp_path):
    db_path = str(tmp_path / "cyber_autoagent.db")
    runner = SQLiteMigrationRunner(db_path)

    runner.migrate()
    runner.migrate()

    with closing(sqlite3.connect(db_path)) as conn, conn:
        applied = conn.execute(
            "SELECT version, filename FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        metric_columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_model_metrics)")}
    assert applied == [
        (1, "0001_initial_schema.sql"),
        (2, "0002_operation_model_metrics.sql"),
        (3, "0003_finding_evidence_receipts.sql"),
        (4, "0004_model_metric_correction_categories.sql"),
        (5, "0005_task_replacement_lineage.sql"),
        (6, "0006_task_recovery_context.sql"),
    ]
    assert {"operations", "plans", "tasks", "operation_model_metrics", "finding_evidence_receipts"}.issubset(tables)
    assert "correction_categories" in metric_columns


def test_task_replacement_lineage_migrates_existing_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "legacy.db")
    packaged_migrations = SQLiteMigrationRunner._load_migrations()
    legacy_migrations = [migration for migration in packaged_migrations if migration[0] < 5]
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: legacy_migrations))
    SQLiteMigrationRunner(db_path).migrate()

    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: packaged_migrations))
    SQLiteMigrationRunner(db_path).migrate()

    with closing(sqlite3.connect(db_path)) as conn, conn:
        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert {"replacement_of", "supersedes_criteria", "recovery_context"}.issubset(task_columns)


def test_concurrent_startup_applies_each_migration_once(tmp_path):
    db_path = str(tmp_path / "concurrent.db")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: SQLiteMigrationRunner(db_path).migrate(), range(2)))

    assert results == [None, None]
    with closing(sqlite3.connect(db_path)) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 6


def test_migrations_are_applied_in_version_order(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ordered.db")
    migrations = [
        _migration(2, "0002_second.sql", "ALTER TABLE example ADD COLUMN value TEXT;"),
        _migration(1, "0001_first.sql", "CREATE TABLE example (id INTEGER PRIMARY KEY);"),
    ]
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: migrations))

    SQLiteMigrationRunner(db_path).migrate()

    with closing(sqlite3.connect(db_path)) as conn, conn:
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

    with closing(sqlite3.connect(db_path)) as conn, conn:
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
    with closing(sqlite3.connect(db_path)) as conn, conn:
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


def test_invalid_migration_filename_is_rejected(monkeypatch):
    root = SimpleNamespace(iterdir=lambda: [SimpleNamespace(name="invalid.sql")])
    monkeypatch.setattr("modules.storage.sqlite.resources.files", lambda _package: root)

    with pytest.raises(RuntimeError, match="Invalid migration filename"):
        SQLiteMigrationRunner._load_migrations()


def test_migration_runner_tolerates_wal_lock_and_reconciles_concurrent_migrations(tmp_path, monkeypatch):
    migration = _migration(1, "0001_example.sql", "CREATE TABLE example (id INTEGER);")
    monkeypatch.setattr(SQLiteMigrationRunner, "_load_migrations", staticmethod(lambda: [migration]))
    original_connect = sqlite3.connect

    class ConnectionProxy:
        def __init__(self, connection, *, concurrent_row=None, lock_wal=False):
            self.connection = connection
            self.concurrent_row = concurrent_row
            self.lock_wal = lock_wal

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, parameters=()):
            if self.lock_wal and statement == "PRAGMA journal_mode = WAL":
                raise sqlite3.OperationalError("database is locked")
            if self.concurrent_row is not None and statement.startswith("SELECT filename, checksum"):
                return SimpleNamespace(fetchone=lambda: self.concurrent_row)
            return self.connection.execute(statement, parameters)

    wal_path = str(tmp_path / "wal-lock.db")
    monkeypatch.setattr(
        "modules.storage.sqlite.sqlite3.connect",
        lambda *args, **kwargs: ConnectionProxy(original_connect(*args, **kwargs), lock_wal=True),
    )
    SQLiteMigrationRunner(wal_path).migrate()

    concurrent_path = str(tmp_path / "concurrent-row.db")
    matching_row = (migration[1], migration[3])
    monkeypatch.setattr(
        "modules.storage.sqlite.sqlite3.connect",
        lambda *args, **kwargs: ConnectionProxy(original_connect(*args, **kwargs), concurrent_row=matching_row),
    )
    SQLiteMigrationRunner(concurrent_path).migrate()

    changed_row = (migration[1], "changed")
    monkeypatch.setattr(
        "modules.storage.sqlite.sqlite3.connect",
        lambda *args, **kwargs: ConnectionProxy(original_connect(*args, **kwargs), concurrent_row=changed_row),
    )
    with pytest.raises(RuntimeError, match="Applied migration 0001 has changed"):
        SQLiteMigrationRunner(str(tmp_path / "concurrent-changed.db")).migrate()


def test_migration_loader_rejects_duplicate_versions_and_ignores_non_sql_files(monkeypatch):
    root = SimpleNamespace(
        iterdir=lambda: [
            SimpleNamespace(name="README.txt"),
            SimpleNamespace(name="0001_first.sql", read_text=lambda **_kwargs: "SELECT 1;"),
            SimpleNamespace(name="0001_second.sql", read_text=lambda **_kwargs: "SELECT 2;"),
        ]
    )
    monkeypatch.setattr("modules.storage.sqlite.resources.files", lambda _package: root)

    with pytest.raises(RuntimeError, match="Duplicate migration version: 0001"):
        SQLiteMigrationRunner._load_migrations()


def test_statement_splitter_handles_quoted_semicolons_and_rejects_incomplete_sql(monkeypatch):
    statements = SQLiteMigrationRunner._split_statements(
        "CREATE TABLE example (value TEXT); INSERT INTO example VALUES ('a;b');"
    )

    assert len(statements) == 2
    with pytest.raises(RuntimeError, match="incomplete SQL statement"):
        SQLiteMigrationRunner._split_statements("CREATE TABLE incomplete (id INTEGER)")

    monkeypatch.setattr("modules.storage.sqlite.sqlite3.complete_statement", lambda _sql: True)
    assert SQLiteMigrationRunner._split_statements("SELECT 1") == ["SELECT 1"]
