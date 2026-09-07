
from types import SimpleNamespace

import pytest
from ddgs.exceptions import RatelimitException, TimeoutException

from modules.tools import web_search as mod


class FakeEmbeddings:
    def embed_query(self, query):
        return [float(len(query)), 0.2, 0.3]


class FakeQdrant:
    def __init__(self, points=None):
        self.exists = False
        self.created = []
        self.deleted = []
        self.indexed = []
        self.queries = []
        self.upserts = []
        self.points = points or []

    def collection_exists(self, _collection_name):
        return self.exists

    def create_collection(self, **kwargs):
        self.exists = True
        self.created.append(kwargs)

    def delete(self, **kwargs):
        self.deleted.append(kwargs)

    def create_payload_index(self, **kwargs):
        self.indexed.append(kwargs)

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        return SimpleNamespace(points=self.points)

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


def build_cache(monkeypatch, points=None, *, model="embedding-v1"):
    backend = FakeQdrant(points)
    memory_client = SimpleNamespace(
        config={"embedding_provider": "test", "embedding_model": model},
        embedding_dimensions=3,
        embeddings=FakeEmbeddings(),
        qdrant=backend,
        qdrant_url="",
    )
    monkeypatch.setattr(mod.memory_tools, "_MEMORY_CLIENT", memory_client)
    return mod.SemanticWebSearchCache(), backend


