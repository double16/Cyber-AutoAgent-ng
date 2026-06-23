import asyncio

import pytest
from ddgs.exceptions import RatelimitException

from modules.tools import web_search as mod


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
async def test_web_search_clamps_limit_and_returns_dicts(monkeypatch):
    async def fake_search(query, limit):
        assert query == "x"
        assert limit == 50
        return [mod.WebSearchHit(title="T", url="U", snippet="S")]

    monkeypatch.setattr(mod, "with_backoff", lambda fn: fake_search)

    assert await mod.web_search("x", limit=500) == [{"title": "T", "url": "U", "snippet": "S"}]
