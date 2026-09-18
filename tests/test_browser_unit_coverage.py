import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from stagehand.schemas import ActResult

from modules.tools import browser as mod


class ForeignActResult:
    def __init__(self, success: bool, message: str, action: str):
        self.success = success
        self.message = message
        self.action = action


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


def test_page_change_summary_ignores_script_and_whitespace_but_reports_visible_changes():
    unchanged = mod._page_change_summary(
        "<main>Hello</main><script>loading = true</script>",
        "<main>  Hello </main><script>loading = false</script>",
    )
    changed = mod._page_change_summary("<main>Register</main>", "<main>Welcome</main>")

    assert unchanged["changed"] is False
    assert changed["changed"] is True
    assert changed["before_preview"] == "Register"
    assert changed["after_preview"] == "Welcome"


@pytest.mark.asyncio
async def test_browser_action_waits_for_page_change_and_reports_result(monkeypatch):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        def __init__(self):
            self.content_value = "<main>Register</main>"
            self.wait_timeout = None

        async def content(self):
            return self.content_value

        async def act(self, action):
            assert action == "Click register"
            self.content_value = "<main>Registration complete</main>"
            return ActResult(success=True, message="Action performed", action="click")

        async def wait_for_load_state(self, state, timeout):
            assert state == "networkidle"
            self.wait_timeout = timeout

        async def observe(self, _instruction):
            return [SimpleNamespace(description="registration complete")]

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return []
            if "return state.events" in expression:
                return []
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[],
                logs=[],
                dialogs=[],
                downloads=[],
                summarize=AsyncMock(return_value="network summary"),
            )

    fake_browser = FakeBrowser()
    monkeypatch.setattr(mod, "get_browser", lambda: fake_browser)

    result = await mod.browser_perform_action("Click register", wait_for_page_change=True)

    assert fake_browser.page.wait_timeout == 10_000
    assert '"changed": true' in result
    assert '"timed_out": false' in result
    assert "network summary" in result


def test_normalize_action_result_accepts_protocol_shapes_and_rejects_malformed_results():
    foreign_failure = mod._normalize_action_result(
        ForeignActResult(False, "No observe results found for action Enter SecretValue", "Enter SecretValue")
    )
    mapping_success = mod._normalize_action_result({"success": True, "message": "performed"})
    malformed = mod._normalize_action_result({"success": "true", "message": "Enter SecretValue"})

    assert foreign_failure.recognized is True
    assert foreign_failure.success is False
    assert foreign_failure.failure_reason == "no_actionable_element"
    assert foreign_failure.type_name == "ForeignActResult"
    assert mapping_success.recognized is True
    assert mapping_success.success is True
    assert malformed.recognized is False
    assert malformed.failure_reason == "unexpected_action_result"


def test_stagehand_json_patch_removes_terminal_formatting_before_json_repair():
    patch = mod.LLMClientJSONResponsePatch(SimpleNamespace(answer=1))

    extracted = patch.extract_json_block("\x1b[32m```json\n{\"elements\": []}\n```\x1b[0m")

    assert extracted == '{"elements": []}'


@pytest.mark.parametrize(("configured_timeout", "expected_timeout"), [(None, 240_000), ("12_345", 12_345)])
def test_browser_service_uses_default_or_explicit_timeout(monkeypatch, configured_timeout, expected_timeout):
    class FakeStagehand:
        def __init__(self, _config):
            self.llm = None

    if configured_timeout is None:
        monkeypatch.delenv("BROWSER_DEFAULT_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("BROWSER_DEFAULT_TIMEOUT", configured_timeout.replace("_", ""))
    monkeypatch.setattr(mod, "Stagehand", FakeStagehand)
    service = mod.BrowserService("ollama", "fixture-model")
    try:
        assert service.default_timeout == expected_timeout
    finally:
        service._loop.call_soon_threadsafe(service._loop.stop)
        service._loop_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_browser_action_retries_once_when_failed_attempt_has_no_side_effects(monkeypatch, caplog):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        def __init__(self):
            self.actions = []

        async def content(self):
            return "<main>Register</main>"

        async def act(self, action):
            self.actions.append(action)
            if len(self.actions) == 1:
                return ForeignActResult(False, "No observe results found", action)
            return ForeignActResult(True, "Action performed", action)

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def observe(self, _instruction):
            return []

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return [{
                    "index": 0,
                    "name": "email",
                    "visible": True,
                    "disabled": False,
                    "read_only": False,
                    "value_present": False,
                    "value_length": 0,
                }]
            if "return state.events" in expression:
                return []
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[], logs=[], dialogs=[], downloads=[], summarize=AsyncMock(return_value="network summary")
            )

    fake_browser = FakeBrowser()
    monkeypatch.setattr(mod, "get_browser", lambda: fake_browser)

    with caplog.at_level(logging.INFO, logger=mod.__name__):
        result = await mod.browser_perform_action("Enter alice@example.test into the Email field")

    assert len(fake_browser.page.actions) == 2
    assert "Visible control identifiers: email" in fake_browser.page.actions[1]
    assert '"attempt_count": 2' in caplog.text
    assert "alice@example.test" not in caplog.text
    assert "network summary" in result


