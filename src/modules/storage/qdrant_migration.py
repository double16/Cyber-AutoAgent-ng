"""Preserve legacy unnamed local Qdrant collections across client upgrades."""

import json
import logging
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import portalocker

LEGACY_COLLECTION_NAME = "cyber_autoagent_legacy_unnamed"
logger = logging.getLogger(__name__)


def migrate_unnamed_collection(location: str | Path) -> None:
    """Back up and rename an unnamed collection while holding Qdrant's storage lock.

    Original database files remain untouched. Metadata switches atomically only after
    a verified SQLite snapshot is available under a valid collection name.
    """
    root = Path(location)
    meta_path = root / "meta.json"
    if not meta_path.exists():
        return
    with portalocker.Lock(str(root / ".lock"), mode="a", flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
        original = meta_path.read_bytes()
        meta = json.loads(original)
        if "" not in meta["collections"]:
            return
        destination = root / "collection" / LEGACY_COLLECTION_NAME
        source = root / "collection" / "storage.sqlite"
        if LEGACY_COLLECTION_NAME in meta["collections"] or destination.exists():
            raise ValueError("Legacy Qdrant migration destination already exists; manual review required")
        if source.is_symlink() or source.resolve().parent != (root.resolve() / "collection"):
            raise ValueError("Legacy Qdrant database must remain inside the storage collection directory")
        if not source.is_file():
            raise ValueError("Legacy Qdrant collection database is missing; metadata was not changed")
        backup = Path(tempfile.mkdtemp(prefix="legacy-unnamed-backup-", dir=root))
        (backup / "meta.json").write_bytes(original)
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Legacy Qdrant database integrity check failed; metadata was not changed")
            with closing(sqlite3.connect(backup / "storage.sqlite")) as snapshot:
                connection.backup(snapshot)
        destination.mkdir()
        temporary = root / "meta.json.migrating"
        try:
            shutil.copy2(backup / "storage.sqlite", destination / "storage.sqlite")
            meta["collections"][LEGACY_COLLECTION_NAME] = meta["collections"].pop("")
            meta["aliases"] = {
                alias: LEGACY_COLLECTION_NAME if name == "" else name
                for alias, name in meta.get("aliases", {}).items()
            }
            indexes = meta.get("text_indexes", {})
            if "" in indexes:
                indexes[LEGACY_COLLECTION_NAME] = indexes.pop("")
            temporary.write_text(json.dumps(meta), encoding="utf-8")
            temporary.replace(meta_path)
        except Exception:
            shutil.rmtree(destination)
            temporary.unlink(missing_ok=True)
            raise
        logger.info("Migrated unnamed Qdrant collection to %s; backup: %s", LEGACY_COLLECTION_NAME, backup)
