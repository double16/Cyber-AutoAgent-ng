import json

import pytest
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent

from src.modules.handlers.tool_recovery import (
    ARTIFACT_PAGE_LIMIT_REACHED_MARKER,
    ARTIFACT_READ_OVERLAP_GUARD_MARKER,
    ARTIFACT_READ_REPEAT_GUARD_MARKER,
    ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER,
    EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY,
    STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY,
    TOOL_RECOVERY_EXHAUSTED_STATE_KEY,
    EvaluatorArtifactReadLimitHook,
    TaskFailureRecoveryHook,
    ToolOutcomeJournal,
    _input_fingerprint,
    _result_success,
    _result_text,
    _shell_executable,
    format_tool_repair_error,
    is_correctable_tool_failure,
    outcomes_to_toon,
)


def _before(tool_id, name, tool_input):
    return BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": tool_id, "name": name, "input": tool_input},
        invocation_state={},
    )


def _after(tool_id, name, tool_input, *, status, text):
    return AfterToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": tool_id, "name": name, "input": tool_input},
        invocation_state={},
        result={"status": status, "toolUseId": tool_id, "content": [{"text": text}]},
    )


def test_prerequisite_failure_allows_independent_work_until_successful_correction():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal, max_policy_violations=10)
    failed_input = {
        "command": (
            "feroxbuster -u http://target/ "
            "-w /usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt"
        )
    }

    hook._after_tool(
        _after(
            "failed",
            "shell",
            failed_input,
            status="error",
            text="Could not open /usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt",
        )
    )

    create_tasks = _before("tasks", "create_tasks", {"tasks": [{"title": "Fake /admin"}]})
    hook._before_tool(create_tasks)
    assert create_tasks.cancel_tool is False
    acceptance = _before("acceptance", "record_task_acceptance", {"results": []})
    hook._before_tool(acceptance)
    assert acceptance.cancel_tool is False

    diagnostic = _before("diagnostic", "shell", {"command": "find /usr/share/wordlists -type f"})
    hook._before_tool(diagnostic)
    assert diagnostic.cancel_tool is False
    hook._after_tool(
        _after(
            "diagnostic",
            "shell",
            diagnostic.tool_use["input"],
            status="success",
            text="/usr/share/wordlists/dirb/common.txt",
        )
    )
    assert hook.unresolved is True

    correction_input = {
        "command": "feroxbuster -u http://target/ -w /usr/share/wordlists/dirb/common.txt"
    }
    correction = _before("correction", "shell", correction_input)
    hook._before_tool(correction)
    assert correction.cancel_tool is False
    hook._after_tool(
        _after("correction", "shell", correction_input, status="success", text="200 /login.php")
    )
    assert hook.unresolved is False

    valid_tasks = _before("valid-tasks", "create_tasks", {"tasks": [{"title": "Verify /login.php"}]})
    hook._before_tool(valid_tasks)
    assert valid_tasks.cancel_tool is False
    assert [outcome.recovery_role for outcome in journal.entries()] == ["normal", "diagnostic", "correction"]


def test_outcome_journal_retains_externalized_artifact_references():
    journal = ToolOutcomeJournal()

    outcome = journal.append(
        tool_use_id="recon",
        tool_name="specialized_recon_orchestrator",
        success=True,
        correctable=False,
        tool_input={},
        output=(
            "[Tool output: 100 chars | Artifact ref: "
            "artifact:artifacts/specialized_recon_orchestrator_result.log]"
        ),
    )

    assert outcome.artifact_refs == ("artifact:artifacts/specialized_recon_orchestrator_result.log",)


def test_outcome_journal_normalizes_comma_terminated_artifact_references():
    outcome = ToolOutcomeJournal().append(
        tool_use_id="evidence",
        tool_name="store_observation",
        success=True,
        correctable=False,
        tool_input={},
        output="Stored artifact:task_evidence/abc/root_status.txt,",
    )

    assert outcome.artifact_refs == ("artifact:task_evidence/abc/root_status.txt",)


def test_outcome_journal_records_static_shell_request_collection():
    outcome = ToolOutcomeJournal().append(
        tool_use_id="routes",
        tool_name="shell",
        success=True,
        correctable=False,
        tool_input={
            "command": 'for path in /api /login; do curl -sS "http://target.test${path}"; done'
        },
        output="/api -> 200\n/login -> 200",
    )

    assert outcome.execution_receipts[0].subjects == (
        "http://target.test/api",
        "http://target.test/login",
    )
    assert outcome.execution_receipts[0].request_count == 2
    assert outcome.execution_receipts[0].collection is True


def test_outcome_journal_records_python_runtime_receipts_only_from_marker():
    journal = ToolOutcomeJournal()
    output = (
        "done\n__CYBER_EXECUTION_RECEIPT__"
        '{"collection": true, "request_count": 2, '
        '"subjects": ["http://target.test/api", "http://target.test/login"]}'
    )

    outcome = journal.append(
        tool_use_id="python-routes",
        tool_name="python_repl",
        success=True,
        correctable=False,
        tool_input={"code": "..."},
        output=output,
    )

    assert outcome.execution_receipts[0].source == "python_runtime"
    assert outcome.execution_receipts[0].request_count == 2


