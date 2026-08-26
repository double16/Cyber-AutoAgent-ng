"""Authoritative task-executor tool outcomes and bounded failure recovery."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from modules.tools.shell_provenance import shell_execution_provenance

logger = logging.getLogger(__name__)


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
_EXECUTION_RECEIPT_MARKER = "__CYBER_EXECUTION_RECEIPT__"
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
_READ_ONLY_TOOLS = {"memory_retrieve", "read_artifact", "tool_catalog"}
_DIAGNOSTIC_EXECUTABLES = {"command", "find", "ls", "stat", "test", "type", "which"}
_SENSITIVE_KEYS = {"api_key", "authorization", "cookie", "password", "secret", "token"}
TOOL_RECOVERY_EXHAUSTED_STATE_KEY = "tool_recovery_exhausted"
EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY = "evaluator_artifact_read_limit_exhausted"
ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER = "ARTIFACT_TOTAL_READ_LIMIT_REACHED"
ARTIFACT_PAGE_LIMIT_REACHED_MARKER = "ARTIFACT_PAGE_LIMIT_REACHED"
ARTIFACT_READ_POLICY_VIOLATION_MARKER = "ARTIFACT_READ_POLICY_VIOLATION"


class EvaluatorArtifactReadLimitExceeded(RuntimeError):
    """Raised after an evaluator ignores bounded artifact-read guidance twice."""


def _bounded_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _value_fingerprint(value: Any) -> str:
    """Return a stable hash without retaining the full value in the outcome journal."""

    try:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canonical = str(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    if not isinstance(result, dict):
        return True
    return str(result.get("status", "success")).lower() in {"success", "ok"}


def _artifact_references(*values: Any) -> tuple[str, ...]:
    """Capture canonical artifact references from tool results before summaries are bounded."""

    references = []

    def add(reference: Any) -> None:
        value = str(reference or "").strip()
        if value and value not in references:
            references.append(value)

    def visit(value: Any, field_name: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, str(key).lower())
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item, field_name)
            return
        text = str(value or "")
        if field_name == "artifact_id" and text and not text.startswith("artifact_id:"):
            add(f"artifact_id:{text}")
        elif field_name in {"artifact", "artifact_ref"} and text.startswith(("artifact:", "artifact_id:")):
            add(text)
        for reference in re.findall(r"(?:artifact|artifact_id):[^\s\\\]\}\)\"']+", text):
            add(reference)

    for value in values:
        visit(value)
    return tuple(references[:16])


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
    if tool_name == "shell" and "service target is outside the assigned task boundary" in str(output).lower():
        return True
    if tool_name in {"read_artifact", "http_request"}:
        return True
    if _REDIRECT_BODY_FAILURE_PATTERN.search(output):
        return True
    if any(pattern.search(output) for pattern in _CORRECTABLE_ERROR_PATTERNS):
        return True
    return tool_name in _STRUCTURED_CORRECTABLE_TOOLS and any(
        pattern.search(output) for pattern in _STRUCTURED_VALIDATION_PATTERNS
    )


def format_tool_repair_error(tool_name: str, output: str) -> str:
    """Return concise, actionable tool failures without exposing raw validator diagnostics to the agent."""

    normalized = str(output or "").lower()
    if tool_name == "shell" and "service target is outside the assigned task boundary" in normalized:
        return (
            "The controller rejected the command before execution because its target is outside the assigned task "
            "boundary. Use the concrete allowed target and corrected command stated in the rejection; do not repeat "
            "the rejected command or broaden the task. Controller rejection: " + _bounded_text(output, 900)
        )
    if tool_name == "store_finding":
        if "evidence assertion" in normalized and "marker" not in normalized:
            return (
                "STORE_FINDING_REPAIR_MISSING_EVIDENCE_ASSERTIONS: Call read_artifact for one cited artifact, "
                "then retry store_finding with evidence_assertions: "
                "[{\"artifact\":\"artifact:artifacts/<file>\",\"marker\":\"<verbatim positive text>\"}]. "
                "Do not call record_task_acceptance until store_finding succeeds and returns finding:<id>."
            )
        if "marker" in normalized and "not found" in normalized:
            return (
                "STORE_FINDING_REPAIR_MARKER_NOT_FOUND: The submitted marker is not evidence and must not be "
                "reused. Call read_artifact for the cited artifact, then retry store_finding once with a changed "
                "payload containing a verbatim positive marker. Do not call record_task_acceptance until it "
                "returns finding:<id>."
            )
        if "validation failed for input parameters" in normalized or "field required" in normalized:
            return (
                "STORE_FINDING_REPAIR_SCHEMA: The legacy content/metadata payload is invalid. Retry store_finding "
                "with title, claim, severity, target, technique, expected_result, observed_result, "
                "reproduction_steps, artifacts, and evidence_assertions. Do not call record_task_acceptance until "
                "store_finding returns finding:<id>."
            )
        if "artifact" in normalized:
            return (
                "STORE_FINDING_REPAIR_ARTIFACT: Use a canonical existing artifact reference that is also listed "
                "in artifacts, read it if needed, and include a verbatim positive evidence_assertions marker. "
                "Do not call record_task_acceptance until store_finding returns finding:<id>."
            )
    if tool_name == "record_task_acceptance":
        if "task_evidence_snapshot_source_unavailable" in normalized:
            return (
                "RECORD_TASK_ACCEPTANCE_SOURCE_ARTIFACT_REPAIR: The controller retained this acceptance "
                "submission and will run one bounded task-local evidence regeneration repair. Do not retry "
                "acceptance in this turn."
            )
        if "task_evidence_snapshot_destination_unverifiable" in normalized:
            return (
                "RECORD_TASK_ACCEPTANCE_SNAPSHOT_DESTINATION_FAILED: The controller could not verify the "
                "immutable task-evidence destination. Do not retry acceptance; the task will be marked "
                "partial_failure and the operation will continue."
            )
        if "task_evidence_snapshot_verification_failed" in normalized:
            return (
                "RECORD_TASK_ACCEPTANCE_SNAPSHOT_VERIFICATION_FAILED: The controller could not verify the "
                "immutable task-evidence copy. Do not retry acceptance in this task. The task will be marked "
                "partial_failure and the operation will continue. Controller error: "
                + _bounded_text(output, 900)
            )
        if "requires a finding created by this task" in normalized:
            return (
                "RECORD_TASK_ACCEPTANCE_REPAIR_FINDING_PREREQUISITE: The preceding finding submission did not "
                "persist. Do not retry acceptance. Repair store_finding first and use its returned finding:<id> "
                "reference only after it succeeds."
            )
        if "evidence_refs required" in normalized:
            eligible_match = re.search(r"eligible_evidence_refs=([^\n]+)", str(output or ""))
            eligible_suffix = (
                f" Eligible current-task references: {eligible_match.group(1).strip()}."
                if eligible_match and eligible_match.group(1).strip() != "none"
                else ""
            )
            return (
                "RECORD_TASK_ACCEPTANCE_REPAIR_EVIDENCE_REFS: Retry record_task_acceptance with at least one "
                "canonical durable evidence reference from this task, such as artifact:artifacts/<file>."
                + eligible_suffix
            )
    return str(output or "")


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


def _execution_receipts(tool_name: str, tool_input: Any, output: Any) -> tuple[ExecutionReceipt, ...]:
    """Extract deterministic request provenance without interpreting agent prose."""

    payload = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in {"http_request", "browser_goto_url"}:
        url = str(payload.get("url") or "").strip()
        return (ExecutionReceipt(tool_name, (url,), 1),) if url else ()
    if tool_name == "shell":
        command = str(payload.get("command") or payload.get("cmd") or "")
        provenance = shell_execution_provenance(command)
        subjects = tuple(dict.fromkeys([*provenance.urls, *provenance.collection_urls]))
        if not subjects:
            return ()
        return (
            ExecutionReceipt(
                "shell",
                subjects,
                max(len(provenance.collection_urls), len(provenance.urls)),
                len(provenance.collection_urls) >= 2,
            ),
        )
    if tool_name != "python_repl":
        return ()

    receipts = []
    for line in str(output or "").splitlines():
        if not line.startswith(_EXECUTION_RECEIPT_MARKER):
            continue
        try:
            value = json.loads(line[len(_EXECUTION_RECEIPT_MARKER) :])
        except (TypeError, ValueError):
            continue
        subjects = tuple(
            str(subject).strip()
            for subject in value.get("subjects", [])
            if str(subject).strip().startswith(("http://", "https://"))
        )
        request_count = value.get("request_count", 0)
        if not subjects or not isinstance(request_count, int) or request_count < 1:
            continue
        receipts.append(ExecutionReceipt("python_runtime", subjects, request_count, bool(value.get("collection"))))
    return tuple(receipts)


@dataclass(frozen=True)
class ExecutionReceipt:
    """Controller-observed network activity associated with one tool result."""

    source: str
    subjects: tuple[str, ...]
    request_count: int
    collection: bool = False


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
    input_fingerprint: str = ""
    output_fingerprint: str = ""
    raw_output_summary: str = ""
    artifact_refs: tuple[str, ...] = ()
    structured_input: Optional[Dict[str, Any]] = None
    execution_receipts: tuple[ExecutionReceipt, ...] = ()


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
        raw_output: Any = None,
        recovery_role: str = "normal",
    ) -> ToolOutcome:
        self._sequence += 1
        redacted_input = _redacted_input(tool_input)
        if tool_name in {"create_tasks", "record_task_acceptance"}:
            try:
                input_summary = json.dumps(redacted_input, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                input_summary = str(redacted_input)
        else:
            input_summary = str(redacted_input)
        artifact_refs = _artifact_references(output, raw_output)
        execution_receipts = _execution_receipts(tool_name, redacted_input, output)
        outcome = ToolOutcome(
            sequence=self._sequence,
            tool_use_id=_bounded_text(tool_use_id, 100),
            tool_name=_bounded_text(tool_name, 100),
            success=success,
            correctable=correctable,
            input_summary=_bounded_text(input_summary, 6000 if tool_name == "create_tasks" else 500),
            output_summary=_bounded_text(output),
            recovery_role=recovery_role,
            input_fingerprint=_value_fingerprint(redacted_input),
            output_fingerprint=_value_fingerprint(output),
            raw_output_summary=_bounded_text(raw_output if raw_output is not None else output),
            artifact_refs=artifact_refs,
            structured_input=(
                redacted_input
                if isinstance(redacted_input, dict)
                else None
            ),
            execution_receipts=execution_receipts,
        )
        self._entries.append(outcome)
        return outcome

    def snapshot(self) -> int:
        return self._sequence

    def since(self, sequence: int) -> List[ToolOutcome]:
        return [entry for entry in self._entries if entry.sequence > sequence]

    def entries(self) -> List[ToolOutcome]:
        return list(self._entries)


class EvaluatorArtifactReadLimitHook(HookProvider):
    """Stop a task evaluator that continues reading after its allowance is exhausted."""

    def __init__(self) -> None:
        self.blocked_attempts = 0
        self.exhausted = False
        self.page_limited_paths: set[str] = set()

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        if str(event.tool_use.get("name", "")) != "read_artifact":
            return
        result_text = _result_text(event.result)
        if ARTIFACT_PAGE_LIMIT_REACHED_MARKER in result_text:
            self._handle_page_limit(event)
            return
        if not any(
            marker in result_text
            for marker in (
                ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER,
                ARTIFACT_READ_POLICY_VIOLATION_MARKER,
            )
        ):
            return

        self.blocked_attempts += 1
        if self.blocked_attempts == 1:
            message = (
                "ARTIFACT_READ_LIMIT_REACHED: The requested artifact page is unavailable. Do not call "
                "read_artifact again. Review the controller-provided evidence and return the requested JSON decision."
            )
        else:
            self.exhausted = True
            message = (
                "ARTIFACT_READ_LIMIT_EXHAUSTED: You called read_artifact after being told it was unavailable. "
                "Evaluation is stopping; do not make further tool calls."
            )
            request_state = event.invocation_state.setdefault("request_state", {})
            if isinstance(request_state, dict):
                request_state["stop_event_loop"] = True
                request_state[EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY] = {
                    "reason": "max_reads_exceeded",
                    "blocked_attempts": self.blocked_attempts,
                }
            logger.warning("Stopping task evaluator after repeated artifact-read limit violation")

        if isinstance(event.result, dict):
            event.result["content"] = [{"text": message}]

    def _handle_page_limit(self, event: AfterToolCallEvent) -> None:
        """Guide a first per-artifact cap to another artifact; stop repeated disregard."""

        tool_input = event.tool_use.get("input", {})
        path = str(tool_input.get("path", "")) if isinstance(tool_input, dict) else ""
        if path not in self.page_limited_paths:
            self.page_limited_paths.add(path)
            message = (
                "ARTIFACT_PAGE_LIMIT_REACHED: This artifact has reached its page allowance. "
                "You may read a different controller-authorized artifact, but do not read this artifact again."
            )
            if isinstance(event.result, dict):
                event.result["content"] = [{"text": message}]
            return

        self.exhausted = True
        message = (
            "ARTIFACT_READ_LIMIT_EXHAUSTED: You reread an artifact after being told its page allowance was "
            "exhausted. Evaluation is stopping; do not make further tool calls."
        )
        request_state = event.invocation_state.setdefault("request_state", {})
        if isinstance(request_state, dict):
            request_state["stop_event_loop"] = True
            request_state[EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY] = {
                "reason": "repeated_page_limit",
                "blocked_attempts": 1,
            }
        logger.warning("Stopping task evaluator after a repeated artifact page-limit violation")
        if isinstance(event.result, dict):
            event.result["content"] = [{"text": message}]


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
        self.finding_submission_repair_active = False
        self._finding_repair_requires_artifact_read = False
        self._finding_repair_artifact_read_complete = False

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
        if self.finding_submission_repair_active:
            if tool_name == "record_task_acceptance":
                self._block(
                    event,
                    tool_id,
                    "FINDING_REPAIR_REQUIRED: store_finding previously failed. Do not call "
                    "record_task_acceptance until a changed store_finding call succeeds and returns finding:<id>.",
                )
                return
            if tool_name not in {"read_artifact", "store_finding"}:
                self._block(
                    event,
                    tool_id,
                    "FINDING_REPAIR_REQUIRED: only read_artifact and a changed store_finding call are allowed "
                    "until finding persistence succeeds.",
                )
                return
            if (
                tool_name == "store_finding"
                and self._finding_repair_requires_artifact_read
                and not self._finding_repair_artifact_read_complete
            ):
                self._block(
                    event,
                    tool_id,
                    "FINDING_REPAIR_READ_REQUIRED: the submitted evidence marker was not found. Read the cited "
                    "artifact, then submit one changed store_finding payload using a verbatim positive marker.",
                )
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
            correction_limit = 1 if self.failure_category == "task_scope_violation" else self.max_corrections
            if self._correction_attempts >= correction_limit:
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
        raw_output = _result_text(event.result)
        output = raw_output
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
        finding_submission_failure = tool_name == "store_finding" and any(
            token in raw_output.lower() for token in ("evidence assertion", "marker", "validation failed", "field required")
        )
        correctable = tool_name != "record_task_acceptance" and not success and (
            startup_failure or finding_submission_failure or is_correctable_tool_failure(tool_name, tool_input, output)
        )
        if not success:
            output = format_tool_repair_error(tool_name, raw_output)
            if output != raw_output and isinstance(event.result, dict):
                event.result["content"] = [{"text": output}]
                logger.info(
                    "Tool repair diagnostic retained outside agent context: tool=%s raw_error=%s",
                    tool_name,
                    _bounded_text(raw_output, 2000),
                )
        self.journal.append(
            tool_use_id=tool_id,
            tool_name=tool_name,
            success=success,
            correctable=correctable,
            tool_input=tool_input,
            output=output,
            raw_output=raw_output,
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
                if tool_name == "store_finding":
                    self.finding_submission_repair_active = False
                    self._finding_repair_requires_artifact_read = False
                    self._finding_repair_artifact_read_complete = False
            elif self.failure_category == "artifact_unavailable":
                self._artifact_retry_used = True
            elif self._correction_attempts >= (
                1 if self.failure_category == "task_scope_violation" else self.max_corrections
            ):
                self.exhausted = True
                self._stop_event_loop(event, "correction_failed")
            return
        if role in {"diagnostic", "read_only"} and success and tool_name == "read_artifact":
            self._finding_repair_artifact_read_complete = True
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
            if tool_name == "store_finding":
                self.finding_submission_repair_active = True
                self._finding_repair_requires_artifact_read = "marker" in raw_output.lower()
                self._finding_repair_artifact_read_complete = False

    @staticmethod
    def _failure_category(tool_name: str, tool_input: Any, output: str) -> str:
        if tool_name == "shell" and "service target is outside the assigned task boundary" in output.lower():
            return "task_scope_violation"
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
        if self.finding_submission_repair_active:
            guidance = (
                "A finding submission failed. Do not call record_task_acceptance or unrelated tools. "
                "Use only read_artifact and one changed store_finding call until it returns finding:<id>. "
                f"Repair instruction: {self.failed_output}"
            )
        elif self.failure_category == "artifact_unavailable":
            guidance = (
                "An artifact could not be read. Make at most one changed read_artifact call using its canonical "
                "artifact: reference or a relative path (artifacts/ is searched first, then the operation root). "
                "If it still fails, do not read that artifact again; use an existing durable "
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
