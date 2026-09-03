from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.tools import memory


class FakeEmbeddings:
    def __init__(self, dimensions: int = 3):
        self.dimensions = dimensions

    def embed_query(self, _text: str):
        return [0.1] * self.dimensions


class FakeQdrant:
    def __init__(self, collection_exists: bool = False):
        self.exists = collection_exists
        self.created = []
        self.indexed = []
        self.upserts = []
        self.scroll_filter = None
        self.query_filter = None
        self.scroll_points = []
        self.retrieved = []
        self.query_results = []

    def collection_exists(self, _name):
        return self.exists

    def create_collection(self, **kwargs):
        self.created.append(kwargs)
        self.exists = True

    def create_payload_index(self, **kwargs):
        self.indexed.append(kwargs)

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def scroll(self, **kwargs):
        self.scroll_filter = kwargs["scroll_filter"]
        return self.scroll_points, None

    def retrieve(self, **_kwargs):
        return self.retrieved

    def query_points(self, **kwargs):
        self.query_filter = kwargs["query_filter"]
        return SimpleNamespace(points=self.query_results)


def build_client(
    monkeypatch, tmp_path, *, mode="operation", backend=None, dimensions=3
):
    fake = backend or FakeQdrant()
    constructor = Mock(return_value=fake)
    monkeypatch.setattr(memory, "QdrantClient", constructor)
    monkeypatch.setenv("CYBER_AGENT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setattr(
        memory, "_MEMORY_CONFIG", {"operation_id": "OP-1", "memory_mode": mode}
    )
    client = memory.QdrantMemoryClient(
        {
            "target_values": ["https://target.test"],
            "operation_id": "OP-1",
            "memory_mode": mode,
            "embedding_dimensions": dimensions,
            "embeddings": FakeEmbeddings(dimensions),
            "output_dir": str(tmp_path),
        },
        silent=True,
    )
    return client, fake, constructor


def test_local_client_creates_collection_without_ineffective_indexes(
    monkeypatch, tmp_path
):
    _client, backend, constructor = build_client(monkeypatch, tmp_path)

    constructor.assert_called_once_with(path=str(tmp_path / "qdrant"))
    assert backend.created[0]["collection_name"] == "cyber_autoagent_memories"
    assert backend.created[0]["vectors_config"].size == 3
    assert backend.indexed == []


def test_service_client_uses_url_and_api_key(monkeypatch, tmp_path):
    backend = FakeQdrant(collection_exists=True)
    constructor = Mock(return_value=backend)
    monkeypatch.setattr(memory, "QdrantClient", constructor)
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")

    memory.QdrantMemoryClient(
        {
            "target_values": ["target.example"],
            "operation_id": "OP-1",
            "memory_mode": "operation",
            "embedding_dimensions": 3,
            "embeddings": FakeEmbeddings(),
        },
        silent=True,
    )

    constructor.assert_called_once_with(url="https://qdrant.example", api_key="secret")
    assert backend.created == []
    assert {item["field_name"] for item in backend.indexed} == {
        "target_values",
        "operation_id",
        "metadata.category",
        "metadata.status",
    }


def test_store_adds_canonical_scope_payload(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path)

    result = client.store_memory(
        "observed behavior", user_id="worker", metadata={"category": "observation"}
    )

    point = backend.upserts[0]["points"][0]
    assert point.payload["target_values"] == ["https://target.test"]
    assert point.payload["operation_id"] == "OP-1"
    assert point.payload["metadata"]["operation_id"] == "OP-1"
    assert result["results"][0]["id"] == str(point.id)


def test_operation_and_shared_filters(monkeypatch, tmp_path):
    operation_client, _backend, _constructor = build_client(
        monkeypatch, tmp_path, mode="operation"
    )
    operation_filter = operation_client._scope_filter(
        {"category": ["finding", "observation"]}
    )
    assert [condition.key for condition in operation_filter.must] == [
        "target_values",
        "operation_id",
        "metadata.category",
    ]

    shared_client, _backend, _constructor = build_client(
        monkeypatch, tmp_path, mode="shared"
    )
    shared_filter = shared_client._scope_filter(operation_id="OP-IGNORED")
    assert [condition.key for condition in shared_filter.must] == ["target_values"]


