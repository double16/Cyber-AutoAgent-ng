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
