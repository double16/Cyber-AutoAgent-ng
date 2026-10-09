"""Controller-owned browser observations for authentication and registration review."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from strands import tool

from modules.handlers.tool_recovery import _result_success
from modules.handlers.utils import get_tool_description, get_tool_name, get_tool_spec
from modules.tools.artifact_references import extract_artifact_references
from modules.tools.browser import get_browser, latest_browser_interaction_receipts

logger = logging.getLogger(__name__)


def observation_url(value: str) -> str:
    """Exclude query, fragment, and URL credentials from browser review observations."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class BrowserFlowObservations:
    """Keep browser facts on disk and only their references on the worker attempt."""

    def __init__(self, root: str | Path, attempt: dict[str, Any]) -> None:
        self.root = Path(root)
        self.attempt = attempt
        self.directory = self.root / "artifacts" / "flow_observations" / uuid4().hex

    def record(
        self,
        url: str,
        controls: list[dict],
        action: dict,
        before_url: str = "",
        references: list[str] | None = None,
        requests: list[dict] | None = None,
    ) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{uuid4().hex}.json"
        data = {
            "url": observation_url(url),
            "before_url": observation_url(before_url),
            "controls": controls,
            "action": {**action, **({"url": observation_url(action["url"])} if "url" in action else {})},
            "source_refs": references or [],
            "requests": [
                {**request, **({"url": observation_url(request["url"])} if "url" in request else {})}
                for request in requests or []
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        reference = "artifact:" + path.relative_to(self.root).as_posix()
        self.attempt.setdefault("observation_refs", []).append(reference)
        self.attempt.setdefault("evidence_refs", []).append(reference)
        return reference

    def context(self, max_chars: int = 12000) -> str:
        # Import locally so the typed login adapter can use this journal without an import cycle.
        from modules.tools.login_replay import _control_tables

        blocks, omitted, seen = [], [], set()
        remaining = max_chars
        for reference in self.attempt.get("observation_refs", []):
            path = self.root / reference.removeprefix("artifact:")
            data = json.loads(path.read_text(encoding="utf-8"))
            controls = data.pop("controls")
            digest = hashlib.sha256(json.dumps(controls, sort_keys=True).encode()).hexdigest()
            block = json.dumps({"evidence_ref": reference, **data}, ensure_ascii=False)
            if digest not in seen:
                block += "\n" + _control_tables(controls)
            if len(block) <= remaining:
                seen.add(digest)
                blocks.append(block)
                remaining -= len(block)
            else:
                omitted.append(reference)
        return (
            "\nController-recorded browser facts (semantic support remains for review):\n"
            + "\n".join(blocks)
            + "\nObservations omitted for context capacity (available as artifacts): "
            + json.dumps(omitted)
        )

    async def observe(self, action: dict, before_url: str = "", references: list[str] | None = None) -> str:
        from modules.tools.login_replay import _login_controls

        async with get_browser() as browser:

            async def snapshot() -> str:
                controls = await _login_controls(browser.page, {})
                return self.record(
                    str(browser.page.url),
                    controls,
                    action,
                    before_url,
                    references,
                    latest_browser_interaction_receipts()
                    if action.get("tool_name") == "browser_perform_action" else [],
                )

            return await browser.run_in_browser_loop(snapshot)


def observe_browser_tool(original: Any, observations: BrowserFlowObservations) -> Any:
    """Observe successful browser interactions without recording prose action arguments or input values."""

    async def observed_tool(**arguments: Any) -> Any:
        result = original(**arguments)
        if inspect.isawaitable(result):
            result = await result
        if _result_success(result):
            try:
                await observations.observe(
                    {"tool_name": get_tool_name(original)}, references=extract_artifact_references(str(result))
                )
            except Exception as error:
                # Observation availability must not replace a successful business action with a tool failure.
                logger.warning("Browser flow observation unavailable: %s", type(error).__name__)
        return result

    observed_tool.__name__ = get_tool_name(original)
    observed_tool.__doc__ = get_tool_description(original)
    observed_tool.__signature__ = inspect.signature(original)
    observed_tool.__annotations__ = inspect.get_annotations(original)
    return tool(observed_tool, name=get_tool_name(original), inputSchema=get_tool_spec(original)["inputSchema"])
