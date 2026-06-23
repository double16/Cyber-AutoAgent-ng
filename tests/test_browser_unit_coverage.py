import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from modules.tools import browser as mod


def test_format_toon_table_and_headers_and_har_body():
    rows = [{"a": "one,two", "b": "line\nbreak"}, {"a": None, "b": "ok"}]

    assert mod.format_toon_table("items", ["a", "b"], rows) == "items[2]{a,b}:\n  one;two,line break\n  ,ok"
    assert mod.format_toon_table("items", ["a"], []) == ""

    headers = {
        "Content-Type": "text/html",
        "X-Test": "1",
        "Accept": "*/*",
    }
    formatted = mod.format_headers(headers)
    assert "`Content-Type`: `text/html`" in formatted
    assert "`X-Test`: `1`" in formatted
    assert "Accept" not in formatted

    assert mod.form_har_body("text/plain", b"hello") == {
        "mimeType": "text/plain",
        "text": "hello",
        "encoding": "utf-8",
    }
    assert mod.form_har_body("application/octet-stream", b"\xff")["encoding"] == "base64"


def test_extract_domain_handles_public_and_local_domains(monkeypatch):
    values = {
        "https://www.example.co.uk/path": SimpleNamespace(domain="example", suffix="co.uk"),
        "server.orb.local": SimpleNamespace(domain="orb", suffix=""),
    }
    monkeypatch.setattr(mod.tldextract, "extract", lambda value: values[value])

    assert mod.extract_domain("https://www.example.co.uk/path") == "example.co.uk"
    assert mod.extract_domain("server.orb.local") == "orb"


@pytest.mark.asyncio
async def test_interaction_collector_delegates_summary():
    browser = SimpleNamespace(simplify_metadata_for_llm=AsyncMock(return_value="summary"))
    collector = mod.InteractionCollector(browser)
    collector.requests.append("req")
    collector.downloads.append("file")
    collector.logs.append({"type": "log", "args": []})
    collector.dialogs.append({"type": "alert", "message": "hi"})

    assert await collector.summarize() == "summary"
    browser.simplify_metadata_for_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_service_metadata_writes_logs_and_formats_sections(tmp_path):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    service.simplify_requests_for_llm = AsyncMock(return_value="requests[1]{url}:\n  https://example.com")

    summary = await service.simplify_metadata_for_llm(
        requests=["req"],
        downloads=["/tmp/file.txt"],
        logs=[{"type": "error", "args": ["bad", {"x": 1}]}],
        dialogs=[{"type": "alert", "message": "hello"}],
    )

    assert "console_logs[1]" in summary
    assert "dialogs[1]" in summary
    assert "downloaded_files[1]" in summary
    assert "requests[1]" in summary
    assert list(tmp_path.glob("logs_*.log"))


