"""Browser facts are stored separately from secrets and semantic conclusions."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from strands import tool

from modules.handlers.utils import get_tool_spec
from modules.tools import browser_flow_observations as mod
from modules.tools import login_replay


@pytest.mark.parametrize("url,expected", [
    ("https://user:secret@host.test:8443/login?token=secret#session", "https://host.test:8443/login"),
    ("http://[::1]:3000/signup?q=1", "http://[::1]:3000/signup"),
    ("about:blank", "about:blank"), ("", ""),
])
def test_observation_urls_exclude_credentials_query_and_fragment(url, expected):
    assert mod.observation_url(url) == expected


def test_filesystem_observations_keep_actions_and_group_controls(tmp_path):
    attempt = {}
    observations = mod.BrowserFlowObservations(tmp_path, attempt)
    controls = [{"selector": "#email", "name": "email", "label": "📧", "required": True, "options": []}]
    for action in ("navigate", "fill", "click"):
        reference = observations.record(
            "https://host.test/login?secret=1", controls,
            {"action": action, "url": "https://user:secret@host.test/login?q=secret"},
            requests=[{"url": "https://host.test/api?token=secret"}],
        )
        assert reference in attempt["evidence_refs"]
        data = json.loads((tmp_path / reference.removeprefix("artifact:")).read_text())
        assert data["url"] == "https://host.test/login"
        assert "secret" not in json.dumps(data)
    context = observations.context()
    assert context.count("login_controls[") == 1
    assert all(action in context for action in ("navigate", "fill", "click"))
    assert "📧" in context and "required" in context
    omitted = observations.context(max_chars=0)
    assert "login_controls[" not in omitted and reference in omitted
    assert "available as artifacts" in omitted
    assert mod.BrowserFlowObservations(tmp_path, attempt).context() == context


@pytest.mark.parametrize("result", ["performed", {"status": "error"}])
@pytest.mark.parametrize("failed_observation", [False, True])
def test_browser_wrapper_preserves_schema_results_and_avoids_action_prose(tmp_path, result, failed_observation):
    @tool
    async def browser_action(action: str) -> dict | str:
        """Perform a browser action."""
        return result
    observations = mod.BrowserFlowObservations(tmp_path, {})
    observations.observe = AsyncMock(side_effect=ValueError("secret") if failed_observation else None)
    wrapped = mod.observe_browser_tool(browser_action, observations)
    assert get_tool_spec(wrapped) == get_tool_spec(browser_action)
    assert asyncio.run(wrapped(action="fill password secret")) == result
    if result == "performed":
        observations.observe.assert_awaited_once()
        assert "secret" not in str(observations.observe.await_args)
    else:
        observations.observe.assert_not_awaited()


@pytest.mark.parametrize("name", ["browser_perform_action", "browser_get_page_html"])
def test_sync_wrapper_and_browser_loop_snapshot(tmp_path, monkeypatch, name):
    @tool(name=name)
    def browser_action() -> str:
        """Observe a page."""
        return "observed"
    async def run(function):
        return await function()
    browser = SimpleNamespace(page=SimpleNamespace(url="https://host.test/signup"), run_in_browser_loop=run)
    @asynccontextmanager
    async def browser_context():
        yield browser
    monkeypatch.setattr(mod, "get_browser", browser_context)
    monkeypatch.setattr(login_replay, "_login_controls", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "latest_browser_interaction_receipts", lambda: [{"status": 201}])
    attempt = {}
    observations = mod.BrowserFlowObservations(tmp_path, attempt)
    assert asyncio.run(mod.observe_browser_tool(browser_action, observations)()) == "observed"
    data = json.loads((tmp_path / attempt["observation_refs"][0].removeprefix("artifact:")).read_text())
    assert data["requests"] == ([{"status": 201}] if name == "browser_perform_action" else [])
    assert data["action"] == {"tool_name": name}