def test_search_duckduckgo_maps_ddgs_results(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def text(self, query, backend, max_results):
            assert query == "python"
            assert backend == "brave,duckduckgo"
            assert max_results == 2
            return [
                {"title": "Python", "href": "https://python.org", "body": "Language"},
                {"title": "Docs", "href": "https://docs.python.org", "body": "Docs"},
            ]

    monkeypatch.setattr(mod, "DDGS", FakeDDGS)

    hits = mod.search_duckduckgo("python", 2)

    assert [hit.title for hit in hits] == ["Python", "Docs"]
    assert hits[0].url == "https://python.org"


@pytest.mark.asyncio
async def test_with_backoff_retries_transient_errors(monkeypatch):
    calls = []

    def flaky(query, limit):
        calls.append((query, limit))
        if len(calls) == 1:
            raise RatelimitException("429")
        return [mod.WebSearchHit(title="ok", url="https://example.com", snippet="done")]

    async def fast_sleep(*_):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(mod.random, "uniform", lambda *_: 0)

    result = await mod.with_backoff(flaky, retries=2, base=0.1)("q", 1)

    assert len(calls) == 2
    assert result[0].snippet == "done"


@pytest.mark.asyncio
async def test_with_backoff_does_not_retry_permanent_errors():
    calls = 0

    def permanent(*_):
        nonlocal calls
        calls += 1
        raise ValueError("bad query")

    with pytest.raises(ValueError):
        await mod.with_backoff(permanent, retries=3)("q", 1)

    assert calls == 1


@pytest.mark.asyncio
async def test_with_backoff_retries_empty_results_then_raises_timeout(monkeypatch):
    async def fast_sleep(*_):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(mod.random, "uniform", lambda *_: 0)

    with pytest.raises(TimeoutException, match="no results"):
        await mod.with_backoff(lambda *_: [], retries=1, base=0.1)("q", 1)


@pytest.mark.asyncio
async def test_web_search_clamps_limit_and_returns_dicts(monkeypatch):
    async def fake_search(query, limit):
        assert query == "x"
        assert limit == 50
        return [mod.WebSearchHit(title="T", url="U", snippet="S")]

    monkeypatch.setattr(mod, "with_backoff", lambda fn: fake_search)

    assert await mod.web_search("x", limit=500) == [{"title": "T", "url": "U", "snippet": "S"}]


@pytest.mark.asyncio
async def test_web_search_clamps_low_limits_and_retries_transient_message_errors(monkeypatch):
    calls = []

    def temporarily_unavailable(*_args):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("temporarily unavailable")
        return [mod.WebSearchHit(title="T", url="U", snippet="S")]

    async def fast_sleep(*_args):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(mod.random, "uniform", lambda *_args: 0)
    assert await mod.with_backoff(temporarily_unavailable, retries=1, base=0.1)("q", 1)

    async def fake_search(query, limit):
        assert query == "x"
        assert limit == 1
        return [mod.WebSearchHit(title="T", url="U", snippet="S")]

    monkeypatch.setattr(mod, "with_backoff", lambda _fn: fake_search)
    assert await mod.web_search("x", limit=0) == [{"title": "T", "url": "U", "snippet": "S"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Authorization: Bearer secret-token-value CVE guidance",
        "api_key=secret-value vendor advisory",
        "-----BEGIN PRIVATE KEY-----",
        "contact alice@example.test exploit mitigation",
        "SSN 123-45-6789 incident response",
        "card 4111 1111 1111 1111 breach response",
    ],
)
async def test_web_search_blocks_sensitive_queries_before_calling_provider(query):
    calls = []

    async def provider(received_query, limit):
        calls.append((received_query, limit))
        return []

    tool = mod.create_web_search_tool(provider)

    with pytest.raises(mod.SensitiveWebSearchQueryError) as error:
        await tool(query, limit=500)

    assert str(error.value) == mod.SENSITIVE_QUERY_BLOCKED_MESSAGE
    assert "secret-token-value" not in str(error.value)
    assert calls == []


@pytest.mark.asyncio
async def test_web_search_wrapper_has_stable_schema_and_delegates_safe_query():
    calls = []

    async def provider(query, limit):
        calls.append((query, limit))
        return {"provider": "test"}

    tool = mod.create_web_search_tool(provider)

    assert tool.tool_name == "web_search"
    assert await tool("CVE-2026-1234 remediation", limit=500) == {"provider": "test"}
    assert calls == [("CVE-2026-1234 remediation", 50)]


@pytest.mark.asyncio
async def test_web_search_reuses_high_confidence_semantic_cache_hit(monkeypatch):
    cached_response = [
        {"title": "Cached", "url": "https://cached.test", "snippet": "cached"},
        {"title": "Second", "url": "https://second.test", "snippet": "second"},
    ]
    cache, backend = build_cache(
        monkeypatch,
        [SimpleNamespace(score=0.95, payload={"result": cached_response, "result_count": 2})],
    )
    calls = []

    async def provider(query, limit):
        calls.append((query, limit))
        return []

    tool = mod.create_web_search_tool(provider, cache=cache)

    assert await tool("related vulnerability research", limit=1) == cached_response[:1]
    assert calls == []
    assert backend.queries[0]["query_filter"].must[0].key == "expires_at"
    assert backend.deleted


@pytest.mark.asyncio
async def test_web_search_refreshes_below_threshold_or_insufficient_semantic_results(monkeypatch):
    cache, backend = build_cache(
        monkeypatch,
        [SimpleNamespace(score=0.95, payload={"result": [{"title": "old"}], "result_count": 1})],
    )
    calls = []

    async def provider(query, limit):
        calls.append((query, limit))
        return [{"title": "fresh"}, {"title": "second"}]

    tool = mod.create_web_search_tool(provider, cache=cache)

    assert await tool("related research", limit=2) == [{"title": "fresh"}, {"title": "second"}]
    assert calls == [("related research", 2)]
    assert len(backend.upserts) == 1

    backend.points = [SimpleNamespace(score=0.919, payload={"result": [{"title": "old"}], "result_count": 2})]
    assert await tool("another related research", limit=1) == [{"title": "fresh"}, {"title": "second"}]
    assert calls[-1] == ("another related research", 1)


def test_semantic_cache_uses_distinct_query_ids_and_model_namespaces(monkeypatch):
    cache, backend = build_cache(monkeypatch)

    cache.store("CVE guidance", [{"title": "result"}])
    cache.store("cve guidance", [{"title": "result"}])

    assert len(backend.upserts) == 2
    point_ids = [call["points"][0].id for call in backend.upserts]
    assert point_ids[0] != point_ids[1]
    first_namespace = backend.upserts[0]["collection_name"]

    alternate_cache, alternate_backend = build_cache(monkeypatch, model="embedding-v2")
    alternate_cache.store("CVE guidance", [{"title": "result"}])
    assert alternate_backend.upserts[0]["collection_name"] != first_namespace


def test_semantic_cache_handles_unavailable_memory_and_remote_expiry_index(monkeypatch):
    cache = mod.SemanticWebSearchCache()
    monkeypatch.setattr(mod.memory_tools, "_MEMORY_CLIENT", None)
    assert cache.lookup("CVE guidance", 1) is None
    cache.store("CVE guidance", object())

    _cache, backend = build_cache(monkeypatch)
    memory_client = mod.memory_tools._MEMORY_CLIENT
    memory_client.qdrant_url = "https://qdrant.example"
    collection_name = _cache._model_namespace(memory_client)
    _cache._ensure_collection(memory_client, collection_name)
    _cache.store("non-serializable", object())

    assert backend.indexed[0]["field_name"] == "expires_at"
    assert backend.upserts == []
    assert _cache._limit_result({"results": [{"title": "one"}, {"title": "two"}]}, 1) == {
        "results": [{"title": "one"}]
    }


@pytest.mark.asyncio
async def test_web_search_cache_failures_fall_back_to_provider(monkeypatch):
    class FailingCache:
        def lookup(self, *_args):
            raise RuntimeError("Qdrant unavailable")

        def store(self, *_args):
            raise RuntimeError("Qdrant unavailable")

    calls = []

    async def provider(query, limit):
        calls.append((query, limit))
        return [{"title": "live"}]

    tool = mod.create_web_search_tool(provider, cache=FailingCache())

    assert await tool("CVE guidance", limit=1) == [{"title": "live"}]
    assert calls == [("CVE guidance", 1)]


@pytest.mark.asyncio
async def test_sensitive_web_search_does_not_access_semantic_cache():
    class TrackingCache:
        def __init__(self):
            self.calls = []

        def lookup(self, *_args):
            self.calls.append("lookup")

        def store(self, *_args):
            self.calls.append("store")

    cache = TrackingCache()

    async def provider(*_args):
        raise AssertionError("Sensitive query must not call provider")

    tool = mod.create_web_search_tool(provider, cache=cache)

    with pytest.raises(mod.SensitiveWebSearchQueryError):
        await tool("api_key=secret-value vendor advisory")

    assert cache.calls == []
