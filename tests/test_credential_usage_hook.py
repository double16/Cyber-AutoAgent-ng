from strands.hooks.events import AfterToolCallEvent

from modules.handlers.credential_usage import CredentialUsageHook
from modules.tools.credentials import store_user_credential
from modules.tools.memory import SQLiteApplicationStore, Task
from tests.helpers.acceptance import make_acceptance


def _after(tool_input: dict[str, object], status: str = "success") -> AfterToolCallEvent:
    return AfterToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": "tool-1", "name": "http_request", "input": tool_input},
        invocation_state={},
        result={"status": status, "toolUseId": "tool-1", "content": [{"text": "completed"}]},
    )


def _active_task() -> Task:
    return Task(
        "task-1",
        "Authenticated request",
        "Test the authenticated endpoint",
        make_acceptance("task-1"),
        1,
        "active",
        auth_context={"mode": "authenticated", "credential_ids": ["placeholder"]},
    )


def test_credential_usage_hook_records_only_an_exact_checked_out_secret_match(tmp_path, monkeypatch):
    store = SQLiteApplicationStore(str(tmp_path / "credentials.db"), "logical-target")
    monkeypatch.setattr("modules.tools.credentials._get_database_store", lambda: store)
    monkeypatch.setattr("modules.handlers.credential_usage._get_database_store", lambda: store)
    monkeypatch.setattr("modules.handlers.credential_usage._operation_id", lambda: "op-1")
    credential = store_user_credential(
        operation_id="op-1",
        credential_type="api_key",
        target="https://api.example.test",
        role="reader",
        values={"api_key": "do-not-persist-in-usage", "placement": "header", "name": "X-API-Key"},
    )
    task = _active_task()
    task = Task(
        task.task_uid,
        task.title,
        task.objective,
        task.acceptance,
        task.phase,
        task.status,
        auth_context={"mode": "authenticated", "credential_ids": [credential["credential_id"]]},
    )
    store.store_task("op-1", task)
    store.record_credential_usage("op-1", credential["credential_id"], task_uid=task.task_uid, outcome="selected")

    hook = CredentialUsageHook()
    hook._after_tool(_after({"url": "https://api.example.test/me", "headers": {"X-API-Key": "unrelated"}}))
    assert [entry["outcome"] for entry in store.list_credential_usage("op-1")] == ["selected"]

    hook._after_tool(
        _after({"url": "https://api.example.test/me", "headers": {"X-API-Key": "do-not-persist-in-usage"}})
    )
    usage = store.list_credential_usage("op-1")
    assert [entry["outcome"] for entry in usage] == ["selected", "succeeded"]
    assert "do-not-persist-in-usage" not in str(usage)
