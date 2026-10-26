import asyncio
import ssl

import httpx
import pytest

from modules.techniques.research import DocumentText, WebResearch, fetch_document


def test_text_extraction_omits_executable_content():
    parser = DocumentText()
    parser.feed("<h1>Guidance</h1><script>bad()</script><style>hidden</style><p>Controls</p>")
    assert parser.parts == ["Guidance", "Controls"]


def test_fetch_document_honors_proxy_configuration_and_bounds_text(monkeypatch):
    actual_client = httpx.Client
    captured = []

    def client(**kwargs):
        captured.append(kwargs)
        return actual_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text="<p>" + "guidance " * 4000 + "</p>",
                    headers={"content-type": "text/html"},
                )
            ),
            **kwargs,
        )

    monkeypatch.setattr("modules.techniques.research.httpx.Client", client)
    document = fetch_document("https://vendor.example/advisory")
    assert document["truncated"]
    assert len(document["text"]) == 16_000
    assert "retrieved_at" in document
    assert captured[0]["trust_env"] is True
    assert isinstance(captured[0]["verify"], ssl.SSLContext)
    assert captured[0]["verify"].verify_mode == ssl.CERT_REQUIRED
    assert captured[0]["verify"].check_hostname


@pytest.mark.parametrize(
    "body,content_type,status",
    [
        ("", "text/plain", 200),
        ("binary", "application/pdf", 200),
        ("x" * 2_000_001, "text/plain", 200),
        ("missing", "text/plain", 404),
    ],
)
def test_fetch_failure_is_explicit(monkeypatch, body, content_type, status):
    actual_client = httpx.Client
    monkeypatch.setattr(
        "modules.techniques.research.httpx.Client",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status,
                    text=body,
                    headers={"content-type": content_type},
                )
            ),
            **kwargs,
        ),
    )
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        fetch_document("https://vendor.example/advisory")


def test_research_agent_must_fetch_discovered_sources_and_is_bounded():
    async def search_tool(**kwargs):
        return [{"url": f"https://vendor.example/{number}", "title": f"Source {number}"} for number in range(7)]

    def runner(role, prompt, tools):
        assert role == "technique_researcher"
        search, read = tools
        with pytest.raises(ValueError, match="must come"):
            read(url="https://undiscovered.example")
        for _ in range(3):
            asyncio.run(search(query="Generic JWT methods"))
        with pytest.raises(ValueError, match="search allowance"):
            asyncio.run(search(query="Again"))
        for number in range(6):
            assert read(url=f"https://vendor.example/{number}")["text"] == "Document"
        assert read(url="https://vendor.example/0")["text"] == "Document"
        with pytest.raises(ValueError, match="fetch allowance"):
            read(url="https://vendor.example/6")

    research = WebResearch(runner, search_tool, fetch=lambda url: {"url": url, "text": "Document"})
    assert len(research("JWT handling")) == 6


def test_research_rejects_empty_or_malformed_results():
    with pytest.raises(ValueError, match="any usable"):
        WebResearch(lambda *args: "No sources")("JWT")

    async def search_tool(**kwargs):
        return {"results": "invalid"}

    def runner(role, prompt, tools):
        asyncio.run(tools[0](query="JWT"))

    with pytest.raises(ValueError, match="result list"):
        WebResearch(runner, search_tool)("JWT")


def test_search_dictionary_results_without_valid_url_are_not_fetchable():
    async def search_tool(**kwargs):
        return {"results": [{"href": "https://vendor.example/doc"}, {"url": "file:///bad"}]}

    def runner(role, prompt, tools):
        asyncio.run(tools[0](query="JWT"))
        tools[1](url="https://vendor.example/doc")

    docs = WebResearch(runner, search_tool, fetch=lambda url: {"url": url, "text": "Guide"})("JWT")
    assert docs[0]["title"] == "https://vendor.example/doc"


@pytest.mark.parametrize(
    "serialized",
    [
        '{"results": [{"url": "https://vendor.example/doc", "title": "Source"}]}',
        "{'results': [{'url': 'https://vendor.example/doc', 'title': 'Source'}], 'ok': True}",
    ],
)
def test_tavily_strands_envelopes_are_normalized(serialized):
    async def search_tool(**kwargs):
        return {"status": "success", "content": [{"text": serialized}]}

    def runner(role, prompt, tools):
        asyncio.run(tools[0](query="JWT"))
        tools[1](url="https://vendor.example/doc")

    assert (
        WebResearch(runner, search_tool, fetch=lambda url: {"url": url, "text": "Guide"})("JWT")[0]["title"] == "Source"
    )


def test_standalone_research_uses_configured_search_provider_and_shared_qdrant_cache(store, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from modules.techniques.research import default_search_tool
    from modules.tools.web_search import SemanticWebSearchCache

    backend = SimpleNamespace(
        qdrant=store.qdrant,
        embeddings=store.embeddings,
        embedding_dimensions=3,
        config={"embedding_model": "test"},
        qdrant_url="",
    )
    cache = SemanticWebSearchCache(backend)
    result = [{"url": "https://vendor.example/doc", "title": "Source", "snippet": "Method"}]
    cache.store("JWT validation guidance", result)
    assert cache.lookup("JWT validation guidance", 1) == result
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    provider = AsyncMock(return_value=result)
    monkeypatch.setattr("strands_tools.tavily.tavily_search", provider)
    search = default_search_tool()
    assert asyncio.run(search(query="Generic validation guidance", limit=25)) == result
    provider.assert_awaited_once_with(query="Generic validation guidance", max_results=20)


def test_tls_failure_stops_further_research_and_surfaces_actionable_error():
    fetched = []
    searches = []

    async def search_tool(**kwargs):
        searches.append(kwargs)
        return [{"url": "https://vendor.example/doc"}]

    def fetch(url):
        fetched.append(url)
        try:
            raise ssl.SSLCertVerificationError("untrusted issuer")
        except ssl.SSLCertVerificationError as error:
            raise httpx.ConnectError("TLS connection failed") from error

    def runner(role, prompt, tools):
        search, read = tools
        asyncio.run(search(query="generic guidance"))
        with pytest.raises(ValueError, match="system/proxy CA trust"):
            read(url="https://vendor.example/doc")
        with pytest.raises(ValueError, match="SSL_CERT_FILE/SSL_CERT_DIR"):
            asyncio.run(search(query="retry"))
        with pytest.raises(ValueError, match="certificate verification failed"):
            read(url="https://vendor.example/doc")

    with pytest.raises(ValueError, match="certificate verification failed"):
        WebResearch(runner, search_tool, fetch)("JWT")
    assert len(fetched) == len(searches) == 1


def test_non_certificate_connection_failure_is_preserved():
    async def search_tool(**kwargs):
        return [{"url": "https://vendor.example/doc"}]

    def fetch(url):
        raise httpx.ConnectError("connection refused")

    def runner(role, prompt, tools):
        asyncio.run(tools[0](query="guidance"))
        tools[1](url="https://vendor.example/doc")

    with pytest.raises(httpx.ConnectError, match="connection refused"):
        WebResearch(runner, search_tool, fetch)("JWT")
