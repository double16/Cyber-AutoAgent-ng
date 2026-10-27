"""CLI diagnostics preserve output contracts and redact conversations without changing inputs."""

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.hooks import AfterModelCallEvent, AfterToolCallEvent, BeforeModelCallEvent, BeforeToolCallEvent

from modules.techniques import cli, roles
from modules.techniques.diagnostics import ConversationDiagnostics, DiagnosticSession
from modules.techniques.events import TechniqueEvents
from modules.utils.redaction import register_runtime_secret


def records(session):
    return [json.loads(line) for line in session.path.read_text().splitlines()]


def test_private_unique_logs_complete_conversations_and_correlation(tmp_path):
    stderr = io.StringIO()
    first = DiagnosticSession(stderr=stderr)
    second = DiagnosticSession(stderr=stderr)
    first.open(str(tmp_path))
    second.open(str(tmp_path))
    with TechniqueEvents().scope(component_id="network", activity_id="activity"):
        first.emit("model_input", level="debug", messages=[{"role": "user", "content": "x" * 60_000}])
    first.close()
    second.close()
    assert first.path != second.path
    assert first.path.parent == tmp_path / "techniques" / "logs"
    assert first.path.stat().st_mode & 0o777 == 0o600
    assert first.path.parent.stat().st_mode & 0o777 == 0o700
    entry = records(first)[-1]
    assert len(entry["messages"][0]["content"]) == 60_000
    assert entry["component_id"] == "network" and entry["activity_id"] == "activity"
    assert entry["run_id"] == first.run_id and entry["timestamp"].endswith("+00:00")
    assert "x" * 100 not in stderr.getvalue()


@pytest.mark.parametrize("verbose,quiet,debug_visible,info_visible", [
    (False, False, False, True), (True, False, True, True), (False, True, False, False),
])
def test_verbosity_only_changes_stderr(tmp_path, verbose, quiet, debug_visible, info_visible):
    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr, verbose=verbose, quiet=quiet)
    session.open(str(tmp_path))
    session.emit("detail", level="debug", message="detailed timing", role_invocation_id="call-1")
    session.lifecycle({"type": "technique_build_progress", "stage": "research", "status": "started"})
    session.lifecycle({"type": "technique_warning", "diagnostic": "trust failed"})
    session.lifecycle({"type": "technique_build_progress", "status": "unresolved", "reason": "coverage gap"})
    session.emit("model_output", level="debug", response="PRIVATE GENERATED DOCUMENT")
    session.close()
    output = stderr.getvalue()
    assert ("detailed timing" in output) == debug_visible
    assert ("research started" in output) == info_visible
    assert ("call-1" in output) == verbose
    assert "trust failed" in output and "coverage gap" in output
    assert "PRIVATE GENERATED DOCUMENT" not in output
    assert records(session)[-1]["response"] == "PRIVATE GENERATED DOCUMENT"


def test_redaction_covers_nested_values_prints_exceptions_and_python_warnings(tmp_path):
    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr, verbose=True)
    session.private_values = ("internal.example",)
    session.open(str(tmp_path))
    secret = register_runtime_secret("unique-raw-diagnostic-secret-4827")
    original = {"messages": [{"password": "password-value", "text": f"{secret} internal.example",
                              "data": {"bytes": b"BINARY-SECRET"}, "count": 3}],
                "tools": [{"schema": {"authorization": "Bearer header-value"}}], "image": {"bytes": b"OTHER"}}
    session.emit("model_input", level="debug", **original)
    session.emit("misc", level="debug", value=Path("internal.example"), payload=b"standalone bytes", enabled=True)
    session.output.write("password=")
    session.output.write("split-secret\n\n")
    session.output.write("trailing diagnostic")
    session.output.flush()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    with session.capture_warnings():
        try:
            raise ValueError(f"token=trace-secret {secret}")
        except ValueError as error:
            session.failure("failed", error, level="error")
            logging.getLogger("test.technique").warning("cookie diagnostic password=warning-secret", exc_info=True)
    assert root.handlers == original_handlers
    session.close()
    raw = session.path.read_text() + stderr.getvalue()
    for value in (secret, "internal.example", "password-value", "header-value", "BINARY-SECRET", "split-secret",
                  "trace-secret", "warning-secret"):
        assert value not in raw
    assert "Traceback" in raw and "trailing diagnostic" in raw
    assert original["messages"][0]["password"] == "password-value"
    assert original["messages"][0]["data"]["bytes"] == b"BINARY-SECRET"
    assert "[binary omitted]" in raw


