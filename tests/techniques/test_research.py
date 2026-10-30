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
    assert WebResearch(lambda *args: "No sources")("JWT") == []

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


def test_later_batches_do_not_refetch_known_sources():
    fetched = []

    async def search_tool(**kwargs):
        return [{"url": "https://vendor.example/old"}, {"url": "https://vendor.example/new"}]

    def runner(role, prompt, tools):
        asyncio.run(tools[0](query="Explicit missing version support"))
        with pytest.raises(ValueError, match="earlier research batch"):
            tools[1](url="https://vendor.example/old")
        tools[1](url="https://vendor.example/new")

    def fetch(url):
        fetched.append(url)
        return {"url": url, "text": "New version support"}

    docs = WebResearch(runner, search_tool, fetch).batch("Missing version", {"https://vendor.example/old"})
    assert fetched == ["https://vendor.example/new"]
    assert len(docs) == 1


@pytest.mark.parametrize("technique_request", [
    "CVE-2021-44228 affected versions and validation methodology",
    "HTTP authorization comparison methodology",
    "TCP service authentication methodology",
    "Source filesystem path traversal review methodology",
])
def test_preferences_are_advisory_in_initial_and_gap_research(technique_request):
    prompts = []
    queries = []

    async def search_tool(**kwargs):
        queries.append(kwargs["query"])
        return [{"url": "https://vendor.example/advisory", "title": "Original evidence"}]

    def runner(role, prompt, tools):
        prompts.append(prompt)
        assert technique_request in prompt
        assert "preferred.example/methods" in prompt
        assert "Request-specific evidence takes priority" in prompt
        assert "Recommendations require no searches, fetches or citations" in prompt
        assert "three searches and six fetches" in prompt
        asyncio.run(tools[0](query="vendor original evidence"))
        tools[1](url="https://vendor.example/advisory")

    research = WebResearch(runner, search_tool, fetch=lambda url: {"url": url, "text": "Evidence"})
    research.preferred_sites = ["https://preferred.example/methods"]
    assert research(technique_request)[0]["url"] == "https://vendor.example/advisory"
    assert research.batch(technique_request + "; evidence gap", set())[0]["text"] == "Evidence"
    assert len(prompts) == len(queries) == 2


def test_fallback_preserves_code_and_excludes_navigation():
    parser = DocumentText()
    parser.feed("<nav>menu</nav><pre>def f(a, b):\n    return a, b</pre><footer>footer</footer>")
    assert parser.parts == ["def f(a, b):\n    return a, b"]


@pytest.mark.parametrize("article", [None, "Extracted technical guidance"])
def test_fetch_extraction_metadata_and_fallback(monkeypatch, article):
    actual_client = httpx.Client
    monkeypatch.setattr("modules.techniques.research.extract", lambda *args, **kwargs: article)
    monkeypatch.setattr(
        "modules.techniques.research.httpx.Client",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, text="<nav>menu</nav><p>Fallback &amp; guidance</p>",
                headers={"content-type": "text/html"},
            )), **kwargs,
        ),
    )
    result = fetch_document("https://example.org/article")
    assert result["text"] == (article or "Fallback & guidance")
    assert result["retained_characters"] == len(result["text"])
    assert result["extraction_method"] == ("article" if article else "whole_page")
    assert not result["truncated"]


def test_article_extractor_preserves_technical_structure():
    from trafilatura import extract

    html = (
        "<html><body><nav>Navigation noise</nav><article><h1>Technical guide</h1><p>"
        + "Technical evidence and methodology. " * 30
        + "</p><pre>def f(a, b):\n    return a, b</pre>"
        + "<ul><li>Compare controls</li></ul>"
        + "<table><tr><th>Input</th><th>Result</th></tr><tr><td>A</td><td>B</td></tr></table>"
        + "</article></body></html>"
    )
    text = extract(html, output_format="markdown", include_tables=True, include_links=False)
    assert "Navigation noise" not in text
    assert "# Technical guide" in text
    assert "def f(a, b):\n    return a, b" in text
    assert "Compare controls" in text
    assert "Input" in text and "Result" in text


def test_fallback_preserves_inline_text_and_nested_code():
    parser = DocumentText()
    parser.feed("<p>Read <b>this</b> carefully.</p><pre>def f(a, b):\n"
                "<code>    return a, b</code>\n</pre>"
                "<table><tr><td>A</td><td>B</td></tr></table>")
    text = "".join(parser.rendered)
    assert "Read this carefully." in text
    assert "def f(a, b):\n    return a, b\n" in text
    assert "A\tB\t" in text


def test_fetch_falls_back_when_article_extractor_rejects_input(monkeypatch):
    actual_client = httpx.Client

    def reject(*args, **kwargs):
        raise ValueError("Malformed article")

    monkeypatch.setattr("modules.techniques.research.extract", reject)
    monkeypatch.setattr(
        "modules.techniques.research.httpx.Client",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, text="<p>Readable fallback</p>", headers={"content-type": "text/html"},
            )), **kwargs,
        ),
    )
    result = fetch_document("https://example.org/article")
    assert result["text"] == "Readable fallback"
    assert result["extraction_method"] == "whole_page"
