"""Global Qdrant documents and rebuildable model-specific semantic indexes."""

import hashlib
import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from modules.storage.qdrant import QDRANT_WRITE_LOCK
from modules.techniques.documents import NAMESPACE, TechniqueDocument
from modules.techniques.repository import TechniqueRepository

DOCUMENTS = "cyber_autoagent_technique_documents"
WRITE_LOCK = QDRANT_WRITE_LOCK


class TechniqueStore:
    """Share the operation's physical Qdrant client without inheriting its scope."""

    def __init__(self, qdrant: QdrantClient, embeddings: Any, config: dict, repository: TechniqueRepository) -> None:
        self.qdrant = qdrant
        self.embeddings = embeddings
        self.repository = repository
        self.dimensions = int(config["embedding_dimensions"])
        identity = f"{config.get('embedding_provider')}:{config.get('embedding_model')}:{self.dimensions}"
        self.index = f"cyber_autoagent_technique_index_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        self._indexed: set[str] = set()
        with WRITE_LOCK:
            if not qdrant.collection_exists(DOCUMENTS):
                qdrant.create_collection(DOCUMENTS, vectors_config={})
            if not qdrant.collection_exists(self.index):
                qdrant.create_collection(
                    self.index,
                    vectors_config=models.VectorParams(size=self.dimensions, distance=models.Distance.COSINE),
                )
            self.reconcile()

    def document(self, revision: str) -> TechniqueDocument:
        points = self.qdrant.retrieve(DOCUMENTS, ids=[revision], with_payload=True)
        if not points:
            raise ValueError("Technique document unavailable in Qdrant")
        return TechniqueDocument(points[0].payload["source_markdown"])

    def lifecycle(self, row: dict) -> dict:
        return {
            "revision_id": row["revision_id"],
            "lineage": row["lineage"],
            "origin": row["origin"],
            "source_sha256": row["source_sha256"],
            "active": bool(row["active"]),
            "inactive_reason": row["inactive_reason"],
            "sentiment_count": row["score_count"],
            "sentiment_mean": row["score_total"] / row["score_count"] if row["score_count"] else 0,
            "feedback_reasons": self.repository.feedback_reasons(row["revision_id"]),
        }

    def render(self, revision: str, *, require_active: bool = True) -> str:
        row = self.repository.get(revision)
        if row is None or (require_active and not row["active"]):
            raise ValueError("Technique is unavailable or inactive")
        return self.document(revision).render(self.lifecycle(row))

    def publish(self, document: TechniqueDocument, lineage: str, origin: str) -> str:
        from modules.techniques.documents import revision_id

        revision = revision_id(lineage, document.sha256)
        with WRITE_LOCK:
            if self.repository.get(revision):
                return revision
            # Stage content and vectors before changing the authoritative current revision.
            self.qdrant.upsert(
                DOCUMENTS,
                points=[
                    models.PointStruct(
                        id=revision,
                        vector={},
                        payload={"source_markdown": document.markdown, "active": False},
                    )
                ],
                wait=True,
            )
            self._index(revision, document, False, False)
            self.repository.publish(
                revision,
                lineage,
                document.sha256,
                origin,
                document.metadata.active,
                DOCUMENTS,
            )
            self.reconcile()
        return revision

    def _vector(self, text: str) -> list[float]:
        vector = list(self.embeddings.embed_query(text))
        if len(vector) != self.dimensions:
            raise ValueError("Technique embedding dimension mismatch")
        return vector

    def _index(self, revision: str, document: TechniqueDocument, active: bool, current: bool) -> None:
        points = [
            models.PointStruct(
                id=revision,
                vector=self._vector(document.markdown),
                payload={"kind": "document", "revision_id": revision, "active": active, "current": current},
            )
        ]
        for number, trigger in enumerate(document.metadata.triggers):
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid5(NAMESPACE, f"{revision}:trigger:{number}")),
                    vector=self._vector(f"{trigger.when}\n{trigger.request}"),
                    payload={
                        "kind": "trigger",
                        "revision_id": revision,
                        "active": active,
                        "current": current,
                        **trigger.model_dump(),
                    },
                )
            )
        self.qdrant.upsert(self.index, points=points, wait=True)
        self._indexed.add(revision)

    def reconcile(self) -> None:
        """Repair interrupted lifecycle writes and rebuild indexes after embedding changes."""
        for row in self.repository.revisions():
            revision = row["revision_id"]
            if not row["pending_sync"]:
                if revision in self._indexed:
                    continue
                if self.qdrant.retrieve(self.index, ids=[revision], with_payload=False):
                    self._indexed.add(revision)
                    continue
            document = self.document(revision)
            self._index(revision, document, bool(row["active"]), bool(row["current_revision"]))
            self.qdrant.set_payload(
                DOCUMENTS,
                payload={
                    **self.lifecycle(row),
                    "document_markdown": document.render(self.lifecycle(row)),
                },
                points=[revision],
                wait=True,
            )
            self.repository.synced(revision, DOCUMENTS)

    def search(self, request: str, *, active: bool, limit: int = 5) -> list[dict]:
        with WRITE_LOCK:
            self.reconcile()
            result = self.qdrant.query_points(
                self.index,
                query=self._vector(request),
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="kind", match=models.MatchValue(value="document")),
                        models.FieldCondition(key="active", match=models.MatchValue(value=active)),
                    ]
                ),
                limit=limit,
                with_payload=True,
            )
            rows = []
            for point in result.points:
                revision = point.payload["revision_id"]
                row = self.repository.get(revision)
                if row is not None and bool(row["active"]) == active:
                    rows.append({"revision_id": revision, "document": self.render(revision, require_active=False)})
            return rows

    def triggers(self, contexts: list[str]) -> list[dict]:
        """Include inactive current advertisements so a lookup can regenerate guidance."""
        if not contexts:
            return []
        with WRITE_LOCK:
            self.reconcile()
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(key="kind", match=models.MatchValue(value="trigger")),
                    models.FieldCondition(key="current", match=models.MatchValue(value=True)),
                    models.FieldCondition(key="phases", match=models.MatchAny(any=contexts)),
                ]
            )
            result = []
            offset = None
            while True:
                page, offset = self.qdrant.scroll(
                    self.index,
                    scroll_filter=query_filter,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                result.extend(page)
                if offset is None:
                    break
        return [
            {"trigger_id": str(point.id), **point.payload}
            for point in result
            if (row := self.repository.get(point.payload["revision_id"])) and row["current_revision"]
        ]