def test_outcome_journal_extracts_structured_mcp_artifact_id_from_result_only():
    journal = ToolOutcomeJournal()

    outcome = journal.append(
        tool_use_id="mcp-inventory",
        tool_name="mcp_inventory_producer",
        success=True,
        correctable=False,
        tool_input={"artifact_id": "input-must-not-be-trusted.json"},
        output={"artifact_id": "mcp-inventory.json"},
    )

    assert outcome.artifact_refs == ("artifact_id:mcp-inventory.json",)


def test_evaluator_artifact_read_limit_hook_guides_once_then_stops():
    hook = EvaluatorArtifactReadLimitHook()
    first = _after(
        "first",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER}: Artifact read limit reached (8)",
    )

    hook._after_tool(first)

    assert hook.blocked_attempts == 1
    assert hook.exhausted is False
    assert "evaluator-wide artifact-read budget is exhausted" in first.result["content"][0]["text"]
    assert first.invocation_state == {}

    second = _after(
        "second",
        "read_artifact",
        {"path": "artifact:artifacts/second.txt"},
        status="error",
        text=f"{ARTIFACT_TOTAL_READ_LIMIT_REACHED_MARKER}: Artifact read limit reached (8)",
    )
    hook._after_tool(second)

    assert hook.blocked_attempts == 2
    assert hook.exhausted is True
    assert "Evaluation is stopping" in second.result["content"][0]["text"]
    assert second.invocation_state["request_state"] == {
        "stop_event_loop": True,
        EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY: {
            "reason": "max_reads_exceeded",
            "blocked_attempts": 2,
        },
    }


def test_evaluator_artifact_read_limit_hook_preserves_repeat_guard_reason():
    hook = EvaluatorArtifactReadLimitHook()
    event = _after(
        "repeat",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_READ_REPEAT_GUARD_MARKER}: Repeated artifact read guidance for duplicate_page",
    )

    hook._after_tool(event)

    assert "ARTIFACT_READ_REPEAT_GUARD" in event.result["content"][0]["text"]
    assert "page is unavailable" not in event.result["content"][0]["text"]


def test_evaluator_artifact_read_limit_hook_guides_overlap_then_stops_repeat():
    hook = EvaluatorArtifactReadLimitHook()
    first = _after(
        "first",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: page overlaps returned content",
    )
    hook._after_tool(first)

    assert hook.exhausted is False
    assert ARTIFACT_READ_OVERLAP_GUARD_MARKER in first.result["content"][0]["text"]

    other = _after(
        "other",
        "read_artifact",
        {"path": "artifact:artifacts/second.txt"},
        status="error",
        text=f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: page overlaps returned content",
    )
    hook._after_tool(other)

    assert hook.exhausted is False

    second = _after(
        "second",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_READ_OVERLAP_GUARD_MARKER}: artifact is blocked",
    )
    hook._after_tool(second)

    assert hook.exhausted is True
    assert second.invocation_state["request_state"]["stop_event_loop"] is True


def test_evaluator_artifact_page_limit_allows_another_artifact_then_stops_repeat():
    hook = EvaluatorArtifactReadLimitHook()
    first = _after(
        "first",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_PAGE_LIMIT_REACHED_MARKER}: Artifact page limit reached (4)",
    )

    hook._after_tool(first)

    assert hook.blocked_attempts == 0
    assert hook.exhausted is False
    assert "different controller-authorized artifact" in first.result["content"][0]["text"]

    other = _after(
        "other",
        "read_artifact",
        {"path": "artifact:artifacts/second.txt"},
        status="error",
        text=f"{ARTIFACT_PAGE_LIMIT_REACHED_MARKER}: Artifact page limit reached (4)",
    )
    hook._after_tool(other)

    assert hook.exhausted is False

    repeated = _after(
        "repeated",
        "read_artifact",
        {"path": "artifact:artifacts/first.txt"},
        status="error",
        text=f"{ARTIFACT_PAGE_LIMIT_REACHED_MARKER}: Artifact page limit reached (4)",
    )
    hook._after_tool(repeated)

    assert hook.exhausted is True
    assert repeated.invocation_state["request_state"] == {
        "stop_event_loop": True,
        EVALUATOR_ARTIFACT_READ_LIMIT_EXHAUSTED_STATE_KEY: {
            "reason": "repeated_page_limit",
            "blocked_attempts": 1,
        },
    }


