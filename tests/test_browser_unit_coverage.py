import asyncio
import json
from types import SimpleNamespace
from typing import Optional
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
    service.stagehand = SimpleNamespace(
        context=SimpleNamespace(
            browser=SimpleNamespace(
                browser_type=SimpleNamespace(name="chromium"),
                version="1.0",
            )
        )
    )
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
async def test_browser_service_metadata_truncates_long_log_and_dialog(tmp_path):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    summary = await service.simplify_metadata_for_llm(
        requests=[],
        downloads=[],
        logs=[{"type": "debug", "args": ["x" * 300]}],
        dialogs=[{"type": "alert", "message": "y" * 300}],
    )
    assert "..." in summary


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

    class V1Elements:
        __fields__ = {"elements": object()}

    class Plain:
        pass

    assert patch.answer == 1
    assert patch.extract_json_block("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert patch.strip_js_comments('{"url": "http://x", /* c */ "a": 1 // tail\n}') == '{"url": "http://x",  "a": 1 \n}'
    assert patch.response_format_has_root_elements_model(ElementsModel) is True
    assert patch.response_format_has_root_elements_model(OtherModel) is False
    assert patch.response_format_has_root_elements_model(None) is False
    assert patch.response_format_has_root_elements_model(Optional[ElementsModel]) is True  # noqa: UP045
    assert patch.response_format_has_root_elements_model(Optional[str]) is False  # noqa: UP045
    assert patch.response_format_has_root_elements_model(42) is False
    assert patch.response_format_has_root_elements_model(V1Elements) is True
    assert patch.response_format_has_root_elements_model(Plain) is False


@pytest.mark.asyncio
async def test_llm_json_patch_create_response_without_schema_and_metadata_empty(tmp_path):
    inner = SimpleNamespace(create_response=AsyncMock(return_value={"ok": True}))
    patch = mod.LLMClientJSONResponsePatch(inner)
    result = await patch.create_response(messages=[], response_format="json")
    assert result == {"ok": True}
    assert inner.create_response.await_args.kwargs["messages"][0]["content"].endswith("json")

    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    service.simplify_requests_for_llm = AsyncMock(return_value="")
    assert await service.simplify_metadata_for_llm([], [], [], []) == ""
    service.simplify_requests_for_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_json_patch_normalizes_valid_content_and_preserves_invalid_responses():
    class Response(dict):
        def __init__(self, choices):
            super().__init__(choices=choices)
            self.choices = choices

    valid_choice = SimpleNamespace(message=SimpleNamespace(content="```json\n[\"one\"]\n```"))
    invalid_choice = SimpleNamespace(message=SimpleNamespace(content="not json"))
    inner = SimpleNamespace(
        create_response=AsyncMock(return_value=Response([valid_choice, invalid_choice]))
    )
    patch = mod.LLMClientJSONResponsePatch(inner)

    response = await patch.create_response(
        messages=[{"role": "user", "content": "hello"}],
        model="test",
        response_format=ElementsModel,
    )

    assert json.loads(valid_choice.message.content) == {"elements": ["one"]}
    assert invalid_choice.message.content == "not json"
    assert response.choices == [valid_choice, invalid_choice]
    assert inner.create_response.await_args.kwargs["messages"][1]["role"] == "system"

    passthrough = await patch.create_response(messages=[], model="test")
    assert passthrough is response


@pytest.mark.asyncio
async def test_llm_json_patch_handles_empty_and_non_list_choices_without_rewriting():
    class Response(dict):
        def __init__(self, choices):
            super().__init__(choices=choices)
            self.choices = choices

    inner = SimpleNamespace(create_response=AsyncMock())
    patch = mod.LLMClientJSONResponsePatch(inner)
    no_choices = {}
    non_list_choices = Response("not-a-list")
    empty_choices = Response([])

    for response in (no_choices, non_list_choices, empty_choices):
        inner.create_response.return_value = response
        assert await patch.create_response(messages=[], response_format=ElementsModel) is response

    assert patch.extract_json_block("```json\ninvalid\n```") == "invalid"
    assert patch.response_format_has_root_elements_model(list[ElementsModel]) is True


@pytest.mark.asyncio
async def test_browser_goto_url_uses_http_fallback_after_non_retriable_navigation_error(monkeypatch, tmp_path):
    class Timeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class ApiResponse:
        status = 403
        headers = {"server": "cloudflare", "x-test": "present"}

        async def text(self):
            return "Cloudflare challenge"

    class RequestClient:
        async def get(self, *_args, **_kwargs):
            return ApiResponse()

    class Browser:
        artifacts_dir = str(tmp_path)
        context = SimpleNamespace(request=RequestClient())
        page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError("blocked by target")))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return Timeout()

        async def run_in_browser_loop(self, function):
            return await function()

        async def reset(self):
            raise AssertionError("non-retriable failures must use fallback without a reset")

    monkeypatch.setattr(mod, "get_browser", lambda: Browser())

    result = await mod.browser_goto_url("https://example.com/path")

    assert "HTTP fallback executed" in result
    assert "Detected Cloudflare/WAF indicators" in result
    assert len(list(tmp_path.glob("http_fallback_*.txt"))) == 3


