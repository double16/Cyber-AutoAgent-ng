"""Controller-owned correlation between checked-out credentials and tool inputs."""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from modules.handlers.tool_recovery import _result_success
from modules.tools.memory import _get_database_store, _operation_id, active_credential_task

_CREDENTIAL_BOOKKEEPING_TOOLS = {
    "checkout_credential",
    "exchange_oauth2_client_credentials",
    "generate_mfa_code",
    "mark_credential_status",
    "prepare_api_key_authentication",
    "prepare_login_form_authentication",
    "query_credentials",
    "request_mfa_code",
    "retrieve_email_mfa_code",
    "set_task_auth_context",
    "store_credential",
}
_SECRET_PAYLOAD_KEYS = {"api_key", "client_secret", "password", "secret"}
_TARGET_FACING_TOOLS = frozenset({
    "http_request",
    "browser_action",
    "execute_shell_command",
    "run_command",
    "curl_request",
})


def _credential_secret_values(payload: dict[str, Any]) -> set[str]:
    """Extract values that prove a downstream tool received a checked-out secret."""

    values: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                visit(nested_value, str(nested_key).lower())
        elif isinstance(value, (list, tuple)):
            for nested_value in value:
                visit(nested_value, key)
        elif key in _SECRET_PAYLOAD_KEYS and isinstance(value, str) and value:
            values.add(value)

    visit(payload)
    return values


def _input_values(value: Any) -> set[str]:
    """Return exact string leaves from one tool input, never a serialized substring haystack."""

    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_input_values(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_input_values(item) for item in value)) if value else set()
    return set()


class CredentialUsageHook(HookProvider):
    """Record actual checked-out-secret use after a target-facing tool outcome.

    The hook compares raw, task-local tool input with credential payload secrets in process memory. It persists only
    credential IDs and a success/failure outcome, never the matched value or the tool input.
    """

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        tool_name = str(event.tool_use.get("name") or "")
        if tool_name in _CREDENTIAL_BOOKKEEPING_TOOLS or tool_name not in _TARGET_FACING_TOOLS:
            return
        store = _get_database_store()
        operation_id = _operation_id()
        active_task = active_credential_task(store, operation_id)
        if active_task is None:
            return
        selected_ids = store.credential_ids_selected_by_task(operation_id, active_task.task_uid)
        if not selected_ids:
            return
        tool_values = _input_values(event.tool_use.get("input", {}))
        outcome = "succeeded" if _result_success(event.result, event.exception) else "failed"
        for credential_id in selected_ids:
            record = store.get_credential(credential_id, include_payload=True)
            if record is None:
                continue
            if _credential_secret_values(record["payload"]) & tool_values:
                store.record_credential_usage(
                    operation_id,
                    credential_id,
                    task_uid=active_task.task_uid,
                    outcome=outcome,
                )