def test_file_creation_failure_override_and_no_overwrite(tmp_path, monkeypatch):
    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr)
    path = tmp_path / "explicit.jsonl"
    path.write_text("existing\n")
    with pytest.raises(FileExistsError):
        session.open(str(tmp_path), str(path))
    assert path.read_text() == "existing\n"
    monkeypatch.setattr(Path, "mkdir", Mock(side_effect=PermissionError("access denied")))
    session.open(str(tmp_path))
    assert session.stream is None
    assert "Automatic log unavailable" in stderr.getvalue()
    with pytest.raises(PermissionError):
        session.open(str(tmp_path), str(tmp_path / "new.jsonl"))


@pytest.mark.parametrize("close_fails", [False, True])
def test_runtime_sink_failure_warns_once_and_progress_survives(close_fails):
    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr)
    stream = Mock()
    stream.write.side_effect = OSError("disk full")
    if close_fails:
        stream.close.side_effect = OSError("close failed")
    session.stream = stream
    session.emit("one", message="first progress")
    session.emit("two", message="second progress")
    session.close()
    assert session.stream is None
    assert stderr.getvalue().count("log write failed") == 1
    assert "second progress" in stderr.getvalue()


def test_close_failure_does_not_interrupt_cli_cleanup():
    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr)
    session.stream = Mock(close=Mock(side_effect=OSError("close failed")))
    session.close()
    session.close()
    assert "log close failed" in stderr.getvalue()


def test_jsonl_writes_are_thread_safe(tmp_path):
    session = DiagnosticSession(quiet=True, stderr=io.StringIO())
    session.open(str(tmp_path))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: session.emit("parallel", level="debug", number=number), range(100)))
    session.close()
    assert len(records(session)) == 101


def test_conversation_hooks_record_fitted_inputs_attempts_tools_and_errors(tmp_path):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    hooks = ConversationDiagnostics(session, {"role": "technique_researcher", "role_invocation_id": "invocation"})
    registry = Mock()
    hooks.register_hooks(registry)
    assert {call.args[0] for call in registry.add_callback.call_args_list} == {
        BeforeModelCallEvent, AfterModelCallEvent, BeforeToolCallEvent, AfterToolCallEvent,
    }
    messages = [{"role": "user", "content": [{"text": "fitted prompt password=input-secret"}]}]
    agent = SimpleNamespace(system_prompt="instructions", messages=messages, _prompt_token_limit=1000,
                            tool_registry=SimpleNamespace(registry={
                                "read_source": SimpleNamespace(tool_spec={"name": "read_source", "schema": {}}),
                            }))
    before = SimpleNamespace(agent=agent, projected_input_tokens=120)
    hooks.before_model(before)
    hooks.after_model(SimpleNamespace(exception=ValueError("token=model-secret"), stop_response=None))
    hooks.before_model(before)
    hooks.after_model(SimpleNamespace(exception=None, stop_response=SimpleNamespace(
        message={"role": "assistant", "content": [{"text": "Fetch document"}]}, stop_reason="tool_use")))
    tool_use = {"toolUseId": "tool-1", "name": "read_source", "input": {"url": "https://docs.example"}}
    hooks.before_tool(SimpleNamespace(tool_use=tool_use))
    after = SimpleNamespace(tool_use=tool_use, duration=0.5, result={"content": [{"text": "source body"}]},
                            exception=None)
    hooks.after_tool(after)
    after.exception = ValueError("password=tool-secret")
    hooks.after_tool(after)
    hooks.after_model(SimpleNamespace(exception=None, stop_response=None))
    session.close()
    rows = records(session)
    assert [row["model_attempt"] for row in rows if row["type"] == "model_input"] == [1, 2]
    assert rows[1]["messages"][0]["content"][0]["text"].startswith("fitted prompt")
    assert rows[1]["input_tokens"] == 120 and rows[1]["prompt_limit"] == 1000
    assert any(row.get("tool_call_id") == "tool-1" and row["type"] == "tool_output" for row in rows)
    assert "source body" in session.path.read_text()
    assert all(secret not in session.path.read_text() for secret in ("input-secret", "model-secret", "tool-secret"))
    assert "input-secret" in messages[0]["content"][0]["text"]


