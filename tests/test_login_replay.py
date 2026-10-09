"""Typed login action execution, replay eligibility, and metadata comparison."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.handlers.utils import get_tool_spec
from modules.tools import login_replay as mod


@pytest.fixture
def action_case(monkeypatch):
    from modules.tools import browser as browser_module

    monkeypatch.setattr(browser_module, "_LAST_INTERACTION_RECEIPTS", [])
    control = SimpleNamespace(
        wait_for=AsyncMock(),
        count=AsyncMock(return_value=1),
        is_visible=AsyncMock(return_value=True),
        is_enabled=AsyncMock(return_value=True),
        fill=AsyncMock(),
        select_option=AsyncMock(),
        set_checked=AsyncMock(),
        click=AsyncMock(),
        evaluate=AsyncMock(return_value=[{"value": "rest", "disabled": False}, {"value": "", "disabled": False}]),
    )
    page = SimpleNamespace(
        url="https://target.test/login",
        locator=Mock(return_value=control),
        route=AsyncMock(),
        unroute=AsyncMock(),
        goto=AsyncMock(),
        evaluate=AsyncMock(return_value=True),
        wait_for_function=AsyncMock(),
    )
    interaction = SimpleNamespace(
        summarize=AsyncMock(return_value="[Full HAR saved to /tmp/network.har]"),
        requests=[],
    )

    @asynccontextmanager
    async def capture_interaction():
        yield interaction

    async def run_in_browser_loop(function):
        return await function()

    browser = SimpleNamespace(
        page=page,
        default_timeout=240000,
        interaction_context_capture=capture_interaction,
        run_in_browser_loop=AsyncMock(side_effect=run_in_browser_loop),
    )

    @asynccontextmanager
    async def browser_context():
        yield browser

    monkeypatch.setattr(mod, "get_browser", browser_context)
    monkeypatch.setattr(mod, "_save_rendered_page", AsyncMock(return_value="artifact:/tmp/page.html"))
    monkeypatch.setattr(
        mod,
        "_login_controls",
        AsyncMock(
            return_value=[
                {
                    "selector": "#email",
                    "tag": "input",
                    "type": "email",
                    "id": "email",
                    "name": "Email",
                    "aria_label": "📧",
                    "options": [],
                },
            ]
        ),
    )
    payload = {"username": "user-one", "email": "one@example.test", "password": "internal-secret"}
    monkeypatch.setattr(mod, "_active_record", lambda _identifier: (None, {"payload": payload}, "selected"))
    attempt = {"replay_actions": []}
    capture = AsyncMock()
    tool = mod.build_login_action_tool("selected", ("https://target.test",), attempt, capture)
    return tool, attempt, capture, page, control, payload


@pytest.mark.parametrize(
    "arguments,method,expected",
    [
        ({"action": "navigate", "url": "https://target.test/login"}, "goto", None),
        ({"action": "fill", "locator": "#email", "credential_field": "email"}, "fill", "one@example.test"),
        ({"action": "input", "locator": "#username", "credential_field": "username"}, "fill", "user-one"),
        ({"action": "fill", "locator": "#password", "credential_field": "password"}, "fill", "internal-secret"),
        ({"action": "select_option", "locator": "#mode", "value": "rest"}, "select_option", None),
        ({"action": "select", "locator": "#mode", "value": ""}, "select_option", None),
        ({"action": "check", "locator": "#remember", "checked": False}, "set_checked", False),
        ({"action": "checkbox", "locator": "#remember", "checked": True}, "set_checked", True),
        ({"action": "click", "locator": "#submit"}, "click", None),
    ],
)
def test_typed_login_actions_resolve_values_and_return_compact_evidence(action_case, arguments, method, expected):
    tool, attempt, capture, page, control, payload = action_case
    result = asyncio.run(tool(**arguments))
    called = getattr(page if method == "goto" else control, method)
    called.assert_awaited_once()
    if expected is not None:
        assert called.await_args.args[0] == expected
    capture.assert_awaited_once()
    assert len(attempt["replay_actions"]) == 1
    assert all(value not in json.dumps(attempt) + result for value in payload.values())
    assert "/tmp/network.har" in result and "/tmp/page.html" in result and "📧" in result
    page.unroute.assert_awaited_once()


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "unknown"},
        {"action": "fill", "locator": "#email"},
        {"action": "click", "locator": "#submit", "value": "extra"},
        {"action": "check", "locator": "#remember", "checked": "false"},
        {"action": "navigate"},
        {"action": "select", "locator": "#mode"},
        {"action": "fill", "locator": "#code", "credential_field": "mfa"},
    ],
)
def test_typed_login_actions_reject_invalid_arguments(action_case, arguments):
    tool, attempt, capture, page, _control, _payload = action_case
    with pytest.raises(ValueError):
        asyncio.run(tool(**arguments))
    assert not attempt["replay_actions"]
    capture.assert_not_awaited()
    page.route.assert_not_awaited()


@pytest.mark.parametrize("failure", ["ambiguous", "missing", "hidden", "disabled", "no_email", "action_error"])
def test_typed_login_action_failure_does_not_record_or_capture(action_case, failure):
    tool, attempt, capture, page, control, payload = action_case
    if failure in {"ambiguous", "missing"}:
        control.count.return_value = 2 if failure == "ambiguous" else 0
    elif failure == "hidden":
        control.is_visible.return_value = False
    elif failure == "disabled":
        control.is_enabled.return_value = False
    elif failure == "no_email":
        payload.pop("email")
    else:
        control.fill.side_effect = RuntimeError("browser failed")
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(tool(action="fill", locator="#email", credential_field="email"))
    assert not attempt["replay_actions"]
    capture.assert_not_awaited()
    page.unroute.assert_awaited_once()


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.test/login",
        "https://target.test:444/login",
        "http://target.test/login",
        "file:///tmp/page",
        "https://user:pass@target.test/login",
        "https://target.test:bad/login",
    ],
)
def test_navigation_rejects_unmapped_origins(action_case, url):
    tool, attempt, _capture, _page, _control, _payload = action_case
    with pytest.raises(ValueError):
        asyncio.run(tool(action="navigate", url=url))
    assert not attempt["replay_actions"]


def test_login_tool_handles_incomplete_context_and_preserves_artifact_access(action_case):
    tool, attempt, capture, _page, _control, _payload = action_case
    capture.side_effect = ValueError("not logged in yet")
    result = json.loads(asyncio.run(tool(action="click", locator="#next")))
    assert result["status"] == "performed"
    assert attempt["replay_actions"] == [{"action": "click", "locator": "#next"}]


def test_login_tool_schema_advertises_canonical_actions(action_case):
    tool, *_rest = action_case
    schema = get_tool_spec(tool)["inputSchema"]["json"]
    assert schema["properties"]["action"]["enum"] == ["navigate", "fill", "select", "check", "click"]
    assert schema["additionalProperties"] is False
    assert "value" not in schema["required"]


def test_login_tool_blocks_redirects_before_navigation_and_preserves_current_page(action_case):
    tool, attempt, _capture, page, _control, _payload = action_case
    rejected = SimpleNamespace(
        request=SimpleNamespace(url="https://elsewhere.test/login", is_navigation_request=lambda: True),
        abort=AsyncMock(),
        continue_=AsyncMock(),
    )

    async def navigate(*_args, **_kwargs):
        guard = page.route.await_args.args[1]
        await guard(rejected)

    page.goto.side_effect = navigate
    with pytest.raises(ValueError, match="mapped origins"):
        asyncio.run(tool(action="navigate", url="https://target.test/login"))
    rejected.abort.assert_awaited_once()
    assert not attempt["replay_actions"]


@pytest.mark.parametrize("navigation,url", [(True, "https://target.test/next"), (False, "https://cdn.test/app.js")])
def test_login_tool_allows_mapped_navigation_and_non_navigation_resources(action_case, navigation, url):
    tool, _attempt, _capture, page, _control, _payload = action_case
    route = SimpleNamespace(
        request=SimpleNamespace(url=url, is_navigation_request=lambda: navigation),
        abort=AsyncMock(),
        continue_=AsyncMock(),
    )

    async def navigate(*_args, **_kwargs):
        await page.route.await_args.args[1](route)

    page.goto.side_effect = navigate
    asyncio.run(tool(action="navigate", url="https://target.test/login"))
    route.continue_.assert_awaited_once()
    route.abort.assert_not_awaited()


def test_login_tool_rejects_current_page_outside_scope_and_credential_literals(action_case):
    tool, _attempt, _capture, page, _control, _payload = action_case
    page.url = "https://elsewhere.test"
    with pytest.raises(ValueError, match="Current page"):
        asyncio.run(tool(action="click", locator="#submit"))
    with pytest.raises(ValueError, match="credential values"):
        asyncio.run(tool(action="select", locator="#mode", value="internal-secret"))


@pytest.mark.parametrize(
    "flow", [{}, {"replay_version": 9, "replay_actions": []}, {"replay_version": 1, "replay_actions": "not a list"}]
)
def test_unknown_or_absent_replay_is_not_executable(flow):
    assert mod.replay_actions(flow) == []


@pytest.mark.parametrize(
    "actions",
    [[], [{"action": "click", "locator": "#submit"}], [{"action": "navigate", "url": "https://target.test/other"}]],
)
def test_replay_requires_an_explicit_initial_navigation(actions):
    with pytest.raises(ValueError, match="start"):
        mod.replay_actions({"replay_version": 1, "replay_actions": actions, "login_url": "https://target.test/login"})


def test_reusable_metadata_ignores_identity_proof_but_detects_transport_and_script_changes():
    flow = {
        "kind": "browser_form",
        "login_url": "https://target.test/login",
        "replay_version": 1,
        "replay_actions": [{"action": "navigate", "url": "https://target.test/login"}],
        "allowed_origins": ["https://target.test"],
        "request_header_names": ["X-Session", "Tauser"],
    }
    assert mod.replay_actions(flow)[0].action == "navigate"
    original = mod.reusable_login_metadata(flow)
    assert (
        mod.reusable_login_metadata(
            {
                **flow,
                "validation_url": "https://target.test/users/other",
                "evidence_refs": ["artifact:old"],
                "steps": [{"action": "verification"}],
                "request_header_names": ["tauser", "x-session"],
            }
        )
        == original
    )
    assert mod.reusable_login_metadata({**flow, "request_header_names": ["Other"]}) != original
    assert mod.reusable_login_metadata({**flow, "replay_actions": []}) != original


@pytest.mark.asyncio
@pytest.mark.parametrize("action,status", [("navigation", "success"), ("navigate", "success"), ("dance", "error")])
async def test_login_action_sdk_runtime_normalizes_aliases_and_rejects_unknown(action_case, action, status):
    tool, attempt, *_rest = action_case
    events = [
        event
        async for event in tool.stream(
            {
                "toolUseId": "login",
                "name": "perform_login_action",
                "input": {"action": action, "url": "https://target.test/login"},
            },
            {},
        )
    ]
    assert events[-1]["tool_result"]["status"] == status
    assert bool(attempt["replay_actions"]) is (status == "success")


@pytest.mark.asyncio
async def test_typed_actions_and_nested_capture_use_serialized_browser_loop(action_case):
    from modules.tools.browser import BrowserService

    tool, _attempt, capture, page, _control, _payload = action_case
    service = BrowserService.__new__(BrowserService)
    service._loop = asyncio.get_running_loop()
    service._op_lock = None
    service._active_ops = 0
    service._active_ops_peak = 0
    service._active_ops_violations = 0
    async with mod.get_browser() as browser:
        browser.run_in_browser_loop = service.run_in_browser_loop

    async def capture_on_browser_loop():
        return None

    async def nested_capture():
        page.unroute.assert_not_awaited()
        await service.run_in_browser_loop(capture_on_browser_loop)

    capture.side_effect = nested_capture
    await tool(action="click", locator="#submit")
    assert service._active_ops_peak == 1
    assert service._active_ops_violations == 0
    page.unroute.assert_awaited_once()


def test_typed_multistep_login_script_can_execute_for_another_account(action_case):
    tool, attempt, capture, page, control, payload = action_case
    actions = [
        {"action": "navigate", "url": "https://target.test/login"},
        {"action": "select", "locator": "#mode", "value": "rest"},
        {"action": "fill", "locator": "#email", "credential_field": "email"},
        {"action": "click", "locator": "#next"},
        {"action": "fill", "locator": "#password", "credential_field": "password"},
        {"action": "click", "locator": "#submit"},
    ]
    capture.side_effect = ValueError("not complete yet")
    for action in actions:
        asyncio.run(tool(**action))
        if action.get("locator") == "#next":
            page.url = "https://target.test/password"
    assert attempt["replay_actions"] == actions
    flow = {"login_url": actions[0]["url"], "replay_version": 1, "replay_actions": actions}
    attempt["replay_actions"] = []
    payload.update(email="two@example.test", password="another-internal-secret")
    for action in mod.replay_actions(flow):
        asyncio.run(tool(**action.model_dump()))
    assert control.fill.await_args_list[-2].args[0] == "two@example.test"
    assert control.fill.await_args_list[-1].args[0] == "another-internal-secret"
    assert "another-internal-secret" not in json.dumps(attempt)
    assert capture.await_count == len(actions) * 2


def test_typed_action_publishes_current_receipts_before_context_capture(action_case, monkeypatch):
    from modules.tools.browser import latest_browser_interaction_receipts

    tool, _attempt, capture, _page, _control, _payload = action_case
    monkeypatch.setattr(
        mod,
        "_interaction_response_receipts",
        AsyncMock(
            return_value=[
                {
                    "url": "https://target.test/login",
                    "method": "POST",
                    "status": 401,
                }
            ]
        ),
    )

    async def check_current_receipt():
        assert latest_browser_interaction_receipts() == [
            {
                "url": "https://target.test/login",
                "method": "POST",
                "status": 401,
                "evidence_ref": "/tmp/network.har",
            }
        ]
        raise ValueError("credential rejected")

    capture.side_effect = check_current_receipt
    asyncio.run(tool(action="click", locator="#submit"))


@pytest.mark.parametrize("timeout", [30000, 240000])
def test_typed_actions_use_configured_timeout(action_case, timeout):
    tool, _attempt, _capture, page, control, _payload = action_case

    async def configure_and_run():
        async with mod.get_browser() as browser:
            browser.default_timeout = timeout
        await tool(action="navigate", url="https://target.test/login")
        await tool(action="fill", locator="#email", credential_field="email")

    asyncio.run(configure_and_run())
    assert page.goto.await_args.kwargs["timeout"] == timeout
    assert control.wait_for.await_args.kwargs["timeout"] == timeout
    assert control.fill.await_args.kwargs["timeout"] == timeout
    assert page.wait_for_function.await_args.kwargs["timeout"] == timeout


def test_delayed_control_can_succeed_beyond_old_ten_second_limit(action_case):
    tool, attempt, _capture, _page, control, _payload = action_case

    async def delayed_ready(*, state, timeout):
        # Virtual readiness at 15 seconds avoids a wall-clock delay in the unit test.
        assert state == "visible"
        if timeout < 15000:
            raise mod.BrowserTimeoutError("not ready")

    control.wait_for.side_effect = delayed_ready
    asyncio.run(tool(action="click", locator="#submit"))
    assert attempt["replay_actions"] == [{"action": "click", "locator": "#submit"}]


def test_invalid_css_explains_correct_input_without_waiting(action_case):
    tool, attempt, _capture, page, control, _payload = action_case
    page.evaluate.return_value = False
    with pytest.raises(ValueError, match="Invalid CSS selector.*inspect_login_controls"):
        asyncio.run(tool(action="click", locator="[invalid"))
    control.wait_for.assert_not_awaited()
    assert attempt["replay_actions"] == []


@pytest.mark.parametrize("options", [None, [], [{"value": "user", "disabled": True}]])
def test_unavailable_or_disabled_options_fail_before_select(action_case, options, monkeypatch):
    tool, attempt, _capture, _page, control, _payload = action_case
    control.evaluate.return_value = options
    monkeypatch.setattr(
        mod,
        "_login_controls",
        AsyncMock(
            return_value=[
                {
                    "selector": "#mode",
                    "tag": "select",
                    "label": "🔐 Any label",
                    "options": [{"value": "html", "label": "Web login", "disabled": False, "selected": True}],
                }
            ]
        ),
    )
    with pytest.raises(ValueError, match="Select value is unavailable") as error:
        asyncio.run(tool(action="select", locator="#mode", value="user"))
    assert "html" in str(error.value) and "Web login" in str(error.value)
    assert "🔐" in str(error.value)
    control.select_option.assert_not_awaited()
    assert attempt["replay_actions"] == []


@pytest.mark.parametrize("state,count", [("missing", 0), ("ambiguous", 2), ("hidden", 1)])
def test_visibility_timeout_reports_state_and_configured_limit(action_case, state, count):
    tool, attempt, _capture, _page, control, _payload = action_case
    control.count.side_effect = [0, count]
    control.wait_for.side_effect = mod.BrowserTimeoutError("wait failed")
    control.is_visible.return_value = False
    with pytest.raises(ValueError, match=f"240s; state={state}"):
        asyncio.run(tool(action="click", locator="#submit"))
    assert not attempt["replay_actions"]


@pytest.mark.parametrize("state", ["missing", "ambiguous", "hidden", "disabled", "action_not_completed"])
def test_action_timeout_reports_control_state_without_secret_call_log(action_case, state):
    tool, attempt, _capture, _page, control, payload = action_case
    count = {"missing": 0, "ambiguous": 2}.get(state, 1)
    control.count.side_effect = [1, 1, count]
    control.is_visible.side_effect = [True, state != "hidden"]
    control.is_enabled.side_effect = [True, state != "disabled"]
    control.fill.side_effect = mod.BrowserTimeoutError(payload["password"])
    with pytest.raises(ValueError, match=f"240s; state={state}") as error:
        asyncio.run(tool(action="fill", locator="#email", credential_field="email"))
    assert payload["password"] not in str(error.value)
    assert not attempt["replay_actions"]


def test_navigation_timeout_reports_navigation_state(action_case):
    tool, attempt, _capture, page, _control, _payload = action_case
    page.goto.side_effect = mod.BrowserTimeoutError("navigation delayed")
    with pytest.raises(ValueError, match="240s; state=navigation"):
        asyncio.run(tool(action="navigate", url="https://target.test/login"))
    assert not attempt["replay_actions"]


def test_login_inspection_is_authorized_read_only_and_returns_artifact(action_case):
    _tool, attempt, capture, page, _control, _payload = action_case
    inspect = mod.build_login_inspection_tool("selected", ("https://target.test",))
    result = json.loads(asyncio.run(inspect()))
    assert "#email" in result["controls"] and "📧" in result["controls"]
    assert result["page_artifact"] == "artifact:/tmp/page.html"
    page.goto.assert_not_awaited()
    page.route.assert_not_awaited()
    capture.assert_not_awaited()
    assert attempt["replay_actions"] == []
    assert get_tool_spec(inspect)["name"] == "inspect_login_controls"


@pytest.mark.parametrize("failure", ["authorization", "origin"])
def test_login_inspection_rejects_unauthorized_access(action_case, monkeypatch, failure):
    _tool, _attempt, _capture, page, _control, _payload = action_case
    snapshot = mod._login_controls
    if failure == "authorization":

        def reject(_identifier):
            raise ValueError("an active task is required")

        monkeypatch.setattr(mod, "_active_record", reject)
    else:
        page.url = "https://elsewhere.test/login"
    inspect = mod.build_login_inspection_tool("selected", ("https://target.test",))
    with pytest.raises(ValueError):
        asyncio.run(inspect())
    snapshot.assert_not_awaited()


def test_control_inspection_redacts_metadata_and_never_reads_input_values():
    page = SimpleNamespace(
        evaluate=AsyncMock(
            return_value=[
                {
                    "selector": "#one@example.test",
                    "label": "one@example.test",
                    "options": [
                        {"value": "internal-secret", "label": "user-one", "selected": False, "disabled": False}
                    ],
                    "disabled": False,
                }
            ]
        )
    )
    payload = {"username": "user-one", "email": "one@example.test", "password": "internal-secret"}
    result = asyncio.run(mod._login_controls(page, payload))
    assert result[0]["selector"] == ""
    assert all(value not in json.dumps(result) for value in payload.values())
    assert "element.value" not in page.evaluate.await_args.args[0]
    assert "option.value" in page.evaluate.await_args.args[0]


def test_control_tables_support_empty_controls():
    assert mod._control_tables([]) == ""


@pytest.mark.parametrize("state", ["disabled", "action_not_completed"])
def test_enabled_readiness_timeout_reports_actual_state(action_case, state):
    tool, _attempt, _capture, page, control, _payload = action_case
    page.wait_for_function.side_effect = mod.BrowserTimeoutError("not enabled yet")
    control.is_enabled.return_value = state != "disabled"
    with pytest.raises(ValueError, match=f"240s; state={state}"):
        asyncio.run(tool(action="click", locator="#submit"))


def test_dom_inspection_generates_verified_selectors_and_exact_options():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    package = Path(__file__).resolve().parents[1] / "src/modules/interfaces/react/node_modules/jsdom"
    if node is None or not package.exists():
        pytest.skip("DOM inspection integration requires installed frontend development dependencies")
    script = r"""