def test_get_rejects_cross_target_and_cross_operation(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path)
    backend.retrieved = [
        SimpleNamespace(
            id="point-1",
            payload={
                "memory": "secret",
                "metadata": {},
                "target_values": ["other.example"],
                "operation_id": "OP-2",
            },
        )
    ]
    assert client.get_memory_by_id("point-1") is None


def test_shared_get_allows_same_target_from_another_operation(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path, mode="shared")
    backend.retrieved = [
        SimpleNamespace(
            id="point-1",
            payload={
                "memory": "reusable lesson",
                "metadata": {"category": "knowledge"},
                "target_values": ["https://target.test"],
                "operation_id": "OP-2",
            },
        )
    ]

    assert client.get_memory_by_id("point-1")["memory"] == "reusable lesson"


def test_invalid_dimension_and_filter_fail_closed(monkeypatch, tmp_path):
    client, _backend, _constructor = build_client(monkeypatch, tmp_path, dimensions=4)
    client.embeddings = FakeEmbeddings(3)
    with pytest.raises(ValueError, match="dimension mismatch"):
        client.store_memory("content")
    with pytest.raises(ValueError, match="Unsupported Qdrant metadata"):
        client._scope_filter({"nested": {"unsafe": True}})


def test_invalid_mode_and_missing_target_fail_closed(monkeypatch):
    with pytest.raises(ValueError, match="memory_mode"):
        memory.QdrantMemoryClient._memory_mode("automatic")
    with pytest.raises(ValueError, match="OperationTarget"):
        memory.QdrantMemoryClient._target_values({"target_name": "default_target"})


def test_missing_operation_id_fails_before_opening_qdrant(monkeypatch):
    qdrant = Mock()
    monkeypatch.setattr(memory, "QdrantClient", qdrant)

    with pytest.raises(ValueError, match="operation ID"):
        memory.QdrantMemoryClient(
            {
                "target_values": ["target.test"],
                "embedding_dimensions": 3,
                "embeddings": FakeEmbeddings(),
            },
            silent=True,
        )

    qdrant.assert_not_called()


def test_target_value_normalization_accepts_string_fallback_and_deduplicates():
    assert memory.QdrantMemoryClient._target_values(
        {"target_values": "target.test"}
    ) == ["target.test"]
    assert memory.QdrantMemoryClient._target_values(
        {"target_value": "fallback.test"}
    ) == ["fallback.test"]
    assert memory.QdrantMemoryClient._target_values(
        {"target_values": ["target.test", "", "target.test"]}
    ) == ["target.test"]


def test_embedding_provider_selection(monkeypatch):
    ollama = Mock(return_value="ollama-embeddings")
    gemini = Mock(return_value="gemini-embeddings")
    bedrock = Mock(return_value="bedrock-embeddings")
    monkeypatch.setattr(memory, "OllamaEmbeddings", ollama)
    monkeypatch.setattr(memory, "GoogleGenerativeAIEmbeddings", gemini)
    monkeypatch.setattr(memory, "BedrockEmbeddings", bedrock)

    assert (
        memory.QdrantMemoryClient._build_embeddings(
            SimpleNamespace(
                config={
                    "embedding_provider": "ollama",
                    "embedding_model": "embed",
                    "ollama_base_url": "x",
                }
            )
        )
        == "ollama-embeddings"
    )
    assert (
        memory.QdrantMemoryClient._build_embeddings(
            SimpleNamespace(
                config={
                    "embedding_provider": "gemini",
                    "embedding_model": "models/embed",
                }
            )
        )
        == "gemini-embeddings"
    )
    adapter = memory.QdrantMemoryClient._build_embeddings(
        SimpleNamespace(
            config={"embedding_provider": "litellm", "embedding_model": "openai/embed"}
        )
    )
    assert isinstance(adapter, memory._LiteLLMEmbeddings)
    assert (
        memory.QdrantMemoryClient._build_embeddings(
            SimpleNamespace(
                config={
                    "embedding_provider": "bedrock",
                    "embedding_model": "bedrock/amazon.embed",
                }
            )
        )
        == "bedrock-embeddings"
    )
    bedrock.assert_called_once_with(model_id="amazon.embed", region_name="us-east-1")


