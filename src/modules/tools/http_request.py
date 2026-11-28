"""Raw HTTP requests that retain negative-control response bodies."""

import math

import httpx
from strands import tool
from strands.types.tools import ToolContext

_HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@tool(context="tool_context")
async def http_request(
    method: str,
    url: str,
    tool_context: ToolContext | None = None,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: float = 30,
) -> dict:
    """Make an HTTP request, retaining status, headers, and body for every HTTP status.

    Transport failures remain errors. Requests honor HTTP(S) proxy environment variables.
    Redirect responses are returned directly so their headers and bodies remain observable.
    Methods are GET, POST, PUT, DELETE, PATCH, HEAD, or OPTIONS (case-insensitive).
    Timeout must be finite and positive, in seconds; it bounds each network operation.
    """
    method = method.strip().upper()
    if method not in _HTTP_METHODS:
        raise ValueError(f"Unsupported HTTP method: {method}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("HTTP timeout must be finite and positive")
    signal = tool_context.cancel_signal if tool_context is not None else None

    def check_cancelled() -> None:
        if signal is not None and signal.is_set():
            raise RuntimeError("HTTP request cancelled")

    check_cancelled()
    async with httpx.AsyncClient(timeout=timeout, trust_env=True, follow_redirects=False) as client:
        async with client.stream(method, url, headers=headers, content=body) as response:
            chunks = []
            async for chunk in response.aiter_bytes():
                check_cancelled()
                chunks.append(chunk)
            check_cancelled()
            text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            return {
                "status": response.status_code,
                "status_text": response.reason_phrase,
                "headers": dict(response.headers),
                "body": text,
            }


# Keep canonical schema values while accepting common casing variants at the runtime boundary.
http_request.tool_spec["inputSchema"]["json"]["properties"]["method"]["enum"] = _HTTP_METHODS