@pytest.mark.asyncio
async def test_browser_action_does_not_retry_after_form_event(monkeypatch):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        def __init__(self):
            self.action_count = 0

        async def content(self):
            return "<main>Register</main>"

        async def act(self, _action):
            self.action_count += 1
            return ForeignActResult(False, "No observe results found", "fill")

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return []
            if "return state.events" in expression:
                return [{"type": "input", "tag": "input", "name": "email"}]
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(requests=[], logs=[], dialogs=[], downloads=[], summarize=AsyncMock())

    fake_browser = FakeBrowser()
    monkeypatch.setattr(mod, "get_browser", lambda: fake_browser)

    with pytest.raises(RuntimeError, match="no_actionable_element"):
        await mod.browser_perform_action("Enter alice@example.test into the Email field")

    assert fake_browser.page.action_count == 1


@pytest.mark.asyncio
async def test_browser_action_logs_secret_safe_form_state_change(monkeypatch, caplog):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        def __init__(self):
            self.snapshots = [
                [{"index": 0, "name": "password", "value_present": False, "value_length": 0}],
                [{"index": 0, "name": "password", "value_present": True, "value_length": 18}],
            ]

        async def content(self):
            return "<main>Register</main>"

        async def act(self, _action):
            return ForeignActResult(success=True, message="Action performed", action="fill")

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def observe(self, _instruction):
            return []

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return self.snapshots.pop(0)
            if "return state.events" in expression:
                return [{"type": "input", "tag": "input", "name": "password"}]
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[],
                logs=[],
                dialogs=[],
                downloads=[],
                summarize=AsyncMock(return_value="network summary"),
            )

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    with caplog.at_level(logging.INFO, logger=mod.__name__):
        await mod.browser_perform_action("Enter VerySensitiveValue into the Password field")

    assert '"changed_control_count": 1' in caplog.text
    assert '"value_changed_control_count": 1' in caplog.text
    assert '"form_event_count": 1' in caplog.text
    assert '"stagehand_success": true' in caplog.text
    assert "VerySensitiveValue" not in caplog.text
    assert "input action" in caplog.text


@pytest.mark.asyncio
async def test_browser_action_warns_when_input_does_not_change_form_state(monkeypatch, caplog):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        async def content(self):
            return "<main>Register</main>"

        async def act(self, _action):
            return {"success": True, "message": "Action performed", "action": "fill"}

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def observe(self, _instruction):
            return []

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return [{"index": 0, "name": "email", "value_present": False, "value_length": 0}]
            if "return state.events" in expression:
                return []
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[],
                logs=[],
                dialogs=[],
                downloads=[],
                summarize=AsyncMock(return_value="network summary"),
            )

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        result = await mod.browser_perform_action("Enter user@example.test into the Email field")

    assert "completed without a form-value change or input/change event" in caplog.text
    assert "user@example.test" not in caplog.text
    assert "network summary" in result


@pytest.mark.asyncio
async def test_browser_action_raises_when_stagehand_reports_failure(monkeypatch, caplog):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        async def content(self):
            return "<main>Register</main>"

        async def act(self, _action):
            return ForeignActResult(
                success=False,
                message="No observe results found for action Enter SecretValue",
                action="Enter SecretValue",
            )

        async def wait_for_load_state(self, *_args, **_kwargs):
            pytest.fail("A negative Stagehand result must stop before page waiting")

        async def observe(self, _instruction):
            pytest.fail("A negative Stagehand result must stop before page observation")

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return [{"index": 0, "name": "password", "value_present": False, "value_length": 0}]
            if "return state.events" in expression:
                return []
            return None

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[],
                logs=[],
                dialogs=[],
                downloads=[],
                summarize=AsyncMock(return_value="network summary"),
            )

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    with caplog.at_level(logging.INFO, logger=mod.__name__), pytest.raises(
            RuntimeError, match="Stagehand browser action failed: no_actionable_element"
    ):
        await mod.browser_perform_action("Enter SecretValue into the Password field")

    assert '"stagehand_success": false' in caplog.text
    assert '"stagehand_failure_reason": "no_actionable_element"' in caplog.text
    assert "SecretValue" not in caplog.text


