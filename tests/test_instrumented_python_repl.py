from modules.tools import python_repl


def test_python_repl_emits_completed_requests_as_runtime_receipt(monkeypatch):
    import requests.sessions

    def fake_request(self, method, url, **kwargs):
        return object()

    def fake_repl(tool, **kwargs):
        requests.sessions.Session().get("http://target.test/api")
        return {
            "toolUseId": tool["toolUseId"],
            "status": "success",
            "content": [{"text": "completed"}],
        }

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    monkeypatch.setattr(python_repl._python_repl, "python_repl", fake_repl)

    result = python_repl.python_repl({"toolUseId": "python-1", "input": {"code": "..."}})

    assert "__CYBER_EXECUTION_RECEIPT__" in result["content"][0]["text"]
    assert "http://target.test/api" in result["content"][0]["text"]


def test_python_repl_does_not_emit_receipt_without_runtime_http_call(monkeypatch):
    def fake_repl(tool, **kwargs):
        return {
            "toolUseId": tool["toolUseId"],
            "status": "success",
            "content": [{"text": "completed"}],
        }

    monkeypatch.setattr(python_repl._python_repl, "python_repl", fake_repl)

    result = python_repl.python_repl({"toolUseId": "python-1", "input": {"code": "..."}})

    assert result["content"][0]["text"] == "completed"