def test_failed_corrections_exhaust_configured_allowance_without_blocking_independent_work():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster --not-an-option http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="unknown option --not-an-option")
    )
    correction = _before("correction", "shell", {"command": "feroxbuster -u http://target"})
    hook._before_tool(correction)
    failed_correction = _after(
        "correction",
        "shell",
        correction.tool_use["input"],
        status="error",
        text="timed out",
    )
    hook._after_tool(failed_correction)

    assert hook.unresolved is True
    assert hook.exhausted is False
    second_input = {"command": "feroxbuster -u http://target --timeout 30"}
    second = _before("second", "shell", second_input)
    hook._before_tool(second)
    second_after = _after("second", "shell", second_input, status="error", text="timed out")
    hook._after_tool(second_after)
    assert hook.exhausted is True
    assert second_after.invocation_state["request_state"]["stop_event_loop"] is True
    assert (
        second_after.invocation_state["request_state"][TOOL_RECOVERY_EXHAUSTED_STATE_KEY]["reason"]
        == "correction_failed"
    )
    observation = _before("observation", "store_observation", {"content": "invented success"})
    hook._before_tool(observation)
    assert observation.cancel_tool is False


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_recovery_stops_at_configured_policy_violation_limit(limit):
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=limit)
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="Could not open /missing.txt")
    )

    last_event = None
    for index in range(limit):
        last_event = _before(f"blocked-{index}", "shell", failed_input)
        hook._before_tool(last_event)
        assert last_event.cancel_tool
        if index < limit - 1:
            assert last_event.invocation_state.get("request_state", {}) == {}

    assert last_event is not None
    recovery_state = last_event.invocation_state["request_state"]
    assert recovery_state["stop_event_loop"] is True
    assert recovery_state[TOOL_RECOVERY_EXHAUSTED_STATE_KEY] == {
        "reason": "policy_violation_limit",
        "policy_violations": limit,
        "max_policy_violations": limit,
        "failed_tool": "shell",
    }
    assert hook.exhausted is True


def test_failed_store_finding_requires_changed_submission_before_other_mutations():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal, max_policy_violations=10)
    failed_input = {
        "claim": "Vulnerability pages are accessible without authentication",
        "target": "http://target/vulnerabilities/sqli/",
    }
    hook._after_tool(
        _after(
            "failed",
            "store_finding",
            failed_input,
            status="error",
            text="validation error: title field required",
        )
    )

    unrelated_write = _before("observation", "store_observation", {"content": "invented success"})
    hook._before_tool(unrelated_write)
    assert "FINDING_REPAIR_REQUIRED" in unrelated_write.cancel_tool

    identical_retry = _before("identical", "store_finding", dict(failed_input))
    hook._before_tool(identical_retry)
    assert identical_retry.cancel_tool
    assert "identical failed invocation" in identical_retry.cancel_tool

    corrected_input = {
        "title": "Vulnerability pages accessible without authentication",
        "claim": "Vulnerability pages are accessible without authentication",
        "target": "http://target/vulnerabilities/sqli/",
    }
    correction = _before("correction", "store_finding", corrected_input)
    hook._before_tool(correction)
    assert correction.cancel_tool is False
    hook._after_tool(
        _after(
            "correction",
            "store_finding",
            corrected_input,
            status="success",
            text="Finding candidate stored.",
        )
    )

    assert hook.unresolved is False
    next_write = _before("next", "store_observation", {"content": "stored after correction"})
    hook._before_tool(next_write)
    assert next_write.cancel_tool is False
    assert [outcome.recovery_role for outcome in journal.entries()] == ["normal", "correction"]


def test_read_artifact_failure_allows_one_changed_retry_then_requires_alternate_evidence():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"path": "artifact:artifacts/missing.txt"}
    hook._after_tool(_after("failed", "read_artifact", failed_input, status="error", text="Artifact does not exist"))

    assert hook.failure_category == "artifact_unavailable"
    assert "at most one changed read_artifact call" in hook.recovery_guidance()
    retry_input = {"path": "artifact:artifacts/renamed.txt"}
    retry = _before("retry", "read_artifact", retry_input)
    hook._before_tool(retry)
    assert retry.cancel_tool is False
    hook._after_tool(_after("retry", "read_artifact", retry_input, status="error", text="Artifact does not exist"))

    blocked = _before("blocked", "read_artifact", {"path": "artifact:artifacts/other.txt"})
    hook._before_tool(blocked)
    assert "do not call read_artifact again" in blocked.cancel_tool
    alternate = _before("alternate", "http_request", {"url": "http://target/evidence"})
    hook._before_tool(alternate)
    assert alternate.cancel_tool is False
    hook._after_tool(_after("alternate", "http_request", alternate.tool_use["input"], status="success", text="200 OK"))
    assert hook.unresolved is False


def test_redirect_body_failure_blocks_repeated_browser_body_call_and_allows_http_fallback():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"url": "http://target/redirect"}
    hook._after_tool(
        _after(
            "failed",
            "browser_get_page_html",
            failed_input,
            status="error",
            text="Response.body: Response body is unavailable for redirect responses",
        )
    )

    assert hook.failure_category == "redirect_response_unavailable"
    repeated = _before("repeated", "browser_get_page_html", {"url": "http://target/final"})
    hook._before_tool(repeated)
    assert "Use the resolved URL or an HTTP request" in repeated.cancel_tool
    fallback = _before("fallback", "http_request", {"url": "http://target/redirect"})
    hook._before_tool(fallback)
    assert fallback.cancel_tool is False
    hook._after_tool(_after("fallback", "http_request", fallback.tool_use["input"], status="success", text="302 Location: /final"))
    assert hook.unresolved is False