const fs = require("node:fs");
const {JSDOM} = require(process.argv[1]);
const source = JSON.parse(fs.readFileSync(0, "utf8"));
const dom = new JSDOM(`<form>
<label for="field:email">📧 Arbitrary label</label>
<input id="field:email" type="email" value="private-input">
<input id="duplicate"><input id="duplicate">
<select aria-label="🔐"><option value="" selected>Choose</option>
<option value="html">🌍 Browser</option>
<optgroup disabled><option value="blocked">Unavailable</option></optgroup></select>
<button disabled>Submit</button><input style="display:none" value="hidden-secret">
</form>`, {runScripts: "outside-only"});
const {window} = dom;
// jsdom has no layout engine or native CSS.escape; supply those browser primitives.
window.HTMLElement.prototype.getBoundingClientRect = () => ({width: 10, height: 10});
window.CSS = {escape: value => value.replaceAll(":", "\\:")};
const controls = window.eval("(" + source + ")() ");
for (const control of controls) {
    if (window.document.querySelectorAll(control.selector).length !== 1) {
        throw new Error("Selector was not unique");
    }
}
console.log(JSON.stringify(controls));
"""
    result = subprocess.run(
        [node, "-e", script, str(package)],
        input=json.dumps(mod._LOGIN_CONTROLS_SCRIPT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    controls = json.loads(result.stdout)
    assert len(controls) == 5
    assert controls[0]["selector"] == r"#field\:email"
    assert controls[0]["label"] == "📧 Arbitrary label"
    assert controls[1]["selector"] != controls[2]["selector"]
    assert controls[1]["selector"].startswith("html:nth-child")
    assert controls[3]["options"] == [
        {"value": "", "label": "Choose", "selected": True, "disabled": False},
        {"value": "html", "label": "🌍 Browser", "selected": False, "disabled": False},
        {"value": "blocked", "label": "Unavailable", "selected": False, "disabled": True},
    ]
    assert controls[4]["disabled"] is True
    assert controls[4]["label"] == "Submit"
    assert "private-input" not in result.stdout and "hidden-secret" not in result.stdout


def test_control_inspection_timeout_cannot_mask_original_error(action_case):
    tool, attempt, _capture, page, _control, _payload = action_case
    page.evaluate.side_effect = mod.BrowserTimeoutError("renderer unresponsive")
    with pytest.raises(ValueError, match="240s; state=control_inspection"):
        asyncio.run(tool(action="click", locator="#submit"))
    assert not attempt["replay_actions"]
    page.unroute.assert_awaited_once()


@pytest.mark.parametrize("failure", ["capture", "snapshot"])
def test_successful_action_remains_recorded_if_later_observation_fails(action_case, monkeypatch, failure):
    tool, attempt, capture, page, control, _payload = action_case
    if failure == "capture":
        capture.side_effect = RuntimeError("capture unavailable")
    else:
        monkeypatch.setattr(mod, "_login_controls", AsyncMock(side_effect=RuntimeError("snapshot unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(tool(action="click", locator="#submit"))
    control.click.assert_awaited_once()
    assert attempt["replay_actions"] == [{"action": "click", "locator": "#submit"}]
    page.unroute.assert_awaited_once()


@pytest.mark.parametrize("inspection", [False, True])
def test_typed_login_records_controller_facts_and_transport(action_case, tmp_path, monkeypatch, inspection):
    from modules.tools.browser_flow_observations import BrowserFlowObservations

    _tool, attempt, capture, page, _control, _payload = action_case
    observations = BrowserFlowObservations(tmp_path, attempt)
    if inspection:
        observed_tool = mod.build_login_inspection_tool("selected", ("https://target.test",), observations)
        asyncio.run(observed_tool())
    else:
        observed_tool = mod.build_login_action_tool("selected", ("https://target.test",), attempt, capture, observations)
        # Metadata comes from observed requests, never inferred from form markup.
        interaction = SimpleNamespace(
            requests=[SimpleNamespace(method="POST", url="https://target.test/login?token=secret",
                                      headers={"content-type": "application/x-www-form-urlencoded"})],
            summarize=AsyncMock(return_value="completed"),
        )
        @asynccontextmanager
        async def interaction_context():
            yield interaction
        @asynccontextmanager
        async def browser_context():
            async def run(function):
                return await function()
            yield SimpleNamespace(page=page, default_timeout=240000, run_in_browser_loop=run,
                                  interaction_context_capture=interaction_context)
        monkeypatch.setattr(mod, "get_browser", browser_context)
        monkeypatch.setattr(mod, "_interaction_response_receipts", AsyncMock(return_value=[]))
        asyncio.run(observed_tool(action="navigate", url="https://target.test/login"))
    data = json.loads((tmp_path / attempt["observation_refs"][0].removeprefix("artifact:")).read_text())
    assert data["controls"][0]["aria_label"] == "📧"
    assert "internal-secret" not in json.dumps(data)
    if not inspection:
        assert data["before_url"] == page.url
        assert data["action"]["action"] == "navigate"
        assert data["requests"][0]["request_content_type"] == "application/x-www-form-urlencoded"
        assert data["requests"][0]["url"] == "https://target.test/login"
        assert attempt["replay_actions"][0]["action"] == "navigate"