@pytest.mark.asyncio
async def test_browser_action_diagnostics_failure_does_not_fail_action(monkeypatch, caplog):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        async def content(self):
            return "<main>Register</main>"

        async def act(self, _action):
            return {"success": True, "message": "Action performed"}

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def observe(self, _instruction):
            return []

        async def evaluate(self, _expression):
            raise RuntimeError("diagnostics unavailable")

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()
            self.page_domain = "example.test"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

        @asynccontextmanager
        async def interaction_context_capture(self, **_kwargs):
            yield SimpleNamespace(
                requests=[],
                logs=[],
                dialogs=[],
                downloads=[],
                summarize=AsyncMock(return_value="network summary"),
            )

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        result = await mod.browser_perform_action("Click register")

    assert "form snapshot failed: RuntimeError" in caplog.text
    assert "event capture failed to start: RuntimeError" in caplog.text
    assert "network summary" in result


@pytest.mark.asyncio
async def test_browser_observation_timeout_returns_safe_dom_fallback(monkeypatch):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class FakePage:
        url = "https://example.test/register"

        async def observe(self, _instruction):
            raise TimeoutError()

        async def evaluate(self, expression):
            if "querySelectorAll" in expression:
                return [{
                    "index": 0,
                    "name": "email",
                    "visible": True,
                    "disabled": False,
                    "read_only": False,
                    "value_present": False,
                    "value_length": 0,
                }]
            return None

    class FakeBrowser:
        page = FakePage()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def timeout(self):
            return FakeTimeout()

        async def run_in_browser_loop(self, function):
            return await function()

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    result = await mod.browser_observe_page("registration controls")

    assert result[0].startswith("Degraded browser observation")
    assert result[-1] == "Visible form controls: email"


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

        async def screenshot(self, *, path, full_page):
            assert full_page is True
            with open(path, "wb") as artifact:
                artifact.write(b"png")

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
    assert "Screenshot saved" in await mod.browser_take_screenshot()
    assert list(tmp_path.glob("browser_screenshot_*.png"))
    assert await mod.browser_evaluate_js("() => 1") == {"expression": "() => 1"}
    cookies_csv = await mod.browser_get_cookies()
    assert "sid,abc,example.com" in cookies_csv
    assert await mod.browser_observe_page("links") == ["observed links"]


@pytest.mark.asyncio
async def test_browser_take_screenshot_reports_missing_artifact_without_returning_reference(monkeypatch, tmp_path):
    class FakeTimeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class MissingArtifactPage:
        async def screenshot(self, *, path, full_page):
            return None

    class FakeBrowser:
        artifacts_dir = str(tmp_path)
        page = MissingArtifactPage()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def run_in_browser_loop(self, fn):
            return await fn()

        def timeout(self):
            return FakeTimeout()

    monkeypatch.setattr(mod, "get_browser", lambda: FakeBrowser())

    result = await mod.browser_take_screenshot()

    assert result == "Browser screenshot was not captured: no artifact file was produced."
    assert "artifact:" not in result
    assert not list(tmp_path.glob("browser_screenshot_*.png"))


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
        def __init__(self, provider, model, artifacts_dir, headers, proxy=None):
            created.update(
                provider=provider,
                model=model,
                artifacts_dir=artifacts_dir,
                headers=headers,
                proxy=proxy,
            )

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
async def test_reset_authentication_browser_session_clears_state_without_closing_service(monkeypatch):
    class Page:
        url = "https://target.test/account"

        def __init__(self):
            self.evaluations = []
            self.navigations = []

        async def evaluate(self, expression):
            self.evaluations.append(expression)

        async def goto(self, url):
            self.navigations.append(url)

    class Context:
        def __init__(self):
            self.clear_cookies = AsyncMock()
            self.set_extra_http_headers = AsyncMock()

    class Browser:
        def __init__(self):
            self.page = Page()
            self.context = Context()
            self.run_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def run_in_browser_loop(self, function):
            self.run_calls += 1
            return await function()

    browser = Browser()
    monkeypatch.setattr(mod, "get_browser", lambda: browser)

    await mod.reset_authentication_browser_session()

    assert browser.run_calls == 1
    browser.context.clear_cookies.assert_awaited_once()
    browser.context.set_extra_http_headers.assert_awaited_once_with({})
    assert "localStorage.clear" in browser.page.evaluations[0]
    assert browser.page.navigations == ["about:blank"]


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


