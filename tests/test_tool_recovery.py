from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent

from src.modules.handlers.tool_recovery import (
    TaskFailureRecoveryHook,
    ToolOutcomeJournal,
    _input_fingerprint,
    _result_success,
    _result_text,
    _shell_executable,
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


def test_correctable_failure_blocks_side_effects_until_successful_correction():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
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
    assert create_tasks.cancel_tool

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
    assert [outcome.recovery_role for outcome in journal.entries()] == [
        "normal",
        "diagnostic",
        "correction",
    ]


def test_failed_correction_exhausts_allowance_and_keeps_writes_blocked():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster --not-an-option http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="unknown option --not-an-option")
    )
    correction = _before("correction", "shell", {"command": "feroxbuster -u http://target"})
    hook._before_tool(correction)
    hook._after_tool(
        _after(
            "correction",
            "shell",
            correction.tool_use["input"],
            status="error",
            text="timed out",
        )
    )

    assert hook.unresolved is True
    assert hook.exhausted is True
    observation = _before("observation", "store_observation", {"content": "invented success"})
    hook._before_tool(observation)
    assert observation.cancel_tool


def test_non_shell_correction_requires_same_tool_and_changed_input():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
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
    assert unrelated_write.cancel_tool

    identical_retry = _before("identical", "store_finding", dict(failed_input))
    hook._before_tool(identical_retry)
    assert identical_retry.cancel_tool
    assert "change the failed input" in identical_retry.cancel_tool

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


def test_non_shell_failed_correction_exhausts_recovery():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
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
    assert hook.exhausted is True
    retry = _before("retry", "create_tasks", {"tasks": [{"title": "Task", "objective": "Run it"}]})
    hook._before_tool(retry)
    assert retry.cancel_tool == "The task's single correction allowance has been exhausted."


def test_shell_correction_requires_same_executable_and_changed_input():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="Could not open /missing.txt")
    )

    identical = _before("identical", "shell", dict(failed_input))
    hook._before_tool(identical)
    assert identical.cancel_tool
    assert "change the failed input" in identical.cancel_tool

    different_executable = _before("curl", "shell", {"command": "curl http://target"})
    hook._before_tool(different_executable)
    assert different_executable.cancel_tool

    corrected = _before("corrected", "shell", {"command": "feroxbuster -u http://target -w /valid.txt"})
    hook._before_tool(corrected)
    assert corrected.cancel_tool is False


def test_shell_validation_failure_without_executable_accepts_valid_changed_correction():
    journal = ToolOutcomeJournal()
    hook = TaskFailureRecoveryHook(journal)
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
    assert "change the failed input" in identical.cancel_tool

    invalid = _before("invalid", "shell", {"command": '"unterminated'})
    hook._before_tool(invalid)
    assert invalid.cancel_tool

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


def test_outcome_journal_is_bounded_and_redacts_sensitive_input():
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
    assert "Bearer secret" not in entries[-1].input_summary
    assert "[REDACTED]" in entries[-1].input_summary
    assert len(entries[-1].output_summary) == 500


def test_correctable_classifier_does_not_retry_ordinary_negative_result():
    assert is_correctable_tool_failure("shell", "Could not open /missing/wordlist.txt") is True
    assert is_correctable_tool_failure("shell", "error: unrecognized argument --bad") is True
    assert is_correctable_tool_failure("shell", "HTTP 404: route was not found") is False
    assert is_correctable_tool_failure("shell", "scan completed; zero results") is False


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


def test_recovery_rejects_extra_diagnostic_and_second_correction():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster --bad http://target"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="invalid argument --bad")
    )

    first_diagnostic = _before("diagnostic-1", "shell", {"command": "which feroxbuster"})
    hook._before_tool(first_diagnostic)
    second_diagnostic = _before("diagnostic-2", "shell", {"command": "ls /usr/share/wordlists"})
    hook._before_tool(second_diagnostic)
    assert second_diagnostic.cancel_tool

    correction_input = {"command": "feroxbuster -u http://target"}
    first_correction = _before("correction-1", "shell", correction_input)
    hook._before_tool(first_correction)
    second_correction = _before("correction-2", "shell", correction_input)
    hook._before_tool(second_correction)
    assert second_correction.cancel_tool
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


def test_recovery_allows_read_only_without_consuming_diagnostic_and_blocks_unrelated_shell():
    hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    failed_input = {"command": "feroxbuster -u http://target -w /missing.txt"}
    hook._after_tool(
        _after("failed", "shell", failed_input, status="error", text="no such file or directory")
    )

    read_artifact = _before("read", "read_artifact", {"path": "artifacts/error.txt"})
    hook._before_tool(read_artifact)
    assert read_artifact.cancel_tool is False
    hook._after_tool(_after("read", "read_artifact", read_artifact.tool_use["input"], status="success", text="log"))

    retrieve = _before("retrieve", "mem0_retrieve", {"query": "wordlists"})
    hook._before_tool(retrieve)
    assert retrieve.cancel_tool is False

    unrelated = _before("unrelated", "shell", {"command": "curl http://target/"})
    hook._before_tool(unrelated)
    assert unrelated.cancel_tool

    diagnostic = _before("diagnostic", "shell", {"command": "which feroxbuster"})
    hook._before_tool(diagnostic)
    assert diagnostic.cancel_tool is False