@pytest.mark.parametrize("fail", [False, True])
def test_role_runner_integrates_diagnostics_after_budget_hooks(tmp_path, monkeypatch, fail):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 48_000)
    monkeypatch.setattr(roles, "create_strands_model", lambda *args: SimpleNamespace(stateful=False))
    captured = []

    def create(kwargs, model):
        captured.append(kwargs)

        def invoke(prompt):
            if fail:
                raise RuntimeError("provider unavailable password=provider-secret")
            return SimpleNamespace(message={"content": [{"text": "final response"}]}, metrics=SimpleNamespace(
                accumulated_usage={"inputTokens": 100, "outputTokens": 50, "totalTokens": 150}))

        return Mock(side_effect=invoke)

    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", create)
    runner = roles.TechniqueRoleRunner("ollama", "model", diagnostics=session)
    if fail:
        with pytest.raises(RuntimeError):
            runner("technique_author", "assembled prompt", [])
    else:
        assert runner("technique_author", "assembled prompt", []) == "final response"
    session.close()
    assert isinstance(captured[0]["hooks"][-1], ConversationDiagnostics)
    assert isinstance(captured[0]["hooks"][-3], roles.PromptBudgetHook)
    rows = records(session)
    assert rows[-1]["type"] == ("role_failed" if fail else "role_completed")
    assert "provider-secret" not in session.path.read_text()
    if not fail:
        assert next(row for row in rows if row["type"] == "role_usage")["generation_tokens"] == 50


@pytest.mark.parametrize("mode", [[], ["--verbose"], ["--quiet"]])
def test_cli_automatic_logs_levels_cleanup_and_repeated_invocations(tmp_path, monkeypatch, capsys, mode):
    build = Mock()
    services = []

    def create(*args, diagnostics=None):
        service = SimpleNamespace(store=SimpleNamespace(qdrant=Mock()))
        services.append(service)
        diagnostics.lifecycle({"type": "technique_sync_complete", "added": 2})
        diagnostics.emit("detail", level="debug", message="model timing")
        print("password=printed-secret")
        service.resolve_bundle = lambda *args, **kwargs: SimpleNamespace(
            render=lambda: "# Markdown bundle\n", delivered=[object()], unresolved=[])
        return service

    build.side_effect = create
    monkeypatch.setattr(cli, "build_service", build)
    handlers = list(logging.getLogger().handlers)
    for _ in range(2):
        assert cli.main(["Source review", "--provider", "ollama", "--output-dir", str(tmp_path), *mode]) == 0
    output = capsys.readouterr()
    assert output.out == "# Markdown bundle\n" * 2
    assert "printed-secret" not in output.err
    assert ("Initializing technique service" in output.err) == (mode != ["--quiet"])
    assert ("model timing" in output.err) == (mode == ["--verbose"])
    assert logging.getLogger().handlers == handlers
    logs = list((tmp_path / "techniques" / "logs").glob("*.jsonl"))
    assert len(logs) == 2
    assert all("# Markdown bundle" not in path.read_text() for path in logs)
    assert all("printed-secret" not in path.read_text() for path in logs)
    assert all(service.store.qdrant.close.call_count == 1 for service in services)


