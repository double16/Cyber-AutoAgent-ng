from concurrent.futures import ThreadPoolExecutor

import pytest

from modules.techniques.documents import TechniqueDocument
from modules.techniques.storage import DOCUMENTS, TechniqueStore


def test_publish_retrieve_replace_and_skip_unchanged(store, markdown):
    first = store.publish(TechniqueDocument(markdown()), "module:web:jwt.md", "module:web")
    assert store.publish(TechniqueDocument(markdown()), "module:web:jwt.md", "module:web") == first
    second = store.publish(TechniqueDocument(markdown("Updated JWT")), "module:web:jwt.md", "module:web")
    assert first != second
    assert store.repository.get(first)["inactive_reason"] == "superseded"
    assert [item["revision_id"] for item in store.search("JWT", active=True)] == [second]
    assert [item["revision_id"] for item in store.search("JWT", active=False)] == [first]
    assert "Updated JWT" in store.render(second)
    with pytest.raises(ValueError, match="inactive"):
        store.render(first)
    assert len(store.repository.revisions()) == 2
    assert store.triggers([]) == []
    assert store.triggers(["reconnaissance"]) == []
    assert store.triggers(["hypothesis_generation"])[0]["revision_id"] == second


def test_feedback_deactivates_only_after_threshold_and_is_idempotent(store, markdown):
    revision = store.publish(TechniqueDocument(markdown()), "lineage", "generated")
    repo = store.repository
    for number, score in enumerate([-1, -1, 0]):
        task = f"task-{number}"
        repo.use("op", task, revision)
        repo.use("op", task, revision)
        assert len(repo.pending_uses("op", task)) == 1
        repo.feedback("op", task, revision, score, "A generalized reason")
        repo.feedback("op", task, revision, -1, "Duplicate is ignored")
        assert not repo.pending_uses("op", task)
        assert bool(repo.get(revision)["active"]) is (number < 2)
    store.reconcile()
    assert not store.search("JWT", active=True)
    assert "A generalized reason" in store.render(revision, require_active=False)
    assert store.triggers(["hypothesis_generation"])
    assert store.publish(TechniqueDocument(markdown()), "lineage", "generated") == revision
    assert not repo.get(revision)["active"]
    with pytest.raises(ValueError):
        repo.use("op", "other", revision)
    with pytest.raises(ValueError):
        repo.use("op", "other", "unknown")
    with pytest.raises(ValueError):
        repo.feedback("op", "other", revision, True, "Bad")
    with pytest.raises(ValueError):
        repo.feedback("op", "other", revision, -1, " ")


def test_positive_outcome_prevents_deactivation_and_feedback_is_revision_scoped(store, markdown):
    revision = store.publish(TechniqueDocument(markdown()), "lineage", "generated")
    for number, score in enumerate([-1, -1, 1]):
        store.repository.use("op", str(number), revision)
        store.repository.feedback("op", str(number), revision, score, "Reason")
    assert store.repository.get(revision)["active"]
    next_revision = store.publish(TechniqueDocument(markdown("Revised")), "lineage", "generated")
    assert store.repository.get(next_revision)["score_count"] == 0


def test_model_change_reindexes_existing_qdrant_documents(store, markdown):
    revision = store.publish(TechniqueDocument(markdown()), "lineage", "generated")
    new_store = TechniqueStore(
        store.qdrant, store.embeddings, {"embedding_dimensions": 3, "embedding_model": "new"}, store.repository
    )
    assert new_store.index != store.index
    assert new_store.search("JWT", active=True)[0]["revision_id"] == revision


def test_failed_staging_preserves_previous_active_revision(store, markdown, monkeypatch):
    first = store.publish(TechniqueDocument(markdown()), "lineage", "generated")
    monkeypatch.setattr(store, "_index", lambda *args: (_ for _ in ()).throw(RuntimeError("Index unavailable")))
    with pytest.raises(RuntimeError):
        store.publish(TechniqueDocument(markdown("New")), "lineage", "generated")
    assert store.repository.get(first)["active"]


