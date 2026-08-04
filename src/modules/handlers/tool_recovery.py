"""Authoritative task-executor tool outcomes and bounded failure recovery."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

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
_STARTUP_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcommand not found\b",
        r"\b(?:module|package)notfounderror\b",
        r"\bimporterror\b",
        r"\bno module named\b",
        r"\berror while loading shared libraries\b",
        r"\b(?:shared object|dynamic library) .* (?:not found|cannot open)\b",
        r"\btraceback \(most recent call last\)\b",
    )
)
_PREREQUISITE_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:could not|cannot|failed to) (?:open|read|stat)\b",
        r"\b(?:no such file or directory|file not found)\b",
    )
)
_HTTP_STATUS_LINE_PATTERN = re.compile(r"\bHTTP/\d(?:\.\d)?\s+([1-5]\d{2})\b", re.IGNORECASE)
_CURL_WRITE_OUT_STATUS_PATTERN = re.compile(r"(?m)^\s*(?:http_code=|status=)?([1-5]\d{2})(?:\s|$)")
_REDIRECT_BODY_FAILURE_PATTERN = re.compile(
    r"(?:response\.)?body.*(?:unavailable|not available).*redirect|redirect responses?", re.IGNORECASE
)

_MUTATING_TOOLS = {
    "create_tasks",
    "record_finding_validation",
    "record_task_acceptance",
    "store_finding",
    "store_knowledge",
    "store_observation",
}
_STRUCTURED_CORRECTABLE_TOOLS = {
    "record_finding_validation",
    "store_finding",
    "store_knowledge",
    "store_observation",
}
_STRUCTURED_VALIDATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bat least one\b.*\brequired\b",
        r"\bdoes not exist\b",
        r"\boutside the current operation\b",
        r"\bunknown finding_uid\b",
        r"\brequires?\b.*\b(?:artifact|evidence|reference|field|value)\b",
        r"\bmust (?:be|contain|describe|include|use)\b",
    )
)
_READ_ONLY_TOOLS = {"mem0_retrieve", "read_artifact", "tool_catalog"}
_DIAGNOSTIC_EXECUTABLES = {"command", "find", "ls", "stat", "test", "type", "which"}
_SENSITIVE_KEYS = {"api_key", "authorization", "cookie", "password", "secret", "token"}
TOOL_RECOVERY_EXHAUSTED_STATE_KEY = "tool_recovery_exhausted"


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
    while parts:
        if "=" in parts[0] and not parts[0].startswith(("/", "./", "../")):
            parts.pop(0)
            continue
        if parts[0] in {"env", "sudo"}:
            parts.pop(0)
            continue
        break
    return parts[0].rsplit("/", 1)[-1] if parts else ""


def _is_diagnostic_tool(tool_name: str, tool_input: Any) -> bool:
    if tool_name != "shell" or not isinstance(tool_input, dict):
        return False
    return _shell_executable(tool_input) in _DIAGNOSTIC_EXECUTABLES


def is_correctable_tool_failure(tool_name: str, tool_input: Any, output: str) -> bool:
    """Return whether a failed task invocation can reasonably be corrected locally."""

    if _is_diagnostic_tool(tool_name, tool_input):
        return False
    if tool_name in {"read_artifact", "http_request"}:
        return True
    if _REDIRECT_BODY_FAILURE_PATTERN.search(output):
        return True
    if any(pattern.search(output) for pattern in _CORRECTABLE_ERROR_PATTERNS):
        return True
    return tool_name in _STRUCTURED_CORRECTABLE_TOOLS and any(
        pattern.search(output) for pattern in _STRUCTURED_VALIDATION_PATTERNS
    )


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
        redacted_input = _redacted_input(tool_input)
        if tool_name == "create_tasks":
            try:
                input_summary = json.dumps(redacted_input, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                input_summary = str(redacted_input)
        else:
            input_summary = str(redacted_input)
        outcome = ToolOutcome(
            sequence=self._sequence,
            tool_use_id=_bounded_text(tool_use_id, 100),
            tool_name=_bounded_text(tool_name, 100),
            success=success,
            correctable=correctable,
            input_summary=_bounded_text(input_summary, 6000 if tool_name == "create_tasks" else 500),
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
    """Bound retries for one failed invocation without locking unrelated task work."""

    def __init__(
        self,
        journal: ToolOutcomeJournal,
        max_policy_violations: int = 2,
        max_corrections: int = 2,
        quarantine_callback: Optional[Callable[[str], List[str]]] = None,
        quarantined_executables: Optional[set[str]] = None,
        efficiency_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.journal = journal
        self.max_policy_violations = max(1, int(max_policy_violations))
        self.max_corrections = max(1, int(max_corrections))
        self.quarantine_callback = quarantine_callback
        self.unresolved = False
        self.exhausted = False
        self.failed_tool_name = ""
        self.failed_executable = ""
        self.failed_input_fingerprint = ""
        self.failed_output = ""
        self.failure_category = ""
        self.alternative_executables: List[str] = []
        self.quarantined_executables = quarantined_executables if quarantined_executables is not None else set()
        self.efficiency_callback = efficiency_callback
        self._correction_attempts = 0
        self._policy_violations = 0
        self._recovery_roles: Dict[str, str] = {}
        self._artifact_retry_used = False

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_name = str(tool_use.get("name", "unknown"))
        tool_id = str(tool_use.get("toolUseId", tool_use.get("_toolUseId", "")))
        tool_input = tool_use.get("input", {})
        executable = _shell_executable(tool_input) if tool_name == "shell" and isinstance(tool_input, dict) else ""
        if executable and executable in self.quarantined_executables:
            alternatives = ", ".join(self.alternative_executables)
            suffix = f" Available capability-compatible alternatives: {alternatives}." if alternatives else ""
            self._block(
                event,
                tool_id,
                f"Executable '{executable}' is quarantined for this operation.{suffix}",
            )
            return
        if not self.unresolved:
            return
        if self.failure_category == "artifact_unavailable" and tool_name == "read_artifact":
            if self._artifact_retry_used:
                self._block(
                    event,
                    tool_id,
                    "Artifact reading is unavailable for this task. Use an existing durable reference or collect "
                    "bounded alternate evidence; do not call read_artifact again.",
                )
                return
        if self.failure_category == "redirect_response_unavailable" and tool_name == self.failed_tool_name:
            self._block(
                event,
                tool_id,
                "The redirect response body is unavailable. Use the resolved URL or an HTTP request instead of "
                "repeating this browser-body call.",
            )
            return
        if tool_name == self.failed_tool_name and _input_fingerprint(tool_input) == self.failed_input_fingerprint:
            self._block(event, tool_id, "Do not repeat the identical failed invocation; change its input or method.")
            return
        if self._is_correction(tool_name, tool_input):
            if self._correction_attempts >= self.max_corrections:
                self.exhausted = True
                self._block(event, tool_id, "The configured correction allowance has been exhausted.")
                return
            self._correction_attempts += 1
            self._recovery_roles[tool_id] = "correction"
            if self.efficiency_callback is not None:
                self.efficiency_callback("tool_correction")
            return
        if self._is_diagnostic(tool_name, tool_input):
            self._recovery_roles[tool_id] = "diagnostic"
        elif self._is_read_only(tool_name):
            self._recovery_roles[tool_id] = "read_only"
        elif tool_name == "shell" or tool_name not in _MUTATING_TOOLS | {"editor", "python_repl"}:
            self._recovery_roles[tool_id] = "alternative"
        else:
            self._recovery_roles[tool_id] = "independent"

    def _block(self, event: BeforeToolCallEvent, tool_id: str, message: str) -> None:
        event.cancel_tool = message
        self._recovery_roles[tool_id] = "blocked"
        self._policy_violations += 1
        if self.efficiency_callback is not None:
            self.efficiency_callback("tool_policy_recovery")
        if self.exhausted or self._policy_violations >= self.max_policy_violations:
            self.exhausted = True
            self._stop_event_loop(event, "policy_violation_limit")

    def _stop_event_loop(self, event: BeforeToolCallEvent | AfterToolCallEvent, reason: str) -> None:
        request_state = event.invocation_state.setdefault("request_state", {})
        if not isinstance(request_state, dict):
            return
        request_state["stop_event_loop"] = True
        request_state[TOOL_RECOVERY_EXHAUSTED_STATE_KEY] = {
            "reason": reason,
            "policy_violations": self._policy_violations,
            "max_policy_violations": self.max_policy_violations,
            "failed_tool": self.failed_tool_name,
        }

    def _is_correction(self, tool_name: str, tool_input: Any) -> bool:
        if tool_name != self.failed_tool_name:
            return False
        if tool_name != "shell":
            return True
        executable = _shell_executable(tool_input) if isinstance(tool_input, dict) else ""
        if not self.failed_executable:
            return bool(executable and executable not in _DIAGNOSTIC_EXECUTABLES)
        return executable == self.failed_executable

    @staticmethod
    def _is_read_only(tool_name: str) -> bool:
        return tool_name in _READ_ONLY_TOOLS

    @staticmethod
    def _is_diagnostic(tool_name: str, tool_input: Any) -> bool:
        return _is_diagnostic_tool(tool_name, tool_input)

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
        startup_failure = (
            not success
            and tool_name == "shell"
            and any(pattern.search(output) for pattern in _STARTUP_FAILURE_PATTERNS)
        )
        correctable = tool_name != "record_task_acceptance" and not success and (
            startup_failure or is_correctable_tool_failure(tool_name, tool_input, output)
        )
        self.journal.append(
            tool_use_id=tool_id,
            tool_name=tool_name,
            success=success,
            correctable=correctable,
            tool_input=tool_input,
            output=output,
            recovery_role=role,
        )

        if startup_failure:
            executable = _shell_executable(tool_input) if tool_name == "shell" else ""
            if executable:
                self.quarantined_executables.add(executable)
                if self.quarantine_callback is not None:
                    self.alternative_executables = self.quarantine_callback(executable)
            if executable == self.failed_executable:
                self.unresolved = False
            return
        if role == "correction":
            if success:
                self.unresolved = False
                self.exhausted = False
                self._policy_violations = 0
            elif self.failure_category == "artifact_unavailable":
                self._artifact_retry_used = True
            elif self._correction_attempts >= self.max_corrections:
                self.exhausted = True
                self._stop_event_loop(event, "correction_failed")
            return
        if role == "alternative" and success:
            self.unresolved = False
            self.exhausted = False
            return
        if role == "independent" and success and tool_name == "record_task_acceptance":
            self.unresolved = False
            self.exhausted = False
            return
        if role in {"blocked", "diagnostic", "read_only", "independent", "alternative"}:
            return
        if correctable:
            category = self._failure_category(tool_name, tool_input, output)
            self.unresolved = True
            self.exhausted = False
            self.failure_category = category
            self.failed_tool_name = tool_name
            self.failed_executable = (
                _shell_executable(tool_input) if tool_name == "shell" and isinstance(tool_input, dict) else ""
            )
            self.failed_input_fingerprint = _input_fingerprint(tool_input)
            self.failed_output = _bounded_text(output)
            self._correction_attempts = 0
            self._policy_violations = 0
            self._artifact_retry_used = False

    @staticmethod
    def _failure_category(tool_name: str, tool_input: Any, output: str) -> str:
        if tool_name == "read_artifact":
            return "artifact_unavailable"
        if _REDIRECT_BODY_FAILURE_PATTERN.search(output):
            return "redirect_response_unavailable"
        if tool_name == "http_request":
            return "repeated_http_failure"
        if tool_name == "shell" and any(pattern.search(output) for pattern in _STARTUP_FAILURE_PATTERNS):
            return "startup_failure"
        if tool_name == "shell" and any(pattern.search(output) for pattern in _PREREQUISITE_FAILURE_PATTERNS):
            return "missing_prerequisite"
        if tool_name == "shell":
            return "repeated_shell_failure"
        return "invalid_invocation"

    def recovery_guidance(self, tool_catalog_context: str = "") -> str:
        if self.failure_category == "artifact_unavailable":
            guidance = (
                "An artifact could not be read. Make at most one changed read_artifact call using a canonical "
                "artifact: reference. If it still fails, do not read that artifact again; use an existing durable "
                "reference or capture bounded alternate evidence before recording acceptance. "
                f"Failed artifact read: {self.failed_output}"
            )
        elif self.failure_category == "redirect_response_unavailable":
            guidance = (
                "A redirect response has no readable body. Do not repeat the browser-body call. Use the resolved "
                "URL or an HTTP request to capture the redirect status, location, and final response as evidence. "
                f"Failed tool: {self.failed_tool_name}. Error: {self.failed_output}"
            )
        elif self.failure_category == "repeated_http_failure":
            guidance = (
                "The HTTP request failed. Do not repeat the identical request. Use one corrected URL/route or a "
                "different registered HTTP-capable method, then continue only with evidence from the changed call. "
                f"Error: {self.failed_output}"
            )
        elif self.failure_category == "repeated_shell_failure":
            guidance = (
                "The shell command failed. Do not repeat the identical command. Use one corrected command or a "
                "capability-compatible registered tool, then continue only with observed output from that changed "
                f"action. Failed tool: {self.failed_tool_name}. Error: {self.failed_output}"
            )
        elif self.failure_category in {"startup_failure", "missing_prerequisite"}:
            guidance = (
                "The shell invocation cannot proceed as issued. Do not repeat the identical command. Use one "
                "corrected command or a capability-compatible registered tool, then continue only with its observed "
                f"output. Error: {self.failed_output}"
            )
        else:
            guidance = (
                "A tool invocation failed and needs bounded correction. Do not claim output from the failed call. "
                "You may inspect or create missing prerequisites, use a different method, continue independent work, "
                f"or make up to {self.max_corrections} changed retries of the failed invocation. "
                f"Failure category: {self.failure_category}. Failed tool: {self.failed_tool_name}. "
                f"Error: {self.failed_output}"
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
