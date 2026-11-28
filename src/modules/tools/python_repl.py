"""SDK Python execution with task-local, completed HTTP call receipts."""

import inspect
import json
import uuid
from typing import Any

from strands import tool
from strands.types.tools import ToolContext
from strands.vended_tools import make_python_repl

_RECEIPT_MARKER = "__CYBER_EXECUTION_RECEIPT__"


def _run_instrumented(code: str, marker: str) -> None:
    """Run inside the child interpreter; emit receipts only after successful execution."""
    import functools
    import importlib
    import json
    from urllib.request import Request

    receipts = []
    patches = []

    def wrap(callback, index):
        @functools.wraps(callback)
        def request(*args, **kwargs):
            response = callback(*args, **kwargs)
            value = kwargs.get("url", kwargs.get("fullurl", args[index] if len(args) > index else ""))
            url = str(value.full_url if isinstance(value, Request) else value)
            if url.startswith(("http://", "https://")):
                receipts.append(url)
            return response

        return request

    for module_name, class_name, method, index in (
        ("urllib.request", "OpenerDirector", "open", 1),
        ("requests.sessions", "Session", "request", 2),
        ("httpx", "Client", "request", 2),
    ):
        try:
            owner = getattr(importlib.import_module(module_name), class_name)
        except ImportError:
            continue
        original = getattr(owner, method)
        patches.append((owner, method, original))
        setattr(owner, method, wrap(original, index))
    try:
        exec(compile(code, "<python_repl>", "exec"), {"__name__": "__main__"})
        if receipts:
            print("\n" + marker + json.dumps(receipts))
    finally:
        for owner, method, original in reversed(patches):
            setattr(owner, method, original)


class _ReceiptSandbox:
    """Delegate SDK execution after adding child-process HTTP instrumentation."""

    def __init__(self, sandbox: Any, marker: str) -> None:
        self.sandbox = sandbox
        self.marker = marker

    async def execute_code(self, code: str, language: str, *, timeout: int, cancel_signal: Any = None):
        source = inspect.getsource(_run_instrumented)
        instrumented = f"{source}\n_run_instrumented({code!r}, {self.marker!r})\n"
        return await self.sandbox.execute_code(instrumented, language, timeout=timeout, cancel_signal=cancel_signal)


@tool(context="tool_context")
async def python_repl(code: str, tool_context: ToolContext, timeout: int = 120) -> dict[str, Any]:
    """Run Python in a fresh interpreter and record completed HTTP requests.

    Files persist between calls; variables and imports do not. Timeout must be positive.
    """
    marker = f"__CYBER_HTTP_{uuid.uuid4().hex}__"
    runner = make_python_repl(sandbox=_ReceiptSandbox(tool_context.agent.sandbox, marker))
    result = await runner(code=code, tool_context=tool_context, timeout=timeout)
    receipts = []
    output = []
    for line in result["output"].splitlines(keepends=True):
        if line.startswith(marker):
            receipts.extend(json.loads(line[len(marker) :]))
        else:
            output.append(line)
    result["output"] = "".join(output)
    text = json.dumps(result)
    if result["exit_code"] == 0 and receipts:
        payload = {
            "subjects": list(dict.fromkeys(receipts)),
            "request_count": len(receipts),
            "collection": len(set(receipts)) >= 2,
        }
        text += "\n" + _RECEIPT_MARKER + json.dumps(payload, sort_keys=True)
    return {
        "status": "success" if result["exit_code"] == 0 else "error",
        "content": [{"text": text}],
    }