def test_reconcile_recovers_lifecycle_write_failure(store, markdown, monkeypatch):
    document = TechniqueDocument(markdown(active=False))
    original = store.qdrant.set_payload
    monkeypatch.setattr(
        store.qdrant, "set_payload", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    )
    with pytest.raises(RuntimeError):
        store.publish(document, "lineage", "generated")
    row = store.repository.revisions()[0]
    assert row["pending_sync"]
    monkeypatch.setattr(store.qdrant, "set_payload", original)
    store.reconcile()
    assert not store.repository.get(row["revision_id"])["pending_sync"]
    assert not store.search("JWT", active=True)
    store.qdrant.delete(DOCUMENTS, points_selector=[row["revision_id"]], wait=True)
    with pytest.raises(ValueError, match="unavailable"):
        store.document(row["revision_id"])
    with pytest.raises(ValueError):
        store.render("unknown")


def test_dimension_mismatch(store, markdown, monkeypatch):
    monkeypatch.setattr(store.embeddings, "embed_query", lambda text: [1.0])
    with pytest.raises(ValueError, match="dimension"):
        store.publish(TechniqueDocument(markdown(triggers=False)), "lineage", "generated")


def test_lease_ownership_expiry_and_concurrency(store):
    repo = store.repository
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda owner: repo.lease("key", owner), ["a", "b"]))
    assert sum(results) == 1
    owner = "a" if results[0] else "b"
    repo.renew("key", owner)
    repo.release("key", "wrong")
    assert not repo.lease("key", "new")
    repo.release("key", owner)
    assert repo.lease("key", "new")
    repo.lease("expired", "first", seconds=-1)
    assert repo.lease("expired", "second")


def test_search_filters_phase_before_limit_and_returns_scores(store, markdown):
    from modules.techniques.documents import TechniqueDocument

    for index in range(300):
        store.publish(
            TechniqueDocument(markdown(title=f"Unrelated {index}").replace(
                "- hypothesis_generation", "- analysis",
            )), f"decoy:{index}", "test",
        )
    revision = store.publish(TechniqueDocument(markdown()), "relevant", "test")
    results = store.search("JWT", active=True, contexts=["hypothesis_generation"], limit=1)
    assert results[0]["revision_id"] == revision
    assert isinstance(results[0]["score"], float)
    triggers = store.search_triggers("JWT", ["hypothesis_generation"], limit=1)
    assert triggers[0]["revision_id"] == revision
    assert not store.search_triggers("", ["hypothesis_generation"])
    assert not store.search_triggers("JWT", [])
    assert not store.search("JWT", active=True, contexts=["execution"])


def test_embedding_status_distinguishes_rebuild_and_reuse(store, markdown):
    assert store.embedding_status()["embedding_documents"] == 0
    revision = store.publish(TechniqueDocument(markdown()), "module:web:jwt.md", "module:web")
    status = store.embedding_status()
    assert status["embedding_documents"] == 1
    assert status["embedding_model"] == "test"
    assert status["embedding_representation"] == "full"
    assert status["embedding_index"] == store.index
    store.reconcile()
    assert store.embedding_status() == status
    reused = TechniqueStore(
        store.qdrant, store.embeddings, {"embedding_dimensions": 3, "embedding_model": "test"}, store.repository,
    )
    assert reused.embedding_status()["embedding_documents"] == 0
    rebuilt = TechniqueStore(
        store.qdrant, store.embeddings, {"embedding_dimensions": 3, "embedding_model": "new-model"}, store.repository,
    )
    assert rebuilt.embedding_status()["embedding_documents"] == 1
    assert rebuilt.render(revision) == store.render(revision)


@pytest.mark.parametrize("version", [None, 0, 2])
def test_obsolete_generation_is_excluded_but_preserved(store, markdown, version):
    revision = store.publish(TechniqueDocument(markdown()), "old", "generated")
    with store.repository.transaction() as connection:
        connection.execute("UPDATE technique_revisions SET generation_version = ?, pending_sync = 1 "
                           "WHERE revision_id = ?", (version, revision))
    assert store.search("JWT", active=True) == []
    assert store.search_triggers("JWT", ["hypothesis_generation"]) == []
    assert store.triggers(["hypothesis_generation"]) == []
    assert store.document(revision).body
    assert store.render(revision)
    with pytest.raises(ValueError):
        store.render(revision, guidance=True)
    with pytest.raises(ValueError):
        store.repository.use("operation", "task", revision)


