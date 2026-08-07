"""SQLite application database migrations."""

import hashlib
import re
import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path


_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


class SQLiteMigrationRunner:
    """Apply packaged, forward-only SQL migrations exactly once."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def migrate(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as error:
                if "database is locked" not in str(error).lower():
                    raise
                # A concurrent migrator owns the startup lock and will persist WAL mode.
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row[0]): (str(row[1]), str(row[2]))
                for row in conn.execute("SELECT version, filename, checksum FROM schema_migrations")
            }
            migrations = sorted(self._load_migrations(), key=lambda item: item[0])
            known_versions = {version for version, _, _, _ in migrations}
            unknown = sorted(set(applied) - known_versions)
            if unknown:
                raise RuntimeError(f"Database contains unknown migration versions: {unknown}")
            for version, filename, sql, checksum in migrations:
                if version in applied:
                    applied_filename, applied_checksum = applied[version]
                    if (applied_filename, applied_checksum) != (filename, checksum):
                        raise RuntimeError(f"Applied migration {version:04d} has changed")
                    continue
                if re.search(r"\b(BEGIN|COMMIT|ROLLBACK)\b", sql, flags=re.IGNORECASE):
                    raise RuntimeError(f"Migration {filename} must not contain transaction control")
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    concurrent_row = conn.execute(
                        "SELECT filename, checksum FROM schema_migrations WHERE version = ?",
                        (version,),
                    ).fetchone()
                    if concurrent_row is not None:
                        if (str(concurrent_row[0]), str(concurrent_row[1])) != (filename, checksum):
                            raise RuntimeError(f"Applied migration {version:04d} has changed")
                        conn.commit()
                        continue
                    applied_at = datetime.now().isoformat()
                    for statement in self._split_statements(sql):
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
                        "VALUES (?, ?, ?, ?)",
                        (version, filename, checksum, applied_at),
                    )
                    conn.commit()
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    raise

    @staticmethod
    def _load_migrations() -> list[tuple[int, str, str, str]]:
        migration_root = resources.files("modules.storage.migrations")
        migrations: list[tuple[int, str, str, str]] = []
        versions: set[int] = set()
        for item in migration_root.iterdir():
            if not item.name.endswith(".sql"):
                continue
            match = _MIGRATION_NAME.fullmatch(item.name)
            if match is None:
                raise RuntimeError(f"Invalid migration filename: {item.name}")
            version = int(match.group("version"))
            if version in versions:
                raise RuntimeError(f"Duplicate migration version: {version:04d}")
            versions.add(version)
            sql = item.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            migrations.append((version, item.name, sql, checksum))
        migrations.sort(key=lambda item: item[0])
        return migrations

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """Split a migration without breaking quoted semicolons or trigger bodies."""
        statements: list[str] = []
        buffer = ""
        for character in sql:
            buffer += character
            if character == ";" and sqlite3.complete_statement(buffer):
                statements.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            if not sqlite3.complete_statement(buffer):
                raise RuntimeError("Migration contains an incomplete SQL statement")
            statements.append(buffer.strip())
        return statements