def test_service_index_failures_are_nonfatal(monkeypatch, tmp_path):
    backend = FakeQdrant(collection_exists=True)
    backend.create_payload_index = Mock(side_effect=RuntimeError("unsupported"))
    monkeypatch.setattr(memory, "QdrantClient", Mock(return_value=backend))
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")

    memory.QdrantMemoryClient(
        {
            "target_values": ["target.example"],
            "operation_id": "OP-1",
            "embedding_dimensions": 3,
            "embeddings": FakeEmbeddings(),
            "output_dir": str(tmp_path),
        },
        silent=True,
    )

    assert backend.create_payload_index.call_count == 4


def test_list_pagination_fetches_prefix_and_slices_page(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path)
    backend.scroll_points = [
        SimpleNamespace(
            id=f"point-{index}",
            payload={
                "memory": f"memory-{index}",
                "metadata": {},
                "target_values": ["https://target.test"],
                "operation_id": "OP-1",
            },
        )
        for index in range(4)
    ]

    result = client.list_memories(limit=2, page=2)

    assert [item["memory"] for item in result] == ["memory-2", "memory-3"]


def test_litellm_embedding_adapter_supports_object_response(monkeypatch):
    monkeypatch.setattr(
        memory.litellm,
        "embedding",
        Mock(
            return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
        ),
    )

    adapter = memory._LiteLLMEmbeddings("provider/embed-model")

    assert adapter.embed_query("text") == [0.1, 0.2]


def test_litellm_embedding_adapter_supports_mapping_response(monkeypatch):
    monkeypatch.setattr(
        memory.litellm,
        "embedding",
        Mock(return_value={"data": [{"embedding": [0.3, 0.4]}]}),
    )

    assert memory._LiteLLMEmbeddings("provider/embed").embed_query("text") == [0.3, 0.4]
    with pytest.raises(ValueError, match="model is required"):
        memory._LiteLLMEmbeddings("")


def test_empty_store_and_inactive_results(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="content is required"):
        client.store_memory(" ")
    backend.scroll_points = [SimpleNamespace(id="inactive", payload={"active": False})]
    backend.query_results = [
        SimpleNamespace(id="inactive", payload={"active": False}, score=0.1)
    ]
    assert client.list_memories() == []
    assert client.search_memories("query") == []


def test_get_missing_and_cross_operation_same_target(monkeypatch, tmp_path):
    client, backend, _constructor = build_client(monkeypatch, tmp_path)
    assert client.get_memory_by_id("missing") is None
    backend.retrieved = [
        SimpleNamespace(
            id="other-operation",
            payload={
                "memory": "other",
                "metadata": {},
                "target_values": ["https://target.test"],
                "operation_id": "OP-2",
            },
        )
    ]
    assert client.get_memory_by_id("other-operation") is None


def test_local_qdrant_round_trip_uses_target_and_operation_scope(monkeypatch, tmp_path):
    from qdrant_client.local.persistence import CollectionPersistence

    monkeypatch.delenv("QDRANT_URL", raising=False)
    # qdrant-client's thread-safety probe uses `with sqlite3.connect(...)`,
    # which commits but does not close its temporary connection.
    monkeypatch.setattr(CollectionPersistence, "CHECK_SAME_THREAD", False)
    monkeypatch.setattr(
        memory,
        "_MEMORY_CONFIG",
        {"operation_id": "OP-ROUNDTRIP", "memory_mode": "operation"},
    )
    client = memory.QdrantMemoryClient(
        {
            "target_values": ["https://roundtrip.test"],
            "operation_id": "OP-ROUNDTRIP",
            "memory_mode": "operation",
            "embedding_dimensions": 3,
            "embeddings": FakeEmbeddings(),
            "output_dir": str(tmp_path),
        },
        silent=True,
    )

    stored = client.store_memory(
        "round-trip memory", metadata={"category": "observation"}
    )
    listed = client.list_memories()
    searched = client.search("round-trip", filters={"category": "observation"})

    assert listed[0]["id"] == stored["results"][0]["id"]
    assert searched[0]["target_values"] == ["https://roundtrip.test"]
    client.qdrant.close()