def test_http_failure_requires_changed_request_or_alternate_method():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"url": "http://target/invalid"}
    hook._after_tool(_after("failed", "http_request", failed_input, status="error", text="connection refused"))

    assert hook.failure_category == "repeated_http_failure"
    assert "Do not repeat the identical request" in hook.recovery_guidance()
    identical = _before("identical", "http_request", failed_input)
    hook._before_tool(identical)
    assert "identical failed invocation" in identical.cancel_tool


def test_shell_failure_requires_changed_command_or_capability_alternate():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"command": "curl --bad-option http://target"}
    hook._after_tool(_after("failed", "shell", failed_input, status="error", text="invalid option --bad-option"))

    assert hook.failure_category == "repeated_shell_failure"
    assert "Do not repeat the identical command" in hook.recovery_guidance()
    identical = _before("identical", "shell", failed_input)
    hook._before_tool(identical)
    assert "identical failed invocation" in identical.cancel_tool


def test_store_finding_missing_artifact_is_a_structured_correctable_failure():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=2)
    failed_input = {"title": "Candidate", "artifacts": []}

    hook._after_tool(
        _after(
            "failed",
            "store_finding",
            failed_input,
            status="error",
            text="At least one existing artifact is required",
        )
    )

    assert hook.unresolved is True
    identical = _before("identical", "store_finding", failed_input)
    hook._before_tool(identical)
    assert "change its input or method" in identical.cancel_tool


def test_failed_finding_submission_blocks_acceptance_until_changed_store_succeeds():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal, max_policy_violations=10)
    failed_input = {"title": "Candidate", "artifacts": ["artifacts/evidence.txt"]}
    failed = _after(
        "failed",
        "store_finding",
        failed_input,
        status="error",
        text="At least one evidence assertion is required",
    )
    hook._after_tool(failed)

    assert hook.finding_submission_repair_active is True
    assert "STORE_FINDING_REPAIR_MISSING_EVIDENCE_ASSERTIONS" in _result_text(failed.result)
    assert journal.entries()[-1].raw_output_summary == "At least one evidence assertion is required"

    acceptance = _before("acceptance", "record_task_acceptance", {"disposition": "finding_candidate"})
    hook._before_tool(acceptance)
    assert "FINDING_REPAIR_REQUIRED" in acceptance.cancel_tool

    artifact_read = _before("read", "read_artifact", {"path": "artifacts/evidence.txt"})
    hook._before_tool(artifact_read)
    assert artifact_read.cancel_tool is False
    hook._after_tool(_after("read", "read_artifact", artifact_read.tool_use["input"], status="success", text="proof"))

    corrected = {
        **failed_input,
        "evidence_assertions": [{"artifact": "artifacts/evidence.txt", "marker": "proof"}],
    }
    retry = _before("retry", "store_finding", corrected)
    hook._before_tool(retry)
    assert retry.cancel_tool is False
    hook._after_tool(_after("retry", "store_finding", corrected, status="success", text="finding:123"))
    assert hook.finding_submission_repair_active is False


def test_missing_marker_requires_artifact_read_before_changed_finding_retry():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"title": "Candidate", "artifacts": ["artifacts/evidence.txt"], "evidence_assertions": []}
    hook._after_tool(
        _after(
            "failed",
            "store_finding",
            failed_input,
            status="error",
            text="evidence assertion marker was not found in artifact:artifacts/evidence.txt",
        )
    )

    retry = _before("retry", "store_finding", {**failed_input, "claim": "changed"})
    hook._before_tool(retry)
    assert "FINDING_REPAIR_READ_REQUIRED" in retry.cancel_tool


def test_schema_error_is_replaced_with_compact_store_finding_repair_contract():
    raw = "Validation failed for input parameters: 9 validation errors for Store_findingTool title Field required"
    formatted = format_tool_repair_error("store_finding", raw)

    assert formatted.startswith("STORE_FINDING_REPAIR_SCHEMA")
    assert "content/metadata" in formatted
    assert "9 validation errors" not in formatted


def test_validation_error_status_is_not_treated_as_a_successful_tool_result():
    assert _result_success({"status": "validation_error"}) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Acceptance disposition finding_candidate requires a finding created by this task",
            "RECORD_TASK_ACCEPTANCE_REPAIR_FINDING_PREREQUISITE",
        ),
        ("acceptance result evidence_refs required", "RECORD_TASK_ACCEPTANCE_REPAIR_EVIDENCE_REFS"),
        (
            "TASK_EVIDENCE_SNAPSHOT_VERIFICATION_FAILED: copied snapshot digest mismatch",
            "RECORD_TASK_ACCEPTANCE_SNAPSHOT_VERIFICATION_FAILED",
        ),
        (
            "TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable",
            "RECORD_TASK_ACCEPTANCE_SOURCE_ARTIFACT_REPAIR",
        ),
        (
            "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: copied snapshot digest mismatch",
            "RECORD_TASK_ACCEPTANCE_SNAPSHOT_DESTINATION_FAILED",
        ),
    ],
)
def test_acceptance_errors_return_specific_repair_instructions(raw, expected):
    assert expected in format_tool_repair_error("record_task_acceptance", raw)