def test_cli_explicit_log_failure_and_invalid_options_do_not_initialize_storage(tmp_path, monkeypatch, capsys):
    build = Mock()
    monkeypatch.setattr(cli, "build_service", build)
    path = tmp_path / "existing.jsonl"
    path.write_text("original")
    assert cli.main(["Source review", "--provider", "ollama", "--log-file", str(path)]) == 1
    assert path.read_text() == "original"
    build.assert_not_called()
    with pytest.raises(SystemExit) as error:
        cli.main(["Source review", "--verbose", "--quiet"])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_cli_partial_failure_summary_and_cleanup_failure_are_logged(tmp_path, monkeypatch, capsys):
    service = SimpleNamespace(store=SimpleNamespace(qdrant=Mock()))
    service.store.qdrant.close.side_effect = OSError("cleanup unavailable")
    service.resolve_bundle = lambda *args, **kwargs: SimpleNamespace(
        render=lambda: "# Partial bundle\n", delivered=[object()], unresolved=[object()])
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: service)
    path = tmp_path / "run.jsonl"
    assert cli.main(["Source review", "--provider", "ollama", "--log-file", str(path), "--quiet"]) == 0
    output = capsys.readouterr()
    assert output.out == "# Partial bundle\n"
    assert "partial" in output.err and "cleanup unavailable" in output.err
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["type"] == "cleanup_failed"
    summary = next(row for row in rows if row["type"] == "bundle_completed")
    assert summary["delivered_count"] == summary["unresolved_count"] == 1


def test_existing_console_loggers_cannot_pollute_stdout_and_are_restored(tmp_path, capsys):
    import sys

    logger = logging.getLogger("test.technique.console")
    previous = (list(logger.handlers), logger.propagate, logger.level)
    console = logging.StreamHandler(sys.stdout)
    logger.handlers = [console]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    session = DiagnosticSession()
    session.open(str(tmp_path))
    try:
        with session.capture_warnings():
            logger.warning("password=old-console-secret")
        assert logger.handlers == [console]
    finally:
        logger.handlers, logger.propagate, logger.level = previous
        session.close()
    output = capsys.readouterr()
    assert output.out == ""
    assert "old-console-secret" not in output.err
    assert "old-console-secret" not in session.path.read_text()
    assert "python_warning" in session.path.read_text()


def test_logging_role_context_retains_stage_on_success_and_failure(tmp_path):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    events = TechniqueEvents()
    events.callback = session.lifecycle
    with events.scope(component_id="source", activity_id="activity"):
        with events.stage("source_selection", 1):
            with session.role("technique_judge", "ollama", "test"):
                pass
        with pytest.raises(ValueError):
            with events.stage("research"):
                raise ValueError("password=stage-secret")
    assert TechniqueEvents.current_context() == {}
    session.close()
    role_record = next(row for row in records(session) if row["type"] == "role_completed")
    assert role_record["stage"] == "source_selection" and role_record["attempt"] == 1
    failure = records(session)[-1]
    assert failure["stage"] == "research" and failure["status"] == "failed"
    assert "stage-secret" not in session.path.read_text()


def test_cli_noisy_cleanup_preserves_markdown_and_log(tmp_path, monkeypatch, capsys):
    def cleanup():
        print("password=cleanup-secret")
        logging.getLogger("test.technique.cleanup").warning("cleanup diagnostic")

    service = SimpleNamespace(store=SimpleNamespace(qdrant=SimpleNamespace(close=cleanup)))
    service.resolve_bundle = lambda *args, **kwargs: SimpleNamespace(
        render=lambda: "# Bundle\n", delivered=[object()], unresolved=[])
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: service)
    path = tmp_path / "run.jsonl"
    assert cli.main(["Source review", "--provider", "ollama", "--log-file", str(path)]) == 0
    output = capsys.readouterr()
    assert output.out == "# Bundle\n"
    assert "cleanup-secret" not in output.err and "cleanup diagnostic" in output.err
    assert "cleanup-secret" not in path.read_text() and "cleanup diagnostic" in path.read_text()


