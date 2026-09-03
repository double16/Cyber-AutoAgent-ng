from urllib.request import Request

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


def test_python_repl_receipt_helpers_handle_request_variants_and_unusable_results():
    receipts = []
    wrapped = python_repl._with_http_receipts(lambda *_args, **_kwargs: "response", receipts, 1)

    assert python_repl._request_url(Request("https://target.test/request")) == "https://target.test/request"
    assert wrapped("GET", "https://target.test/positional") == "response"
    assert wrapped(url="ftp://target.test/not-recorded") == "response"
    assert receipts == ["https://target.test/positional"]

    assert python_repl._append_receipts("not-a-result", receipts) == "not-a-result"
    assert python_repl._append_receipts({"content": []}, receipts) == {"content": []}
    assert python_repl._append_receipts({"content": ["invalid"]}, receipts) == {"content": ["invalid"]}


def test_python_repl_receipt_payload_deduplicates_subjects_and_tracks_collection():
    result = {"content": [{"text": "completed"}]}

    python_repl._append_receipts(result, ["https://one.test", "https://one.test", "https://two.test"])

    assert '"collection": true' in result["content"][0]["text"]
    assert '"request_count": 3' in result["content"][0]["text"]
