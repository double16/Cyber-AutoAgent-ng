"""Python REPL adapter that records completed HTTP calls for task receipts."""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable
from typing import Any
from urllib.request import Request

from strands_tools import python_repl as _python_repl

TOOL_SPEC = _python_repl.TOOL_SPEC
_RECEIPT_MARKER = "__CYBER_EXECUTION_RECEIPT__"
_PATCH_LOCK = threading.RLock()


def _request_url(value: Any) -> str:
    if isinstance(value, Request):
        return str(value.full_url)
    return str(getattr(value, "url", value) or "")


def _append_receipts(result: Any, receipts: list[str]) -> Any:
    if not receipts or not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return result
    first = content[0]
    if not isinstance(first, dict):
        return result
    payload = {
        "subjects": list(dict.fromkeys(receipts)),
        "request_count": len(receipts),
        "collection": len(set(receipts)) >= 2,
    }
    first["text"] = str(first.get("text") or "") + "\n" + _RECEIPT_MARKER + json.dumps(payload, sort_keys=True)
    return result


def _with_http_receipts(
    callback: Callable[..., Any], receipts: list[str], positional_url_index: int
) -> Callable[..., Any]:
    @functools.wraps(callback)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        response = callback(*args, **kwargs)
        url = kwargs.get("url")
        if url is None and len(args) > positional_url_index:
            url = args[positional_url_index]
        normalized = _request_url(url)
        if normalized.startswith(("http://", "https://")):
            receipts.append(normalized)
        return response

    return wrapped


def python_repl(tool: Any, **kwargs: Any) -> Any:
    """Run the standard REPL while emitting runtime HTTP provenance on success."""

    receipts: list[str] = []
    with _PATCH_LOCK:
        import urllib.request

        original_open = urllib.request.OpenerDirector.open
        urllib.request.OpenerDirector.open = _with_http_receipts(original_open, receipts, 1)
        patches: list[tuple[Any, str, Any]] = [(urllib.request.OpenerDirector, "open", original_open)]
        try:
            try:
                import requests.sessions

                original_request = requests.sessions.Session.request
                requests.sessions.Session.request = _with_http_receipts(original_request, receipts, 2)
                patches.append((requests.sessions.Session, "request", original_request))
            except ImportError:
                pass
            try:
                import httpx

                original_request = httpx.Client.request
                httpx.Client.request = _with_http_receipts(original_request, receipts, 2)
                patches.append((httpx.Client, "request", original_request))
            except ImportError:
                pass
            result = _python_repl.python_repl(tool, **kwargs)
        finally:
            for owner, attribute, original in reversed(patches):
                setattr(owner, attribute, original)
    return _append_receipts(result, receipts)