@pytest.mark.asyncio
async def test_browser_tool_wrappers_use_fake_browser(monkeypatch, tmp_path):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakeBrowserContext:
        def __init__(self):
            self.headers = None

        async def set_extra_http_headers(self, headers):
            self.headers = headers

        async def cookies(self):
            return [
                {
                    "name": "sid",
                    "value": "abc",
                    "domain": "example.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]

    class FakePage:
        async def content(self):
            return "<html>ok</html>"

        async def evaluate(self, expression):
            return {"expression": expression}

        async def observe(self, instruction):
            return [SimpleNamespace(description=f"observed {instruction}")]

    class FakeBrowser:
        def __init__(self):
            self.context = FakeBrowserContext()
            self.page = FakePage()
            self.artifacts_dir = str(tmp_path)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def run_in_browser_loop(self, fn):
            return await fn()

        def timeout(self):
            return FakeTimeout()

    fake_browser = FakeBrowser()
    monkeypatch.setattr(mod, "get_browser", lambda: fake_browser)

    assert (await mod.browser_set_headers()).startswith("No headers provided")
    assert "Applied 1" in await mod.browser_set_headers({"x-test": "1"})
    assert fake_browser.context.headers == {"x-test": "1"}
    assert "HTML content saved" in await mod.browser_get_page_html()
    assert list(tmp_path.glob("browser_page_*.html"))
    assert await mod.browser_evaluate_js("() => 1") == {"expression": "() => 1"}
    cookies_csv = await mod.browser_get_cookies()
    assert "sid,abc,example.com" in cookies_csv
    assert await mod.browser_observe_page("links") == ["observed links"]


class ElementsModel(BaseModel):
    elements: list[str]


class OtherModel(BaseModel):
    value: str


def test_llm_json_patch_helpers_and_response_format_detection():
    patch = mod.LLMClientJSONResponsePatch(SimpleNamespace(answer=1))

    assert patch.answer == 1
    assert patch.extract_json_block("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert patch.strip_js_comments('{"url": "http://x", /* c */ "a": 1 // tail\n}') == '{"url": "http://x",  "a": 1 \n}'
    assert patch.response_format_has_root_elements_model(ElementsModel) is True
    assert patch.response_format_has_root_elements_model(OtherModel) is False
    assert patch.response_format_has_root_elements_model(None) is False


@pytest.mark.asyncio
async def test_simplify_requests_for_llm_writes_har_and_network_summary(tmp_path):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    service.stagehand = SimpleNamespace(
        context=SimpleNamespace(
            browser=SimpleNamespace(
                browser_type=SimpleNamespace(name="chromium"),
                version="1.2.3",
            )
        )
    )

    class FakeResponse:
        status = 302
        status_text = "Found"

        async def all_headers(self):
            return {
                "content-type": "text/html",
                "location": "https://example.com/next",
                "set-cookie": "sid=abc; Path=/",
                "x-response": "yes",
            }

        async def body(self):
            return b"<html>redirect</html>"

    class FakeRequest:
        method = "POST"
        url = "https://example.com/login?next=%2Fadmin"
        headers = {"content-type": "application/json"}
        post_data_buffer = b'{"user":"a"}'
        timing = {
            "startTime": 1_700_000_000_000,
            "domainLookupStart": 1,
            "domainLookupEnd": 3,
            "connectStart": 3,
            "connectEnd": 5,
            "requestStart": 5,
            "responseStart": 11,
            "responseEnd": 20,
            "secureConnectionStart": 4,
        }

        async def all_headers(self):
            return {
                "content-type": "application/json",
                "cookie": "sid=abc",
                "x-request": "yes",
            }

        async def response(self):
            return FakeResponse()

    summary = await service.simplify_requests_for_llm([FakeRequest()])

    assert "network_calls[1]" in summary
    assert "`POST` `https://example.com/login?next=%2Fadmin`" in summary
    assert "Status Code: `302`" in summary
    har_files = list(tmp_path.glob("network_calls_*.har"))
    assert har_files
    har = json.loads(har_files[0].read_text())
    entry = har["log"]["entries"][0]
    assert entry["request"]["queryString"] == [{"name": "next", "value": "/admin"}]
    assert entry["request"]["cookies"][0]["name"] == "sid"
    assert entry["response"]["redirectURL"] == "https://example.com/next"


@pytest.mark.asyncio
async def test_interaction_context_capture_filters_and_unhooks(monkeypatch):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.simplify_metadata_for_llm = AsyncMock(return_value="summary")
    registered = {}
    removed = []
    service.on = lambda name, fn: registered.setdefault(name, fn)
    service.off = lambda name, fn: removed.append((name, fn))

    async def run_in_loop(fn):
        return await fn()

    service.run_in_browser_loop = run_in_loop

    async with service.interaction_context_capture(only_domains=["example.com"]) as collector:
        registered["request"](SimpleNamespace(method="GET", url="https://example.com/a"))
        registered["request"](SimpleNamespace(method="OPTIONS", url="https://example.com/skip"))
        registered["request"](SimpleNamespace(method="GET", url="https://other.test/a"))
        registered["download"]("/tmp/file")
        registered["dialog"](SimpleNamespace(type="alert", message="hi", default_value=""))

        class FakeArg:
            async def json_value(self):
                return {"x": 1}

        await registered["console"](SimpleNamespace(type="log", args=[FakeArg()]))
        assert len(collector.requests) == 1
        assert collector.downloads == ["/tmp/file"]
        assert collector.dialogs[0]["message"] == "hi"
        assert collector.logs[0]["args"] == [{"x": 1}]

    assert len(removed) == 7