@pytest.mark.asyncio
async def test_browser_loop_serializes_and_allows_nested_operations_on_its_own_loop():
    service = mod.BrowserService.__new__(mod.BrowserService)
    service._loop = asyncio.get_running_loop()
    service._op_lock = None
    service._active_ops = 0
    service._active_ops_peak = 0
    service._active_ops_violations = 0

    async def nested():
        return "nested"

    async def outer():
        return await service.run_in_browser_loop(nested)

    assert await service.run_in_browser_loop(outer) == "nested"
    assert service._active_ops_peak == 1
    assert service._active_ops_violations == 0

    service._loop = None
    with pytest.raises(RuntimeError, match="not initialized"):
        await service.run_in_browser_loop(nested)


@pytest.mark.asyncio
async def test_initialize_browser_merges_default_headers_and_get_browser_requires_init(monkeypatch):
    created = {}

    class StubBrowser:
        def __init__(self, provider, model, artifacts_dir, headers):
            created.update(provider=provider, model=model, artifacts_dir=artifacts_dir, headers=headers)

    monkeypatch.setattr(mod, "BrowserService", StubBrowser)
    monkeypatch.setenv("CYBER_BROWSER_DEFAULT_HEADERS", "true")
    browser = mod.initialize_browser("ollama", "model", "/tmp/artifacts", {"User-Agent": "custom"})
    assert browser is mod._BROWSER
    assert created["headers"]["user-agent"] == "custom"
    assert "accept-language" in created["headers"]

    mod._BROWSER = None

    with pytest.raises(ValueError, match="Browser not initialized"):
        async with mod.get_browser():
            pass


@pytest.mark.asyncio
async def test_browser_ensure_init_configures_stagehand_and_registers_events(tmp_path):
    events = []

    class Context:
        def set_default_timeout(self, value):
            events.append(("timeout", value))

        def set_default_navigation_timeout(self, value):
            events.append(("navigation", value))

    class Page:
        def set_default_timeout(self, value):
            events.append(("page_timeout", value))

        def set_default_navigation_timeout(self, value):
            events.append(("page_navigation", value))

        def on(self, name, callback):
            events.append(("on", name, callback))

    stagehand = SimpleNamespace(
        init=AsyncMock(),
        context=Context(),
        page=Page(),
    )
    service = mod.BrowserService.__new__(mod.BrowserService)
    service._initialized = False
    service.stagehand = stagehand
    service.default_timeout = 50.0
    service.artifacts_dir = str(tmp_path)
    service.run_in_browser_loop = lambda function: function()
    service.emit_async = AsyncMock()

    await service.ensure_init()
    await service.ensure_init()

    stagehand.init.assert_awaited_once()
    assert service._initialized is True
    assert [item[1] for item in events if item[0] == "on"] == [
        "dialog", "download", "request", "response", "requestfailed", "requestfinished", "console"
    ]


@pytest.mark.asyncio
async def test_browser_reset_handles_close_failure_and_uninitialized_state():
    service = mod.BrowserService.__new__(mod.BrowserService)
    service._initialized = True
    service.stagehand = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    service.run_in_browser_loop = lambda function: function()

    await service.reset()
    assert service._initialized is False
    await service.reset()
    mod._BROWSER = None
    mod.close_browser()


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
async def test_simplify_requests_handles_request_without_response_or_body(tmp_path):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    service.stagehand = SimpleNamespace(
        context=SimpleNamespace(
            browser=SimpleNamespace(
                browser_type=SimpleNamespace(name="chromium"),
                version="1.0",
            )
        )
    )

    class NoResponseRequest:
        method = "GET"
        url = "https://example.test/path"
        headers = {}
        post_data_buffer = b""
        timing = {
            "startTime": 1_700_000_000_000,
            "domainLookupStart": -1,
            "domainLookupEnd": -1,
            "connectStart": -1,
            "connectEnd": -1,
            "requestStart": -1,
            "responseStart": -1,
            "responseEnd": -1,
            "secureConnectionStart": -1,
        }

        async def all_headers(self):
            return {}

        async def response(self):
            return None

    summary = await service.simplify_requests_for_llm([NoResponseRequest()])
    assert "No Response was received" in summary
    assert "network_calls[1]" in summary


@pytest.mark.asyncio
async def test_simplify_requests_handles_response_timeout(tmp_path):
    service = mod.BrowserService.__new__(mod.BrowserService)
    service.artifacts_dir = str(tmp_path)
    service.stagehand = SimpleNamespace(
        context=SimpleNamespace(
            browser=SimpleNamespace(
                browser_type=SimpleNamespace(name="chromium"),
                version="1.0",
            )
        )
    )

    class TimeoutRequest:
        method = "GET"
        url = "https://example.test/slow"
        headers = {}
        post_data_buffer = None
        timing = {
            "startTime": 1,
            "domainLookupStart": -1,
            "domainLookupEnd": -1,
            "connectStart": -1,
            "connectEnd": -1,
            "requestStart": -1,
            "responseStart": -1,
            "responseEnd": -1,
            "secureConnectionStart": -1,
        }

        async def all_headers(self):
            return {}

        async def response(self):
            raise TimeoutError

    assert "No Response was received" in await service.simplify_requests_for_llm([TimeoutRequest()])


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
