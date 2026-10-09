"""Typed browser login actions and secret-free reusable execution metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as BrowserTimeoutError
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator
from strands import tool

from modules.tools.artifact_references import extract_artifact_references
from modules.tools.authentication import _active_record, _safe_flow_descriptor_url
from modules.tools.browser import (
    _interaction_response_receipts,
    _save_rendered_page,
    format_toon_table,
    get_browser,
    set_browser_interaction_receipts,
)
from modules.tools.browser_flow_observations import BrowserFlowObservations, observation_url


class LoginAction(BaseModel):
    """One atomic browser operation; credential values are resolved only at execution."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["navigate", "fill", "select", "check", "click"]
    url: str = ""
    locator: str = ""
    credential_field: Literal["", "username", "email", "password"] = ""
    value: str | None = None
    checked: StrictBool | None = None

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        aliases = {
            "navigation": "navigate",
            "type": "fill",
            "input": "fill",
            "select_option": "select",
            "checkbox": "check",
            "press": "click",
        }
        normalized = str(value).strip().lower()
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def validate_parameters(self) -> LoginAction:
        expected = {
            "navigate": {"url"},
            "fill": {"locator", "credential_field"},
            "select": {"locator", "value"},
            "check": {"locator", "checked"},
            "click": {"locator"},
        }[self.action]
        supplied = {
            key for key, value in self.model_dump().items() if key != "action" and value is not None and value != ""
        }
        if self.value == "":
            supplied.add("value")
        if supplied != expected:
            raise ValueError("Supply only the parameters required by this login action")
        return self


def replay_actions(flow: dict[str, Any]) -> list[LoginAction]:
    """Reject incomplete or unknown replay formats without guessing executable steps."""

    if flow.get("replay_version") != 1 or not isinstance(flow.get("replay_actions"), list):
        return []
    actions = [LoginAction.model_validate(item) for item in flow["replay_actions"]]
    if not actions or actions[0].action != "navigate" or actions[0].url != flow.get("login_url"):
        raise ValueError("Login replay must start at the recorded login URL")
    return actions


def reusable_login_metadata(flow: dict[str, Any]) -> str:
    """Compare reusable mapping semantics independently of per-account execution proof."""

    fields = (
        "kind",
        "login_url",
        "allowed_origins",
        "replay_version",
        "replay_actions",
        "storage_header_bindings",
        "authorization_storage_key",
        "request_format",
        "request_method",
        "username_field",
        "password_field",
    )
    normalized = {key: flow.get(key) for key in fields}
    normalized["storage_header_bindings"] = flow.get("storage_header_bindings") or []
    normalized["authorization_storage_key"] = flow.get("authorization_storage_key") or ""
    normalized["allowed_origins"] = sorted(set(flow.get("allowed_origins") or []))
    normalized["request_header_names"] = sorted({name.lower() for name in flow.get("request_header_names") or []})
    normalized["steps"] = [step for step in flow.get("steps") or [] if step.get("action") != "verification"]
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Login navigation requires an allowed HTTP origin")
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


_LOGIN_CONTROLS_SCRIPT = """
() => Array.from(document.querySelectorAll("input, textarea, select, button"))
    .filter(element => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0
            && style.visibility !== "hidden" && style.display !== "none";
    }).slice(0, 100).map(element => {
        let selector = element.id ? "#" + CSS.escape(element.id) : "";
        if (!selector || document.querySelectorAll(selector).length !== 1) {
            const parts = [];
            let current = element;
            while (current && current.nodeType === 1) {
                const parent = current.parentElement;
                const position = parent ? Array.from(parent.children).indexOf(current) + 1 : 1;
                parts.unshift(current.tagName.toLowerCase() + ":nth-child(" + position + ")");
                current = parent;
            }
            selector = parts.join(" > ");
        }
        const matches = document.querySelectorAll(selector);
        return {
            selector: matches.length === 1 && matches[0] === element ? selector : "",
            tag: element.tagName.toLowerCase(), type: element.getAttribute("type") || "",
            id: element.id || "", name: element.getAttribute("name") || "",
            label: Array.from(element.labels || []).map(label => label.textContent).join(" ")
                || (element.tagName === "BUTTON" ? element.textContent : ""),
            aria_label: element.getAttribute("aria-label") || "",
            placeholder: element.getAttribute("placeholder") || "",
            disabled: element.matches(":disabled"), read_only: Boolean(element.readOnly),
            required: Boolean(element.required),
            options: element.tagName === "SELECT" ? Array.from(element.options).map(option => ({
                value: option.value, label: option.label, selected: option.selected,
                disabled: option.disabled || Boolean(option.parentElement.disabled)
            })) : []
        };
    })
"""


