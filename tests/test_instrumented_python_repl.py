import asyncio
import json
from types import SimpleNamespace

import pytest
from strands.sandbox.types import ExecutionResult
from strands.vended_tools.python_repl.types import PythonReplError

from modules.tools.python_repl import _run_instrumented, python_repl


class LocalSandbox:
    async def execute_code(self, code, language, *, timeout, cancel_signal=None):
        process = await asyncio.create_subprocess_exec(
            "./.venv/bin/python",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        return ExecutionResult(stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode)


def context(sandbox=None):
    return SimpleNamespace(agent=SimpleNamespace(sandbox=sandbox or LocalSandbox()), cancel_signal=None)


@pytest.mark.asyncio
async def test_completed_calls_emit_deduplicated_receipts_in_child_process():
    # Mock the underlying HTTP transport in the child before installing instrumentation.
    class Sandbox(LocalSandbox):
        async def execute_code(self, code, language, *, timeout, cancel_signal=None):
            prelude = "import requests.sessions\nrequests.sessions.Session.request = lambda *a, **k: object()\n"
            return await super().execute_code(prelude + code, language, timeout=timeout)

    result = await python_repl(
        code='import requests; s=requests.Session(); s.get("https://one.test"); '
        's.get("https://one.test"); s.get("https://two.test"); print("completed")',
        tool_context=context(Sandbox()),
    )
    assert result["status"] == "success"
    output, receipt = result["content"][0]["text"].split("\n__CYBER_EXECUTION_RECEIPT__")
    assert "completed" in json.loads(output)["output"]
    assert json.loads(receipt) == {
        "subjects": ["https://one.test", "https://two.test"],
        "request_count": 3,
        "collection": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("code, status", [("print('ok')", "success"), ("raise ValueError('failed')", "error")])
async def test_no_receipts_without_completed_http_calls(code, status):
    result = await python_repl(code=code, tool_context=context())
    assert result["status"] == status
    assert "__CYBER_EXECUTION_RECEIPT__" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_fresh_interpreters_and_invalid_timeout():
    await python_repl(code="x = 1", tool_context=context())
    result = await python_repl(code="print(x)", tool_context=context())
    assert result["status"] == "error"
    with pytest.raises(ValueError, match="positive"):
        await python_repl(code="pass", timeout=0, tool_context=context())


@pytest.mark.asyncio
async def test_execution_failure_propagates():
    class Broken:
        async def execute_code(self, *args, **kwargs):
            raise RuntimeError("cannot execute")

    with pytest.raises(PythonReplError, match="cannot execute"):
        await python_repl(code="pass", tool_context=context(Broken()))


def test_instrumentation_restores_transports_on_failure(monkeypatch, capsys):
    import requests.sessions

    def original(*args, **kwargs):
        return object()

    monkeypatch.setattr(requests.sessions.Session, "request", original)
    with pytest.raises(ValueError):
        _run_instrumented('import requests; requests.get("https://one.test"); raise ValueError()', "marker")
    assert requests.sessions.Session.request is original
    assert "marker" not in capsys.readouterr().out


def test_instrumentation_urllib_and_optional_dependencies(monkeypatch, capsys):
    import importlib
    import urllib.request

    original_import = importlib.import_module

    def optional_import(name):
        if name in {"requests.sessions", "httpx"}:
            raise ImportError(name)
        return original_import(name)

    monkeypatch.setattr(importlib, "import_module", optional_import)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda *args, **kwargs: object())
    _run_instrumented(
        "import urllib.request; o=urllib.request.OpenerDirector(); "
        'o.open(urllib.request.Request("https://one.test")); o.open("ftp://ignored.test"); '
        'o.open(fullurl="https://two.test")',
        "marker",
    )
    assert 'marker["https://one.test", "https://two.test"]' in capsys.readouterr().out