def test_generation_stamp_and_module_compatibility(store, markdown):
    from modules.techniques.versioning import TECHNIQUE_GENERATION_VERSION

    generated = store.publish(TechniqueDocument(markdown()), "generated", "generated")
    module = store.publish(TechniqueDocument(markdown()), "module", "module:web")
    assert store.repository.get(generated)["generation_version"] == TECHNIQUE_GENERATION_VERSION
    assert store.repository.get(module)["generation_version"] is None
    assert {row["revision_id"] for row in store.search("JWT", active=True)} == {generated, module}
    assert store.render(module, guidance=True)


def test_generation_increment_creates_distinct_revision_without_changing_source(store, markdown, monkeypatch):
    import modules.techniques.repository as repository
    import modules.techniques.storage as storage

    document = TechniqueDocument(markdown())
    first = store.publish(document, "same-lineage", "generated")
    store.repository.use("operation", "task", first, {"delivered_documents": ["original snapshot"]})
    monkeypatch.setattr(storage, "TECHNIQUE_GENERATION_VERSION", 2)
    monkeypatch.setattr(repository, "TECHNIQUE_GENERATION_VERSION", 2)
    second = store.publish(document, "same-lineage", "generated")
    assert second != first
    assert store.repository.get(second)["source_sha256"] == document.sha256
    assert store.repository.get(first)["generation_version"] == 1
    assert store.repository.get(second)["generation_version"] == 2
    assert "original snapshot" in store.repository.pending_uses("operation", "task")[0]["use_context"]


def test_stale_documents_do_not_consume_retrieval_limit(store, markdown):
    stale = store.publish(TechniqueDocument(markdown()), "stale", "generated")
    with store.repository.transaction() as connection:
        connection.execute("UPDATE technique_revisions SET generation_version = NULL, pending_sync = 1 "
                           "WHERE revision_id = ?", (stale,))
    current = store.publish(TechniqueDocument(markdown()), "current", "generated")
    assert [row["revision_id"] for row in store.search("JWT", active=True, limit=1)] == [current]
    assert {row["revision_id"] for row in store.search_triggers("JWT", ["hypothesis_generation"], limit=1)} == {current}


def test_reopen_rebuilds_old_index_without_trusting_persisted_eligibility(store, markdown):
    revision = store.publish(TechniqueDocument(markdown()), "stale", "generated")
    with store.repository.transaction() as connection:
        connection.execute("UPDATE technique_revisions SET generation_version = NULL WHERE revision_id = ?",
                           (revision,))
    # Simulate an index from the previous compatibility epoch.
    store.qdrant.delete_collection(store.index)
    restored = TechniqueStore(store.qdrant, store.embeddings,
                              {"embedding_dimensions": 3, "embedding_model": "test"}, store.repository)
    assert restored.search("JWT", active=True) == []
    assert restored.render(revision)


def test_migration_keeps_existing_generated_revision_unversioned(tmp_path):
    from modules.storage.sqlite import SQLiteMigrationRunner
    from modules.techniques.repository import TechniqueRepository

    path = str(tmp_path / "legacy.db")
    migrations = SQLiteMigrationRunner._load_migrations()
    from unittest.mock import patch

    with patch.object(SQLiteMigrationRunner, "_load_migrations",
                      return_value=[row for row in migrations if row[0] < 16]):
        repository = TechniqueRepository(path)
        with repository.transaction() as connection:
            connection.execute("INSERT INTO technique_revisions "
                               "(revision_id,lineage,source_sha256,origin,active,document_collection,created_at) "
                               "VALUES ('legacy','lineage','digest','generated',1,'documents',0)")
    migrated = TechniqueRepository(path)
    assert migrated.get("legacy")["generation_version"] is None
    assert migrated.get("legacy")["source_sha256"] == "digest"