async def _login_controls(page: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Inspect controls without collecting input values or exposing credential material."""
    controls = await page.evaluate(_LOGIN_CONTROLS_SCRIPT)
    materials = [str(payload[key]) for key in ("username", "email", "password") if payload.get(key)]

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            for material in materials:
                value = value.replace(material, "[redacted]")
            return value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            result = {key: redact(item) for key, item in value.items()}
            if "[redacted]" in result.get("selector", ""):
                result["selector"] = ""
            return result
        return value

    return redact(controls)


def _control_tables(controls: list[dict[str, Any]]) -> str:
    options = [
        {"selector": control["selector"], **option} for control in controls for option in control.get("options", [])
    ]
    return "\n".join(
        filter(
            None,
            [
                format_toon_table(
                    "login_controls",
                    [
                        "selector",
                        "tag",
                        "type",
                        "name",
                        "label",
                        "aria_label",
                        "placeholder",
                        "required",
                        "disabled",
                        "read_only",
                    ],
                    controls,
                ),
                format_toon_table("login_options", ["selector", "value", "label", "selected", "disabled"], options),
            ],
        )
    )


def build_login_inspection_tool(
    credential_id: str,
    allowed_origins: tuple[str, ...],
    observations: BrowserFlowObservations | None = None,
) -> Any:
    """Bind read-only inspection to the same authorization and origin boundary as login."""
    origins = {_origin(url) for url in allowed_origins}

    @tool(name="inspect_login_controls")
    async def inspect_login_controls() -> str:
        """Inspect current login controls, unique CSS selectors, and exact select option values."""
        _task, record, _identifier = _active_record(credential_id)
        async with get_browser() as browser:

            async def inspect() -> str:
                if _origin(str(browser.page.url)) not in origins:
                    raise ValueError("Current page is outside the mapped origins")
                controls = await _login_controls(browser.page, record["payload"])
                artifact = await _save_rendered_page(browser)
                if observations is not None:
                    observations.record(
                        str(browser.page.url),
                        controls,
                        {"tool_name": "inspect_login_controls"},
                        references=extract_artifact_references(artifact),
                    )
                return json.dumps(
                    {"controls": _control_tables(controls), "page_artifact": artifact}, ensure_ascii=False
                )

            return await browser.run_in_browser_loop(inspect)

    return inspect_login_controls


async def _login_control_state(control: Any) -> str:
    count = await control.count()
    if count == 0:
        return "missing"
    if count > 1:
        return "ambiguous"
    if not await control.is_visible():
        return "hidden"
    if not await control.is_enabled():
        return "disabled"
    return "action_not_completed"


async def _ready_login_control(page: Any, item: LoginAction, timeout: float, payload: dict[str, Any]) -> Any:
    valid = await page.evaluate(
        """selector => {
            try { document.querySelectorAll(selector); return true; }
            catch { return false; }
        }""",
        item.locator,
    )
    if not valid:
        raise ValueError("Invalid CSS selector. Use a selector returned by inspect_login_controls.")
    control = page.locator(f"css={item.locator}")
    if await control.count() > 1:
        raise ValueError("Login selector is ambiguous. Use a unique CSS selector from inspect_login_controls.")
    try:
        await control.wait_for(state="visible", timeout=timeout)
        await page.wait_for_function(
            """selector => {
                const element = document.querySelector(selector);
                return element && !element.matches(":disabled");
            }""",
            arg=item.locator,
            timeout=timeout,
        )
        count = await control.count()
        if count != 1 or not await control.is_visible() or not await control.is_enabled():
            state = await _login_control_state(control)
            tables = _control_tables(await _login_controls(page, payload))
            raise ValueError(f"Login selector must match one ready control; state={state}.\n{tables}")
        if item.action == "select":
            options = await control.evaluate(
                """element => element.tagName === "SELECT" ? Array.from(element.options).map(option => ({
                    value: option.value, disabled: option.disabled || Boolean(option.parentElement.disabled)
                })) : null"""
            )
            if options is None or not any(
                option["value"] == item.value and not option["disabled"] for option in options
            ):
                tables = _control_tables(await _login_controls(page, payload))
                raise ValueError(f"Select value is unavailable or disabled. Use an observed enabled value.\n{tables}")
    except BrowserTimeoutError:
        state = await _login_control_state(control)
        tables = _control_tables(await _login_controls(page, payload))
        raise ValueError(f"Login control timed out after {timeout / 1000:g}s; state={state}.\n{tables}") from None
    return control


def build_login_action_tool(
    credential_id: str,
    allowed_origins: tuple[str, ...],
    attempt: dict[str, Any],
    capture: Callable[[], Any],
    observations: BrowserFlowObservations | None = None,
) -> Any:
    """Bind direct browser operations to one selected credential and its mapped origins."""

    origins = {_origin(url) for url in allowed_origins}

    @tool(name="perform_login_action", inputSchema={"json": LoginAction.model_json_schema()})
    async def perform_login_action(
        action: str,
        url: str = "",
        locator: str = "",
        credential_field: str = "",
        value: str | None = None,
        checked: bool | None = None,
    ) -> str:
        """Perform an observed login action using a unique CSS locator and credential bindings.

        Canonical actions: navigate(url), fill(locator, credential_field), select(locator, value),
        check(locator, checked), click(locator). Never supply literal credential values.
        """

        item = LoginAction.model_validate(
            {
                "action": action,
                "url": url,
                "locator": locator,
                "credential_field": credential_field,
                "value": value,
                "checked": checked,
            }
        )
        _task, record, _identifier = _active_record(credential_id)
        payload = record["payload"]
        for key in ("username", "email", "password"):
            material = payload.get(key)
            if material and (
                item.value == material or f'"{material}"' in item.locator or f"'{material}'" in item.locator
            ):
                raise ValueError("Login action metadata must not contain credential values")
        if item.action == "navigate":
            if _origin(item.url) not in origins:
                raise ValueError("Login URL is outside the mapped origins")
            item.url = _safe_flow_descriptor_url(item.url, item.url, label="login replay URL")
        set_browser_interaction_receipts([])
        blocked_navigation = False

        async def guard_navigation(route: Any) -> None:
            nonlocal blocked_navigation
            request = route.request
            allowed = True
            if request.is_navigation_request():
                try:
                    allowed = _origin(request.url) in origins
                except ValueError:
                    allowed = False
            if allowed:
                await route.continue_()
            else:
                blocked_navigation = True
                await route.abort()

        async with get_browser() as browser:

            async def execute() -> tuple[str, str, list[dict[str, Any]]]:
                page = browser.page
                control = None
                before_url = str(page.url)
                if item.action != "navigate" and _origin(str(page.url)) not in origins:
                    raise ValueError("Current page is outside the mapped origins")
                await page.route("**/*", guard_navigation)
                try:
                    async with browser.interaction_context_capture() as interaction:
                        if item.action == "navigate":
                            await page.goto(item.url, wait_until="domcontentloaded", timeout=browser.default_timeout)
                        else:
                            control = await _ready_login_control(page, item, browser.default_timeout, payload)
                            if item.action == "fill":
                                material = payload.get(item.credential_field)
                                if not isinstance(material, str) or not material:
                                    raise ValueError("Selected credential field is unavailable")
                                await control.fill(material, timeout=browser.default_timeout)
                            elif item.action == "select":
                                await control.select_option(value=item.value, timeout=browser.default_timeout)
                            elif item.action == "check":
                                await control.set_checked(item.checked, timeout=browser.default_timeout)
                            else:
                                await control.click(timeout=browser.default_timeout)
                        if blocked_navigation or _origin(str(page.url)) not in origins:
                            raise ValueError("Login navigation left the mapped origins")
                        attempt["replay_actions"].append(item.model_dump(exclude_defaults=True))
                        dom = await _save_rendered_page(browser)
                        summary = await interaction.summarize()
                        controls = await _login_controls(page, payload)
                        receipts = await _interaction_response_receipts(interaction.requests)
                        har = re.search(r"\[Full HAR saved to ([^\]]+)\]", summary)
                        if har:
                            for receipt in receipts:
                                receipt["evidence_ref"] = har.group(1)
                        set_browser_interaction_receipts(receipts)
                        if observations is not None:
                            requests = [
                                {
                                    "method": request.method,
                                    "url": observation_url(request.url),
                                    "request_content_type": request.headers.get("content-type", ""),
                                }
                                for request in interaction.requests
                            ]
                            observations.record(
                                str(page.url),
                                controls,
                                item.model_dump(exclude_defaults=True),
                                before_url,
                                extract_artifact_references(dom),
                                requests,
                            )
                    try:
                        await capture()
                    except ValueError:
                        pass  # Incomplete context is ordinary multi-step login progress.
                    return dom, summary, controls
                except BrowserTimeoutError:
                    state = "navigation"
                    if item.action != "navigate":
                        state = "control_inspection" if control is None else await _login_control_state(control)
                    tables = _control_tables(await _login_controls(page, payload))
                    raise ValueError(
                        f"Login action timed out after {browser.default_timeout / 1000:g}s; state={state}.\n{tables}"
                    ) from None
                finally:
                    await page.unroute("**/*", guard_navigation)

            dom, summary, controls = await browser.run_in_browser_loop(execute)
        references = re.findall(r"\[Full HAR saved to ([^\]]+)\]", summary)
        attempt.setdefault("evidence_refs", []).extend(
            [
                *extract_artifact_references(dom),
                *(f"artifact:{path}" for path in references),
            ]
        )
        return json.dumps(
            {
                "status": "performed",
                "completed_actions": len(attempt["replay_actions"]),
                "page_artifact": dom,
                "network_artifacts": references,
                "controls": _control_tables(controls),
            },
            ensure_ascii=False,
        )

    return perform_login_action