def test_resolve_browser_proxy_from_env_vars():
    # Test HTTP_PROXY
    proxy = mod.resolve_browser_proxy(environ={"HTTP_PROXY": "http://192.168.1.100:8080"})
    assert proxy == {"server": "http://192.168.1.100:8080"}

    # Test HTTPS_PROXY
    proxy = mod.resolve_browser_proxy(environ={"HTTPS_PROXY": "http://10.0.0.1:8080"})
    assert proxy == {"server": "http://10.0.0.1:8080"}

    # Test lowercase http_proxy / https_proxy
    proxy = mod.resolve_browser_proxy(environ={"http_proxy": "http://proxy.local:3128"})
    assert proxy == {"server": "http://proxy.local:3128"}

    proxy = mod.resolve_browser_proxy(environ={"https_proxy": "https://proxy.local:8443"})
    assert proxy == {"server": "https://proxy.local:8443"}

    # Test ALL_PROXY / all_proxy
    proxy = mod.resolve_browser_proxy(environ={"ALL_PROXY": "socks5://127.0.0.1:1080"})
    assert proxy == {"server": "socks5://127.0.0.1:1080"}

    # Test NO_PROXY / no_proxy bypass
    proxy = mod.resolve_browser_proxy(
        environ={
            "HTTP_PROXY": "http://127.0.0.1:8080",
            "NO_PROXY": "localhost,127.0.0.1,.internal",
        }
    )
    assert proxy == {
        "server": "http://127.0.0.1:8080",
        "bypass": "localhost,127.0.0.1,.internal",
    }


def test_resolve_browser_proxy_auth_and_schemes():
    # Proxy with credentials
    proxy = mod.resolve_browser_proxy("http://admin:secret123@127.0.0.1:8080")
    assert proxy == {
        "server": "http://127.0.0.1:8080",
        "username": "admin",
        "password": "secret123",
    }

    # Proxy with URL-encoded special characters in auth
    proxy = mod.resolve_browser_proxy("http://user%40domain:p%40ss%3Aword@proxy:8080")
    assert proxy == {
        "server": "http://proxy:8080",
        "username": "user@domain",
        "password": "p@ss:word",
    }

    # Schemeless proxy string defaults to http
    proxy = mod.resolve_browser_proxy("127.0.0.1:8080")
    assert proxy == {"server": "http://127.0.0.1:8080"}

    # IPv6 address
    proxy = mod.resolve_browser_proxy("http://[::1]:8080")
    assert proxy == {"server": "http://[::1]:8080"}


def test_resolve_browser_proxy_dict_and_empty():
    # Explicit dict
    proxy_dict = {"server": "http://burp:8080", "username": "u", "password": "p"}
    res = mod.resolve_browser_proxy(proxy_dict)
    assert res == proxy_dict
    assert res is not proxy_dict  # Returns a copy

    # Explicit dict attaches bypass from env if not present
    res = mod.resolve_browser_proxy(
        {"server": "http://burp:8080"},
        environ={"no_proxy": "localhost"},
    )
    assert res == {"server": "http://burp:8080", "bypass": "localhost"}

    # Empty/None returns None when no env set
    assert mod.resolve_browser_proxy(None, environ={}) is None
    assert mod.resolve_browser_proxy("", environ={}) is None
    assert mod.resolve_browser_proxy("   ", environ={}) is None


def test_browser_service_configures_proxy_launch_options(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:8080")

    # Mock Stagehand to avoid actual browser/network initialization
    monkeypatch.setattr(mod, "Stagehand", lambda config: SimpleNamespace(llm=None))

    service = mod.BrowserService(
        provider="ollama",
        model="test-model",
        artifacts_dir=None,
    )
    try:
        assert service.proxy == {"server": "http://127.0.0.1:8080"}
        launch_opts = service.stagehand_config.local_browser_launch_options
        assert launch_opts["proxy"] == {"server": "http://127.0.0.1:8080"}
        assert "--ignore-certificate-errors" in launch_opts["args"]
        assert launch_opts["ignoreHTTPSErrors"] is True
    finally:
        if service._loop:
            service._loop.call_soon_threadsafe(service._loop.stop)


def test_browser_service_explicit_proxy_override(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:8080")
    monkeypatch.setattr(mod, "Stagehand", lambda config: SimpleNamespace(llm=None))

    service = mod.BrowserService(
        provider="ollama",
        model="test-model",
        artifacts_dir=None,
        proxy="http://explicit-proxy:9090",
    )
    try:
        assert service.proxy == {"server": "http://explicit-proxy:9090"}
        launch_opts = service.stagehand_config.local_browser_launch_options
        assert launch_opts["proxy"] == {"server": "http://explicit-proxy:9090"}
    finally:
        if service._loop:
            service._loop.call_soon_threadsafe(service._loop.stop)