def test_role_usage_is_omitted_when_unavailable(tmp_path, monkeypatch):
    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 48_000)
    monkeypatch.setattr(roles, "create_strands_model", lambda *args: SimpleNamespace(stateful=False))
    result = SimpleNamespace(message={"content": [{"text": "response"}]})
    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", lambda *args: Mock(return_value=result))
    assert roles.TechniqueRoleRunner("ollama", "test", diagnostics=session)("technique_author", "prompt", []) == "response"
    session.close()
    usage = next(row for row in records(session) if row["type"] == "role_usage")
    assert not {"input_tokens", "generation_tokens", "total"}.intersection(usage)


def test_real_sdk_agent_records_assembled_input_response_and_usage(tmp_path, monkeypatch):
    from strands import Agent, tool
    from strands.models import Model

    sent = []

    class LocalModel(Model):
        def get_config(self):
            return {"model_id": "local-test"}

        def update_config(self, **kwargs):
            pass

        async def structured_output(self, *args, **kwargs):
            raise NotImplementedError

        async def stream(self, messages, *args, **kwargs):
            sent.append(messages)
            yield {"messageStart": {"role": "assistant"}}
            if len(sent) == 1:
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "fetch-1", "name": "read_source"}},
                                            "contentBlockIndex": 0}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"url":"https://docs.example"}'}},
                                            "contentBlockIndex": 0}}
            else:
                yield {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}}
                yield {"contentBlockDelta": {"delta": {"text": "completed password=output-secret"},
                                            "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use" if len(sent) == 1 else "end_turn"}}
            yield {"metadata": {"usage": {"inputTokens": 20, "outputTokens": 5, "totalTokens": 25},
                                "metrics": {"latencyMs": 1}}}

    @tool
    def read_source(url: str) -> str:
        """Read a test document without accessing the network."""
        return "source body token=source-secret"

    session = DiagnosticSession(stderr=io.StringIO())
    session.open(str(tmp_path))
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 48_000)
    monkeypatch.setattr(roles, "create_strands_model", lambda *args: LocalModel())
    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", lambda kwargs, model: Agent(**kwargs))
    result = roles.TechniqueRoleRunner("ollama", "local-test", diagnostics=session)(
        "technique_researcher", "request password=input-secret", [read_source])
    session.close()
    assert result == "completed password=output-secret"
    rows = records(session)
    request = next(row for row in rows if row["type"] == "model_input")
    response = [row for row in rows if row["type"] == "model_output"][-1]
    assert request["system_prompt"] == roles.SYSTEM
    assert request["prompt_limit"] == 48_000
    assert "[REDACTED]" in request["messages"][0]["content"][0]["text"]
    assert response["response"]["content"][0]["text"] == "completed password=[REDACTED]"
    assert next(row for row in rows if row["type"] == "role_usage")["total"] == 50
    assert request["tools"][0]["name"] == "read_source"
    assert any(row["type"] == "tool_input" and row["tool_call_id"] == "fetch-1" for row in rows)
    assert any(row["type"] == "tool_output" for row in rows)
    assert "source body token=[REDACTED]" in session.path.read_text()
    assert "source-secret" not in session.path.read_text()
    assert "input-secret" in sent[0][0]["content"][0]["text"]


def test_quiet_retains_critical_python_diagnostics(tmp_path):
    stderr = io.StringIO()
    session = DiagnosticSession(quiet=True, stderr=stderr)
    session.open(str(tmp_path))
    with session.capture_warnings():
        logging.getLogger("test.technique.critical").critical("critical provider diagnostic")
    session.close()
    assert "critical provider diagnostic" in stderr.getvalue()
    assert records(session)[-1]["level"] == "critical"


def test_alignment_diagnostic_displays_adjustment_and_preserves_original(tmp_path):
    import io
    import json

    from modules.techniques.diagnostics import DiagnosticSession

    stderr = io.StringIO()
    session = DiagnosticSession(stderr=stderr)
    session.open(str(tmp_path))
    session.emit("technique_build_progress", stage="request_alignment", status="completed",
                 original_request="Validate applicability", adjusted_request="Develop applicability hypotheses",
                 reason="Hypothesis guidance")
    session.close()
    assert "Aligned request: Develop applicability hypotheses" in stderr.getvalue()
    record = json.loads(session.path.read_text().splitlines()[-1])
    assert record["original_request"] == "Validate applicability"