def test_non_shell_failed_correction_exhausts_recovery():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"tasks": "not-a-list"}
    hook._after_tool(
        _after("failed", "create_tasks", failed_input, status="error", text="validation error: input should be a list")
    )

    correction = _before("correction", "create_tasks", {"tasks": [{"title": "Only title changed"}]})
    hook._before_tool(correction)
    assert correction.cancel_tool is False
    hook._after_tool(
        _after(
            "correction",
            "create_tasks",
            correction.tool_use["input"],
            status="error",
            text="validation error: objective field required",
        )
    )

    assert hook.unresolved is True
    assert hook.exhausted is False
    retry_input = {"tasks": [{"title": "Task", "objective": "Run it"}]}
    retry = _before("retry", "create_tasks", retry_input)
    hook._before_tool(retry)
    assert retry.cancel_tool is False
    retry_failure = _after("retry", "create_tasks", retry_input, status="error", text="validation error")
    hook._after_tool(retry_failure)
    assert hook.exhausted is True
    assert STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY not in retry_failure.invocation_state["request_state"]


def test_exhausted_store_finding_correction_requests_stop_for_current_invocation():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10, max_corrections=1)
    failed_input = {"title": "Candidate", "artifacts": ["artifacts/evidence.txt"]}
    hook._after_tool(
        _after(
            "failed",
            "store_finding",
            failed_input,
            status="error",
            text="At least one evidence assertion is required",
        )
    )

    correction = _before(
        "correction",
        "store_finding",
        {**failed_input, "evidence_assertions": [{"artifact": "artifacts/evidence.txt", "marker": "proof"}]},
    )
    hook._before_tool(correction)
    correction_failure = _after(
        "correction",
        "store_finding",
        correction.tool_use["input"],
        status="error",
        text="evidence assertion marker was not found in artifact:artifacts/evidence.txt",
    )
    hook._after_tool(correction_failure)

    request_state = correction_failure.invocation_state["request_state"]
    assert hook.exhausted is True
    assert request_state["stop_event_loop"] is True
    assert request_state[STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY] == {
        "reason": "correction_failed",
        "policy_violations": 0,
        "max_policy_violations": 10,
        "failed_tool": "store_finding",
    }
    assert request_state[TOOL_RECOVERY_EXHAUSTED_STATE_KEY] == request_state[
        STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY
    ]


def test_blocked_store_finding_recovery_requests_stop_for_current_invocation():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=1)
    failed_input = {"title": "Candidate", "artifacts": []}
    hook._after_tool(
        _after(
            "failed",
            "store_finding",
            failed_input,
            status="error",
            text="At least one existing artifact is required",
        )
    )

    blocked = _before("blocked", "store_finding", failed_input)
    hook._before_tool(blocked)

    request_state = blocked.invocation_state["request_state"]
    assert hook.exhausted is True
    assert request_state["stop_event_loop"] is True
    assert request_state[STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY]["reason"] == "policy_violation_limit"
    assert request_state[STORE_FINDING_RECOVERY_EXHAUSTED_STATE_KEY]["failed_tool"] == "store_finding"


def test_finding_validation_corrections_survive_shell_inspection_and_stop_on_final_failure():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal, max_policy_violations=10, max_corrections=2)
    validation_error = (
        "secret exposure revalidation requires the same exposure in a fresh artifact; "
        "API_KEY=exact-validator-secret"
    )
    first_input = {"outcome": "confirmed", "evidence_artifacts": ["artifact:artifacts/first.json"]}

    first_failure = _after(
        "validation-1",
        "record_finding_validation",
        first_input,
        status="error",
        text=validation_error,
    )
    hook._after_tool(first_failure)
    assert "FINDING_VALIDATION_REPAIR" in _result_text(first_failure.result)
    assert "same exposure in a fresh artifact" in _result_text(first_failure.result)
    assert "exact-validator-secret" in _result_text(first_failure.result)

    shell_one = _before("shell-1", "shell", {"command": "curl -sS http://target/first"})
    hook._before_tool(shell_one)
    hook._after_tool(_after("shell-1", "shell", shell_one.tool_use["input"], status="success", text="first.json"))
    assert hook.unresolved is True

    second_input = {"outcome": "confirmed", "evidence_artifacts": ["artifact:artifacts/second.json"]}
    second = _before("validation-2", "record_finding_validation", second_input)
    hook._before_tool(second)
    assert second.cancel_tool is False
    hook._after_tool(_after("validation-2", "record_finding_validation", second_input, status="error", text=validation_error))

    shell_two = _before("shell-2", "shell", {"command": "curl -sS http://target/second"})
    hook._before_tool(shell_two)
    hook._after_tool(_after("shell-2", "shell", shell_two.tool_use["input"], status="success", text="second.json"))
    assert hook.unresolved is True

    final_input = {"outcome": "confirmed", "evidence_artifacts": ["artifact:artifacts/final.json"]}
    final = _before("validation-3", "record_finding_validation", final_input)
    hook._before_tool(final)
    assert final.cancel_tool is False
    final_failure = _after("validation-3", "record_finding_validation", final_input, status="error", text=validation_error)
    hook._after_tool(final_failure)

    assert hook.exhausted is True
    assert final_failure.invocation_state["request_state"]["stop_event_loop"] is True
    assert final_failure.invocation_state["request_state"][TOOL_RECOVERY_EXHAUSTED_STATE_KEY]["reason"] == "correction_failed"
    assert [outcome.recovery_role for outcome in journal.entries()] == [
        "normal",
        "alternative",
        "correction",
        "alternative",
        "correction",
    ]


