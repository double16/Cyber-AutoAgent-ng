"""Data-preserving migration for legacy unnamed Qdrant collections."""

import json
import sqlite3
from contextlib import closing

import portalocker
import pytest
from qdrant_client import QdrantClient, models

from modules.storage.qdrant_migration import LEGACY_COLLECTION_NAME, migrate_unnamed_collection


@pytest.fixture
def legacy(tmp_path):
    with closing(QdrantClient(path=str(tmp_path))) as client:
        client.create_collection("old", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
        client.upsert("old", [models.PointStruct(id=1, vector=[1.0, 0.0], payload={"evidence": "retained"})])
    database = tmp_path / "collection" / "old" / "storage.sqlite"
    database.rename(tmp_path / "collection" / "storage.sqlite")
    database.parent.rmdir()
    meta_path = tmp_path / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["collections"][""] = meta["collections"].pop("old")
    meta["aliases"] = {"legacy_alias": ""}
    meta["text_indexes"] = {"": {}}
    meta_path.write_text(json.dumps(meta))
    return tmp_path


def test_migration_preserves_records_aliases_backup_and_original(legacy):
    original = (legacy / "meta.json").read_bytes()
    migrate_unnamed_collection(legacy)
    with closing(QdrantClient(path=str(legacy))) as client:
        assert client.retrieve(LEGACY_COLLECTION_NAME, [1])[0].payload == {"evidence": "retained"}
        assert client.retrieve("legacy_alias", [1])[0].id == 1
    backup, = legacy.glob("legacy-unnamed-backup-*")
    assert (backup / "meta.json").read_bytes() == original
    with closing(sqlite3.connect(backup / "storage.sqlite")) as connection:
        assert connection.execute("SELECT count(*) FROM points").fetchone() == (1,)
    assert (legacy / "collection" / "storage.sqlite").exists()
    migrate_unnamed_collection(legacy)
    assert len(list(legacy.glob("legacy-unnamed-backup-*"))) == 1


def test_fresh_storage_is_unchanged(tmp_path):
    migrate_unnamed_collection(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["collision", "missing", "symlink", "corrupt"])
def test_migration_refuses_unsafe_or_unavailable_source(legacy, failure):
    source = legacy / "collection" / "storage.sqlite"
    if failure == "collision":
        (legacy / "collection" / LEGACY_COLLECTION_NAME).mkdir()
    elif failure == "missing":
        source.unlink()
    elif failure == "symlink":
        source.rename(legacy / "outside.sqlite")
        source.symlink_to(legacy / "outside.sqlite")
    else:
        source.write_bytes(b"invalid database")
    original = (legacy / "meta.json").read_bytes()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        migrate_unnamed_collection(legacy)
    assert (legacy / "meta.json").read_bytes() == original


def test_active_client_lock_prevents_migration(legacy):
    with portalocker.Lock(str(legacy / ".lock"), mode="a", flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
        with pytest.raises(portalocker.exceptions.LockException):
            migrate_unnamed_collection(legacy)
    assert "" in json.loads((legacy / "meta.json").read_text())["collections"]


def test_metadata_write_failure_rolls_back_destination(legacy, monkeypatch):
    from pathlib import Path

    original = (legacy / "meta.json").read_bytes()
    replace = Path.replace
    def fail(path, target):
        if path.name == "meta.json.migrating":
            raise OSError("write failed")
        return replace(path, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="write failed"):
        migrate_unnamed_collection(legacy)
    assert (legacy / "meta.json").read_bytes() == original
    assert not (legacy / "collection" / LEGACY_COLLECTION_NAME).exists()
