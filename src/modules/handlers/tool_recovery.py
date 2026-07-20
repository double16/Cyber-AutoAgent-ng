"""Authoritative task-executor tool outcomes and bounded failure recovery."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry


_CORRECTABLE_ERROR_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"command not found",
        r"no such file or directory",
        r"could not (?:open|read)",
        r"cannot (?:open|read)",
        r"invalid (?:argument|option|value)",
        r"(?:unknown|unrecognized) (?:argument|option)",
        r"missing (?:required )?(?:argument|option|value)",
        r"timed? out",
        r"usage error",
        r"validation error",
    )
)
_HTTP_STATUS_LINE_PATTERN = re.compile(r"\bHTTP/\d(?:\.\d)?\s+([1-5]\d{2})\b", re.IGNORECASE)
_CURL_WRITE_OUT_STATUS_PATTERN = re.compile(r"(?m)^\s*(?:http_code=|status=)?([1-5]\d{2})(?:\s|$)")

_MUTATING_TOOLS = {
    "create_tasks",
    "record_finding_validation",
    "store_finding",
    "store_knowledge",
    "store_observation",
}
_READ_ONLY_TOOLS = {"mem0_retrieve", "read_artifact"}
_DIAGNOSTIC_EXECUTABLES = {"command", "find", "ls", "stat", "test", "type", "which"}
_SENSITIVE_KEYS = {"api_key", "authorization", "cookie", "password", "secret", "token"}


def _bounded_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _redacted_input(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redacted_input(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redacted_input(item) for item in value]
    return value


def _input_fingerprint(value: Any) -> str:
    """Return a deterministic redacted fingerprint for tool input."""

    try:
        canonical = json.dumps(_redacted_input(value), sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canonical = str(_redacted_input(value))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_text(result: Any) -> str:
    if isinstance(result, Exception):
        return str(result)
    if not isinstance(result, dict):
        return str(result)
    chunks = []
    for item in result.get("content", []):
        if isinstance(item, dict):
            chunks.append(str(item.get("text", item)))
        else:
            chunks.append(str(item))
    return "\n".join(chunks)


def _result_success(result: Any, exception: Optional[Exception] = None) -> bool:
    if exception is not None or isinstance(result, Exception):
        return False
    return not isinstance(result, dict) or result.get("status", "success") != "error"


def is_correctable_tool_failure(tool_name: str, output: str) -> bool:
    """Return whether a failed invocation can reasonably be corrected locally."""

    del tool_name  # Reserved for future tool-specific classifiers.
    return any(pattern.search(output) for pattern in _CORRECTABLE_ERROR_PATTERNS)


def _shell_executable(tool_input: Dict[str, Any]) -> str:
    command = tool_input.get("command", "")
    if isinstance(command, list):
        if not command:
            return ""
        command = command[0].get("command", "") if isinstance(command[0], dict) else command[0]
    try:
        parts = shlex.split(str(command))
    except ValueError:
        return ""
    while parts and "=" in parts[0] and not parts[0].startswith(("/", "./", "../")):
        parts.pop(0)
    if parts and parts[0] in {"env", "sudo"}:
        parts.pop(0)
    return parts[0].rsplit("/", 1)[-1] if parts else ""


def _shell_command_text(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command", "")
    if isinstance(command, list):
        commands = []
        for item in command:
            commands.append(str(item.get("command", "")) if isinstance(item, dict) else str(item))
        return "\n".join(commands)
    return str(command)


def _uses_curl_write_out(tool_input: Any) -> bool:
    command = _shell_command_text(tool_input)
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return "-w" in parts or "--write-out" in parts or "--write-out=" in command


def _captured_http_status(output: str, *, allow_write_out_status: bool = False) -> Optional[str]:
    status_line = _HTTP_STATUS_LINE_PATTERN.search(output)
    if status_line:
        return status_line.group(1)
    if allow_write_out_status:
        write_out = _CURL_WRITE_OUT_STATUS_PATTERN.search(output)
        if write_out:
            return write_out.group(1)
    return None


def _is_interpretable_curl_http_result(tool_name: str, tool_input: Any, output: str) -> bool:
    if tool_name != "shell" or not isinstance(tool_input, dict):
        return False
    if _shell_executable(tool_input) != "curl":
        return False
    return _captured_http_status(output, allow_write_out_status=_uses_curl_write_out(tool_input)) is not None


@dataclass(frozen=True)
class ToolOutcome:
    """A bounded, controller-observed tool result."""

    sequence: int
    tool_use_id: str
    tool_name: str
    success: bool
    correctable: bool
    input_summary: str
    output_summary: str
    recovery_role: str = "normal"


class ToolOutcomeJournal:
    """Bounded per-agent journal used by task execution and evaluation."""

    def __init__(self, max_entries: int = 200) -> None:
        self._entries: Deque[ToolOutcome] = deque(maxlen=max_entries)
        self._sequence = 0

    def __len__(self) -> int:
        return len(self._entries)

    def append(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        success: bool,
        correctable: bool,
        tool_input: Any,
        output: Any,
        recovery_role: str = "normal",
    ) -> ToolOutcome:
        self._sequence += 1
        outcome = ToolOutcome(
            sequence=self._sequence,
            tool_use_id=_bounded_text(tool_use_id, 100),
            tool_name=_bounded_text(tool_name, 100),
            success=success,
            correctable=correctable,
            input_summary=_bounded_text(_redacted_input(tool_input)),
            output_summary=_bounded_text(output),
            recovery_role=recovery_role,
        )
        self._entries.append(outcome)
        return outcome

    def snapshot(self) -> int:
        return self._sequence

    def since(self, sequence: int) -> List[ToolOutcome]:
        return [entry for entry in self._entries if entry.sequence > sequence]

    def entries(self) -> List[ToolOutcome]:
        return list(self._entries)


class TaskFailureRecoveryHook(HookProvider):
    """Block task side effects while allowing one diagnostic and one correction."""

    def __init__(self, journal: ToolOutcomeJournal) -> None:
        self.journal = journal
        self.unresolved = False
        self.exhausted = False
        self.failed_tool_name = ""
        self.failed_executable = ""
        self.failed_input_fingerprint = ""
        self.failed_output = ""
        self._diagnostic_used = False
        self._correction_attempted = False
        self._recovery_roles: Dict[str, str] = {}

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        if not self.unresolved:
            return
        tool_use = event.tool_use
        tool_name = str(tool_use.get("name", "unknown"))
        tool_id = str(tool_use.get("toolUseId", tool_use.get("_toolUseId", "")))
        tool_input = tool_use.get("input", {})

        if self.exhausted:
            event.cancel_tool = "The task's single correction allowance has been exhausted."
            self._recovery_roles[tool_id] = "blocked"
            return
        if self._is_read_only(tool_name):
            self._recovery_roles[tool_id] = "read_only"
            return
        correction_status = self._correction_status(tool_name, tool_input)
        if correction_status == "changed":
            self._mark_correction_or_block(event, tool_id)
            return
        if correction_status == "same_input":
            event.cancel_tool = "The corrected invocation must change the failed input."
            self._recovery_roles[tool_id] = "blocked"
            return
        if tool_name in _MUTATING_TOOLS:
            event.cancel_tool = (
                "Resolve the correctable tool failure before creating tasks or storing durable evidence."
            )
            self._recovery_roles[tool_id] = "blocked"
            return
        if self._diagnostic_used:
            event.cancel_tool = "Only one diagnostic invocation is allowed before the corrected invocation."
            self._recovery_roles[tool_id] = "blocked"
            return
        if self._is_diagnostic(tool_name, tool_input):
            self._diagnostic_used = True
            self._recovery_roles[tool_id] = "diagnostic"
            return
        event.cancel_tool = "Recovery may only use read-only inspection, one diagnostic, and one corrected call."
        self._recovery_roles[tool_id] = "blocked"

    def _mark_correction_or_block(self, event: BeforeToolCallEvent, tool_id: str) -> None:
        if self._correction_attempted:
            event.cancel_tool = "Only one corrected invocation is allowed for this task."
            self._recovery_roles[tool_id] = "blocked"
            return
        self._correction_attempted = True
        self._recovery_roles[tool_id] = "correction"

    def _correction_status(self, tool_name: str, tool_input: Any) -> str:
        if tool_name != self.failed_tool_name:
            return "different_tool"
        if _input_fingerprint(tool_input) == self.failed_input_fingerprint:
            return "same_input"
        if tool_name != "shell":
            return "changed"
        if not isinstance(tool_input, dict):
            return "different_tool"
        executable = _shell_executable(tool_input)
        if not self.failed_executable:
            if executable and executable not in _DIAGNOSTIC_EXECUTABLES:
                return "changed"
            return "different_tool"
        if executable and executable == self.failed_executable and executable not in _DIAGNOSTIC_EXECUTABLES:
            return "changed"
        return "different_tool"

    @staticmethod
    def _is_read_only(tool_name: str) -> bool:
        return tool_name in _READ_ONLY_TOOLS

    @staticmethod
    def _is_diagnostic(tool_name: str, tool_input: Any) -> bool:
        if tool_name != "shell" or not isinstance(tool_input, dict):
            return False
        return _shell_executable(tool_input) in _DIAGNOSTIC_EXECUTABLES

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_name = str(tool_use.get("name", "unknown"))
        tool_id = str(tool_use.get("toolUseId", tool_use.get("_toolUseId", "")))
        tool_input = tool_use.get("input", {})
        output = _result_text(event.result)
        success = _result_success(event.result, event.exception)
        if not success and _is_interpretable_curl_http_result(tool_name, tool_input, output):
            success = True
            status_code = _captured_http_status(output, allow_write_out_status=_uses_curl_write_out(tool_input))
            output = f"{output}\nInterpretable curl response status captured: {status_code}"
        role = self._recovery_roles.pop(tool_id, "normal")
        correctable = not success and is_correctable_tool_failure(tool_name, output)
        self.journal.append(
            tool_use_id=tool_id,
            tool_name=tool_name,
            success=success,
            correctable=correctable,
            tool_input=tool_input,
            output=output,
            recovery_role=role,
        )

        if role == "correction":
            if success:
                self.unresolved = False
                self.exhausted = False
            else:
                self.exhausted = True
            return
        if role in {"blocked", "diagnostic", "read_only"}:
            return
        if correctable:
            self.unresolved = True
            self.exhausted = False
            self.failed_tool_name = tool_name
            self.failed_executable = (
                _shell_executable(tool_input) if tool_name == "shell" and isinstance(tool_input, dict) else ""
            )
            self.failed_input_fingerprint = _input_fingerprint(tool_input)
            self.failed_output = _bounded_text(output)
            self._diagnostic_used = False
            self._correction_attempted = False

    def recovery_guidance(self, tool_catalog_context: str = "") -> str:
        guidance = (
            "A correctable tool invocation failed. Do not claim output from it or create tasks/store evidence until "
            "it is resolved. You may make read-only inspection calls, at most one diagnostic/preflight call, and one "
            "corrected invocation of the failed tool. "
            f"Failed tool: {self.failed_tool_name}. Error: {self.failed_output}"
        )
        tool_catalog_context = str(tool_catalog_context or "").strip()
        if not tool_catalog_context:
            return guidance
        return guidance + "\n\n## Failed Command Help\n" + tool_catalog_context


def outcomes_to_toon(outcomes: Iterable[ToolOutcome]) -> str:
    """Serialize authoritative outcomes compactly for an evaluator prompt."""

    entries = list(outcomes)
    lines = [
        f"tool_outcomes[{len(entries)}]{{sequence,tool_name,status,correctable,recovery_role,input,output}}:"
    ]
    for entry in entries:
        values = (
            entry.sequence,
            entry.tool_name.replace(",", ";"),
            "success" if entry.success else "error",
            str(entry.correctable).lower(),
            entry.recovery_role,
            entry.input_summary.replace("\n", " ").replace(",", ";"),
            entry.output_summary.replace("\n", " ").replace(",", ";"),
        )
        lines.append("  " + ",".join(str(value) for value in values))
    return "\n".join(lines)