def test_shell_correction_allows_different_executable_and_requires_changed_input():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=10)
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="Could not open /missing.txt")
    )

    identical = _before("identical", "shell", dict(failed_input))
    hook._before_tool(identical)
    assert identical.cancel_tool
    assert "identical failed invocation" in identical.cancel_tool

    different_executable = _before("curl", "shell", {"command": "curl http://target"})
    hook._before_tool(different_executable)
    assert different_executable.cancel_tool is False
    hook._after_tool(
        _after(
            "curl",
            "shell",
            different_executable.tool_use["input"],
            status="success",
            text="HTTP/1.1 200 OK",
        )
    )
    assert hook.unresolved is False

    hook._after_tool(
        _after("failed-again", "shell", failed_input, status="error", text="Could not open /missing.txt")
    )
    corrected = _before("corrected", "shell", {"command": "feroxbuster -u http://target -w /valid.txt"})
    hook._before_tool(corrected)
    assert corrected.cancel_tool is False


def test_shell_validation_failure_without_executable_accepts_valid_changed_correction():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal, max_policy_violations=10)
    failed_input = {}
    hook._after_tool(
        _after(
            "failed",
            "shell",
            failed_input,
            status="error",
            text="Validation failed for input parameters: 1 validation error: command field required",
        )
    )

    identical = _before("identical", "shell", {})
    hook._before_tool(identical)
    assert "identical failed invocation" in identical.cancel_tool

    invalid = _before("invalid", "shell", {"command": '"unterminated'})
    hook._before_tool(invalid)
    assert invalid.cancel_tool is False

    diagnostic = _before("diagnostic", "shell", {"command": "which curl"})
    hook._before_tool(diagnostic)
    assert diagnostic.cancel_tool is False

    correction_input = {"command": "curl -sS http://target/login.php"}
    correction = _before("correction", "shell", correction_input)
    hook._before_tool(correction)
    assert correction.cancel_tool is False
    hook._after_tool(_after("correction", "shell", correction_input, status="success", text="login"))

    assert hook.unresolved is False
    assert [outcome.recovery_role for outcome in journal.entries()] == ["normal", "correction"]


def test_outcome_journal_is_bounded_and_retains_sensitive_internal_input():
    journal = ToolOutcomeJournal(max_entries=2)
    for index in range(3):
        journal.append(
            tool_use_id=str(index),
            tool_name="http_request",
            success=True,
            correctable=False,
            tool_input={"authorization": "Bearer secret", "url": f"http://target/{index}"},
            output="x" * 600,
        )

    entries = journal.entries()
    assert [entry.sequence for entry in entries] == [2, 3]
    assert "Bearer secret" in entries[-1].input_summary
    assert "[REDACTED]" not in entries[-1].input_summary
    assert len(entries[-1].output_summary) == 500


def test_outcome_journal_retains_record_task_acceptance_input_as_json():
    journal = ToolOutcomeJournal()
    payload = {
        "status": "satisfied",
        "disposition": "finding_candidate",
        "summary": "Payload reflected without encoding",
        "evidence_refs": ["artifact:artifacts/xss.html"],
    }

    outcome = journal.append(
        tool_use_id="acceptance",
        tool_name="record_task_acceptance",
        success=False,
        correctable=False,
        tool_input=payload,
        output="finding created by this task is required",
    )

    assert json.loads(outcome.input_summary) == payload
    assert outcome.structured_input == payload


def test_outcome_journal_retains_full_acceptance_payload_for_controller_replay():
    journal = ToolOutcomeJournal()
    payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "x" * 900,
        "evidence_refs": ["artifact:artifacts/result.txt"],
    }

    outcome = journal.append(
        tool_use_id="acceptance",
        tool_name="record_task_acceptance",
        success=False,
        correctable=False,
        tool_input=payload,
        output="execution prerequisite missing",
    )

    assert len(outcome.input_summary) == 500
    assert outcome.structured_input == payload


def test_correctable_classifier_does_not_retry_ordinary_negative_result():
    tool_input = {"command": "feroxbuster -u http://target"}
    assert is_correctable_tool_failure("shell", tool_input, "Could not open /missing/wordlist.txt") is True
    assert is_correctable_tool_failure("shell", tool_input, "error: unrecognized argument --bad") is True
    assert is_correctable_tool_failure("shell", tool_input, "HTTP 404: route was not found") is False
    assert is_correctable_tool_failure("shell", tool_input, "scan completed; zero results") is False


def test_shell_scope_rejection_is_correctable_with_controller_target_guidance():
    output = (
        "The shell command was not executed because its service target is outside the assigned task boundary. "
        "Allowed targets: http://192.0.2.10:3001."
    )

    assert is_correctable_tool_failure("shell", {"command": "curl https://target-1/api"}, output) is True
    assert "http://192.0.2.10:3001" in format_tool_repair_error("shell", output)


def test_removed_execution_receipt_tool_has_no_special_recovery_behavior():
    output = "Error: Artifact does not exist: artifact:none"

    assert is_correctable_tool_failure("record_execution_evidence", {}, output) is False
    assert format_tool_repair_error("record_execution_evidence", output) == output


def test_startup_dependency_failure_quarantines_only_failed_executable():
    quarantined = []
    shared_quarantine = set()

    def quarantine(executable):
        quarantined.append(executable)
        return ["ffuf"]

    hook = TaskFailureRecoveryHook(
        ToolOutcomeJournal(),
        quarantine_callback=quarantine,
        quarantined_executables=shared_quarantine,
    )
    failed_input = {"command": "dirsearch -u http://target"}
    hook._after_tool(
        _after(
            "failed",
            "shell",
            failed_input,
            status="error",
            text="Traceback (most recent call last):\nModuleNotFoundError: No module named 'dependency'",
        )
    )

    assert quarantined == ["dirsearch"]
    assert hook.quarantined_executables == {"dirsearch"}
    assert hook.alternative_executables == ["ffuf"]
    assert hook.unresolved is False

    retry = _before("retry", "shell", failed_input)
    hook._before_tool(retry)
    assert "quarantined" in retry.cancel_tool

    alternative = _before("alternative", "shell", {"command": "ffuf -u http://target/FUZZ -w words.txt"})
    hook._before_tool(alternative)
    assert alternative.cancel_tool is False

    later_hook = TaskFailureRecoveryHook(
        ToolOutcomeJournal(),
        quarantined_executables=shared_quarantine,
    )
    later_retry = _before("later-retry", "shell", failed_input)
    later_hook._before_tool(later_retry)
    assert "quarantined" in later_retry.cancel_tool


def test_missing_prerequisite_allows_creation_before_retry():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "scanner -w /missing/words.txt http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="No such file or directory: words.txt")
    )

    create_input = {"code": "open('/tmp/words.txt', 'w').write('admin')"}
    create = _before("create", "python_repl", create_input)
    hook._before_tool(create)
    assert create.cancel_tool is False
    hook._after_tool(_after("create", "python_repl", create_input, status="success", text="created"))

    corrected_input = {"command": "scanner -w /tmp/words.txt http://target"}
    corrected = _before("corrected", "shell", corrected_input)
    hook._before_tool(corrected)
    assert corrected.cancel_tool is False


@pytest.mark.parametrize("executable", ["command", "find", "ls", "stat", "test", "type", "which"])
def test_failed_diagnostic_shell_command_does_not_start_recovery(executable):
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
    tool_input = {"command": f"{executable} /missing"}

    hook._after_tool(
        _after(
            "diagnostic",
            "shell",
            tool_input,
            status="error",
            text="No such file or directory",
        )
    )

    outcome = journal.entries()[0]
    assert outcome.success is False
    assert outcome.correctable is False
    assert outcome.recovery_role == "normal"
    assert hook.unresolved is False
    assert hook.exhausted is False
    assert hook.failed_tool_name == ""

    following_call = _before("following", "shell", {"command": "curl -I http://target"})
    hook._before_tool(following_call)
    assert following_call.cancel_tool is False


@pytest.mark.parametrize(
    "command",
    [
        "CHECK_PATH=/missing ls /missing",
        "env CHECK_PATH=/missing ls /missing",
        "sudo ls /missing",
        "sudo env CHECK_PATH=/missing ls /missing",
    ],
)
def test_wrapped_diagnostic_shell_command_does_not_start_recovery(command):
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    tool_input = {"command": command}

    assert is_correctable_tool_failure("shell", tool_input, "No such file or directory") is False
    hook._after_tool(
        _after("diagnostic", "shell", tool_input, status="error", text="No such file or directory")
    )

    assert hook.unresolved is False


def test_failed_diagnostic_during_recovery_preserves_original_failure():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="Could not open /missing.txt")
    )
    original_fingerprint = hook.failed_input_fingerprint

    diagnostic_input = {"command": "ls /also-missing"}
    diagnostic = _before("diagnostic", "shell", diagnostic_input)
    hook._before_tool(diagnostic)
    assert diagnostic.cancel_tool is False
    hook._after_tool(
        _after("diagnostic", "shell", diagnostic_input, status="error", text="No such file or directory")
    )

    assert hook.unresolved is True
    assert hook.exhausted is False
    assert hook.failed_tool_name == "shell"
    assert hook.failed_executable == "feroxbuster"
    assert hook.failed_input_fingerprint == original_fingerprint
    assert [outcome.correctable for outcome in journal.entries()] == [True, False]
    assert [outcome.recovery_role for outcome in journal.entries()] == ["normal", "diagnostic"]

    correction_input = {"command": "feroxbuster -u http://target -w /valid.txt"}
    correction = _before("correction", "shell", correction_input)
    hook._before_tool(correction)
    assert correction.cancel_tool is False
    hook._after_tool(
        _after("correction", "shell", correction_input, status="success", text="200 /login.php")
    )
    assert hook.unresolved is False


def test_curl_header_status_is_interpretable_even_when_shell_status_is_error():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
    tool_input = {"command": "curl --fail -sS -D - -o /dev/null http://target/robots.txt"}

    hook._after_tool(
        _after(
            "curl-404",
            "shell",
            tool_input,
            status="error",
            text="HTTP/1.1 404 Not Found\ncontent-length: 0",
        )
    )

    outcome = journal.entries()[0]
    assert outcome.success is True
    assert outcome.correctable is False
    assert "Interpretable curl response status captured: 404" in outcome.output_summary
    assert hook.unresolved is False


def test_curl_write_out_status_is_interpretable_even_when_shell_status_is_error():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
    tool_input = {
        "command": 'curl --fail -sS -o /dev/null -w "%{http_code} %{url_effective}\\n" http://target/robots.txt'
    }

    hook._after_tool(_after("curl-404", "shell", tool_input, status="error", text="404 http://target/robots.txt"))

    outcome = journal.entries()[0]
    assert outcome.success is True
    assert outcome.correctable is False
    assert "Interpretable curl response status captured: 404" in outcome.output_summary
    assert hook.unresolved is False


def test_silent_curl_without_captured_status_is_not_reclassified_as_evidence():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
    tool_input = {"command": "curl -s http://target/robots.txt"}

    hook._after_tool(_after("curl-silent", "shell", tool_input, status="error", text=""))

    outcome = journal.entries()[0]
    assert outcome.success is False
    assert outcome.correctable is False
    assert outcome.output_summary == ""
    assert hook.unresolved is False


def test_outcome_helpers_cover_schema_variants_and_journal_slices():
    assert _result_text(RuntimeError("boom")) == "boom"
    assert _result_text("plain") == "plain"
    assert _result_text({"content": ["one", {"other": "two"}]}) == "one\n{'other': 'two'}"
    assert _result_success("plain") is True
    assert _result_success({}, RuntimeError("boom")) is False
    assert _shell_executable({"command": []}) == ""
    assert _shell_executable({"command": [{"command": "env feroxbuster --help"}]}) == "feroxbuster"
    assert _shell_executable({"command": '"unterminated'}) == ""
    assert _input_fingerprint({"b": 2, "a": 1}) == _input_fingerprint({"a": 1, "b": 2})
    assert _input_fingerprint({"token": "old"}) == _input_fingerprint({"token": "new"})
    assert _input_fingerprint({"value": "old"}) != _input_fingerprint({"value": "new"})

    journal = ToolOutcomeJournal()
    assert len(journal) == 0
    snapshot = journal.snapshot()
    outcome = journal.append(
        tool_use_id="one",
        tool_name="shell",
        success=True,
        correctable=False,
        tool_input=[{"token": "secret"}],
        output="ok",
    )
    assert journal.since(snapshot) == [outcome]
    assert "success" in outcomes_to_toon(journal.entries())


def test_recovery_allows_multiple_diagnostics_and_two_corrections():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster --bad http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="invalid argument --bad")
    )

    first_diagnostic = _before("diagnostic-1", "shell", {"command": "which feroxbuster"})
    hook._before_tool(first_diagnostic)
    second_diagnostic = _before("diagnostic-2", "shell", {"command": "ls /usr/share/wordlists"})
    hook._before_tool(second_diagnostic)
    assert second_diagnostic.cancel_tool is False

    correction_input = {"command": "feroxbuster -u http://target"}
    first_correction = _before("correction-1", "shell", correction_input)
    hook._before_tool(first_correction)
    second_correction = _before("correction-2", "shell", correction_input)
    hook._before_tool(second_correction)
    assert second_correction.cancel_tool is False
    assert "Failed tool: shell" in hook.recovery_guidance()


def test_recovery_guidance_includes_optional_failed_command_help():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster --bad http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="invalid argument --bad")
    )

    guidance = hook.recovery_guidance(
        "command: feroxbuster\nUsage: feroxbuster\n  -w, --wordlist <FILE>"
    )

    assert "Failed tool: shell" in guidance
    assert "## Failed Command Help" in guidance
    assert "-w, --wordlist <FILE>" in guidance


def test_recovery_allows_read_only_diagnostics_and_unrelated_shell():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="no such file or directory")
    )

    read_artifact = _before("read", "read_artifact", {"path": "artifacts/error.txt"})
    hook._before_tool(read_artifact)
    assert read_artifact.cancel_tool is False
    hook._after_tool(_after("read", "read_artifact", read_artifact.tool_use["input"], status="success", text="log"))

    retrieve = _before("retrieve", "memory_retrieve", {"query": "wordlists"})
    hook._before_tool(retrieve)
    assert retrieve.cancel_tool is False

    unrelated = _before("unrelated", "shell", {"command": "curl http://target/"})
    hook._before_tool(unrelated)
    assert unrelated.cancel_tool is False

    diagnostic = _before("diagnostic", "shell", {"command": "which feroxbuster"})
    hook._before_tool(diagnostic)
    assert diagnostic.cancel_tool is False
