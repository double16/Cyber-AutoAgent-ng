"""Worker-owned flow review, publication, and continuation invariants."""

import asyncio
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from strands import tool

from modules.agents import multi_agent_workflow as workflow
from modules.agents.worker_completion import WorkerCompletion
from modules.config.types import BudgetConfig
from modules.handlers.tool_recovery import ToolOutcome, ToolOutcomeJournal
from modules.tools import artifact, authentication, credentials, memory
from modules.tools.memory import SQLiteApplicationStore
from tests.fixtures.outcome_agent import complete_tool_call, create_outcome_agent
from tests.test_authenticated_http_context import _activate_task
from tests.test_multi_agent_workflow import FakeState, _runtime


@pytest.fixture
def worker_case(tmp_path, monkeypatch):
    target = "https://target.test"
    store = SQLiteApplicationStore(str(tmp_path / "worker.db"), target)
    _activate_task(store, target)
    task = store.get_tasks("OP_AUTH")[0]
    runtime = _runtime()
    runtime.config.model_id = None
    runtime.operation_id = "OP_AUTH"
    controller = workflow.MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(store.get_plan("OP_AUTH"), [task]),
        text_runner=Mock(),
    )
    for module in (workflow, authentication, credentials, memory):
        monkeypatch.setattr(module, "_get_database_store", lambda: store)
    for module in (authentication, credentials, memory):
        monkeypatch.setattr(module, "_operation_id", lambda: "OP_AUTH")
    for module in (workflow, artifact, memory):
        monkeypatch.setattr(module, "_operation_output_root", lambda: str(tmp_path))
    journal = ToolOutcomeJournal()
    path = tmp_path / "observed.html"
    path.write_text('<form><input name="email"><input name="password"></form>')
    outcome = journal.append(
        tool_use_id="observe",
        tool_name="browser_get_page_html",
        success=True,
        correctable=False,
        tool_input={},
        output=f"HTML content saved to artifact: {path}",
    )
    return controller, task, store, target, outcome


def approval(reference="artifact:observed.html"):
    return {"approved": True, "feedback": [], "supporting_evidence_refs": [reference]}


@pytest.mark.parametrize("purpose", ["authentication", "registration"])
def test_flow_replay_survives_deleted_origin_evidence(worker_case, tmp_path, purpose):
    controller, task, store, target, observed = worker_case
    origin = tmp_path / "old-operation"
    origin.mkdir()
    evidence = origin / "form.html"
    evidence.write_text("<form></form>")
    descriptor = {
        "target": target, "purpose": purpose,
        "kind": "browser_form" if purpose == "authentication" else "browser_registration",
        "flow_version": (authentication.AUTHENTICATION_FLOW_VERSION if purpose == "authentication"
                         else authentication.REGISTRATION_FLOW_VERSION),
        "login_url": f"{target}/signup", "roles": ["member"],
        "evidence_refs": [str(evidence)], "worker_review": {"artifact_digests": {str(evidence): "old"}},
        "provenance": "observed_worker_flow",
    }
    saved = store.upsert_authentication_flow("OLD_OPERATION", descriptor, status="validated")
    evidence.unlink()
    origin.rmdir()
    task = replace(task, recovery_context={"conditional_phase": {"registration_flow": {
        "url": f"{target}/signup",
    }}})
    attempt = controller._worker_flow_attempt(task, target, purpose, ("member",))
    assert attempt["existing"]["flow_id"] == saved["flow_id"]
    replay = controller._flow_replay_descriptor(attempt["existing"])
    assert replay["login_url"] == descriptor["login_url"]
    assert "evidence_refs" not in replay and "worker_review" not in replay
    assert not attempt["successful"]
    assert controller._worker_flow_references(task, target, purpose, attempt, []) == []
    assert controller._worker_flow_references(task, target, purpose, attempt, [observed]) == [
        "artifact:observed.html",
    ]
    assert controller._worker_flow_references(task, target, purpose, attempt, []) == ["artifact:observed.html"]
    with pytest.raises(ValueError):
        controller._validate_flow_refinement_critique(approval(str(evidence)), attempt["evidence_refs"])


@pytest.mark.parametrize("result", ["observed", {"status": "success"}, {"status": "error"}])
@pytest.mark.parametrize("incomplete", [False, True])
def test_browser_capture_wrapper_preserves_schema_and_multistep_progress(result, incomplete):
    @tool
    async def interaction(action: str = "continue") -> dict | str:
        """Perform the next observed interaction."""
        return result

    capture = AsyncMock(side_effect=ValueError("Login not complete") if incomplete else None)
    bound = workflow.MultiAgentWorkflowController._authentication_browser_capture_tool(interaction, capture)
    assert workflow.get_tool_spec(bound) == workflow.get_tool_spec(interaction)
    assert asyncio.run(bound(action="continue")) == result
    assert capture.await_count == (0 if isinstance(result, dict) and result["status"] == "error" else 1)


def test_browser_capture_wrapper_preserves_interaction_errors():
    @tool
    def interaction() -> str:
        """Fail the requested interaction."""
        raise RuntimeError("Navigation failed")

    capture = AsyncMock()
    bound = workflow.MultiAgentWorkflowController._authentication_browser_capture_tool(interaction, capture)
    with pytest.raises(RuntimeError, match="Navigation failed"):
        asyncio.run(bound())
    capture.assert_not_awaited()


def test_worker_automatically_captures_and_stops_browser_actions(worker_case, monkeypatch):
    controller, task, _store, target, _observed = worker_case
    credential = {"credential_id": "selected", "credential_type": "username_password",
                  "target": target, "role": "member"}
    ready = {"value": False}
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: ready["value"])
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: {})

    async def capture(**_arguments):
        ready["value"] = True
        return json.dumps({"status": "valid", "validation_url": f"{target}/private",
                           "credential_header_names": ["x-session"], "evidence_refs": ["artifact:observed.html"]})

    monkeypatch.setattr(workflow, "capture_browser_authenticated_context", capture)

    @tool
    async def browser_perform_action(action: str) -> str:
        """Perform an observed browser action."""
        return "HTML content saved to artifact: observed.html"

    @contextmanager
    def factory(_role, tools, _system):
        agent = create_outcome_agent(monkeypatch, "authentication_agent", tools)

        def run(_prompt, policy):
            agent._cyber_terminal_tool_hook.configure(policy)
            bound = next(item for item in tools if workflow.get_tool_name(item) == "browser_perform_action")
            result = asyncio.run(bound(action="submit login"))
            event = complete_tool_call(agent, "browser_perform_action", {"action": "submit login"}, result)
            assert event.invocation_state["request_state"]["policy_terminal"]["reason"] == (
                "authentication_credential_worker_done"
            )
            return workflow.TaskExecutorCycleResult(
                "captured", agent._cyber_callback_handler.tool_outcome_journal.entries(),
            )

        yield run

    controller.executor_session_factory = factory
    # Inspect the functional handoff independently of the metadata-only repair session.
    monkeypatch.setattr(controller, "_refine_worker_flow", lambda *_args, **_kwargs: [])
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    result = controller._run_authentication_credential_worker(task, setup, credential, (), [browser_perform_action])
    assert result.browser_session_ready
    attempt = controller._worker_flow_attempt(task, target, "authentication", kinds=(
        "browser_form", "browser_redirect", "browser_mfa",
    ))
    assert attempt["functional_completed"]
    assert attempt["validation_metadata"]["credential_header_names"] == ["x-session"]
    assert controller._worker_completions(task)[0].credential_id == "selected"


@pytest.fixture
def registered_case(worker_case):
    controller, _old_task, store, target, observed = worker_case
    task = controller._credential_provisioning_tasks(1, "credential_provisioning", 1, {
        "target": target, "target_id": "target", "url": f"{target}/signup", "roles": ["member"],
    }, {"member": 1})[0]
    task = replace(task, status="active")
    controller.state.tasks = [task]
    store.store_task("OP_AUTH", task)
    record = store.store_credential("OP_AUTH", {
        "credential_id": "registered", "target": target, "role": "member", "origin": "registered",
        "credential_type": "username_password", "management_policy": "operation", "status": "unknown",
        "operation_id": None, "payload": {"username": "new-user", "password": "internal-secret"},
    })
    stored = ToolOutcome(
        sequence=2, tool_use_id="repair-store", tool_name="store_credential", success=True, correctable=False,
        input_summary="artifact:observed.html", output_summary=json.dumps({"stored": True, "credential": record}),
        artifact_refs=("artifact:observed.html",),
    )
    return controller, task, store, target, observed, stored


@pytest.mark.parametrize("approved", [True, False])
def test_registration_completion_survives_controller_reload_and_mapping_failure(registered_case, approved):
    controller, task, store, target, _observed, stored = registered_case
    payload = controller._registration_acceptance_payload(task, [stored], {
        "mapping_approved": approved, "existing": {"flow_id": "reviewed-flow"} if approved else None,
    })
    assert payload["status"] == "satisfied"
    assert "Registered identity persisted" in payload["summary"]
    assert "not yet approved" in payload["summary"] if not approved else "mapping approved" in payload["summary"]
    retained = controller.state.tasks[0]
    store.store_task("OP_AUTH", retained)
    reloaded = workflow.MultiAgentWorkflowController(
        runtime=controller.runtime, budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(store.get_plan("OP_AUTH"), store.get_tasks("OP_AUTH")),
    )
    assert reloaded._registration_acceptance_payload(task, []) == payload
    assert "internal-secret" not in json.dumps(retained.recovery_context)
    assert "new-user" not in json.dumps(retained.recovery_context)
    prompt = reloaded._worker_completion_section(task)
    assert "registered" in prompt and "registration" in prompt
    completion = reloaded._worker_completions(task)[0]
    assert completion.producer_id == "repair-store"
    assert completion.mapping_approved is approved
    assert completion.target == target


@pytest.mark.parametrize("mutation", [
    {"origin": "found"}, {"target": "https://other.test"}, {"role": "other"},
    {"status": "revoked"}, {"status": "retired"}, {"status": "invalid"},
])
def test_registration_completion_revalidates_persisted_credential(registered_case, monkeypatch, mutation):
    controller, task, store, _target, _observed, stored = registered_case
    assert controller._registration_acceptance_payload(task, [stored])
    original = store.get_credential("registered")
    monkeypatch.setattr(store, "get_credential", lambda _id: {**original, **mutation})
    assert controller._registration_acceptance_payload(task, []) is None


def test_registration_requires_task_local_storage_not_http_or_existing_credential(registered_case):
    controller, task, _store, target, observed, stored = registered_case
    assert controller._registration_acceptance_payload(task, [observed]) is None
    assert controller._registered_worker_completion(task, target, ("member",), []) is None
    for changed in (
        replace(stored, success=False), replace(stored, output_summary='{"stored":false}'),
        replace(stored, output_summary="not JSON"), replace(stored, output_summary='{"stored":true,"credential":null}'),
        replace(stored, output_summary='{"stored":true,"credential":{"credential_id":"missing"}}'),
    ):
        assert controller._registration_acceptance_payload(task, [changed]) is None
    missing_evidence = replace(stored, artifact_refs=(), input_summary="")
    assert controller._registration_acceptance_payload(task, [missing_evidence]) is None
    assert controller._registered_worker_success(task, target, ("member",), [missing_evidence])


def test_registration_completion_uses_full_structured_storage_arguments(registered_case):
    controller, task, _store, _target, _observed, stored = registered_case
    stored = replace(stored, input_summary="truncated input", artifact_refs=(),
                     structured_input={"evidence_refs": ["artifact:observed.html"]})
    payload = controller._registration_acceptance_payload(task, [stored])
    assert payload["evidence_refs"] == ["artifact:observed.html"]


def test_missing_credential_and_malformed_completion_state_do_not_prove_success(registered_case, monkeypatch):
    controller, task, store, _target, _observed, stored = registered_case
    controller.state.patch_task(task.task_uid, recovery_context_updates={"worker_completions": []})
    assert controller._worker_completions(task) == []
    controller.state.patch_task(task.task_uid, recovery_context_updates={"worker_completions": {}})
    assert controller._registration_acceptance_payload(task, [stored])
    monkeypatch.setattr(store, "get_credential", lambda _id: None)
    assert controller._worker_completions(task) == []


def test_registration_gap_is_corrected_before_acceptance_publication(registered_case, monkeypatch):
    controller, task, store, _target, _observed, stored = registered_case
    published = []
    monkeypatch.setattr(memory, "_record_task_acceptance", lambda uid, results: published.extend(results) or "ok")
    bound = memory.build_record_task_acceptance_tool(
        task.task_uid, task,
        acceptance_reconciler=lambda current, payload: controller._registration_acceptance_payload(current, [stored])
        or payload,
    )
    bound(status="satisfied", disposition="observation", summary="No registered identity persisted",
          evidence_refs=["artifact:missing.html"])
    assert len(published) == 1
    assert "Registered identity persisted" in published[0].summary
    assert "No registered identity" not in published[0].summary
    assert published[0].evidence_refs == ("artifact:observed.html",)
    assert store.get_credential("registered")["operation_id"] is None


@pytest.mark.parametrize("changed", [
    {"operation_id": "earlier"}, {"task_uid": "other-task"}, {"purpose": "unknown"},
    {"evidence_refs": "artifact:observed.html"}, {"producer_id": ""}, {"functional_success": False},
    {"mapping_approved": "true"}, {"role": None}, {"extra": "unknown"}, {"target": ""},
    {"evidence_refs": [None]}, {"credential_id": 1},
])
def test_invalid_or_foreign_completion_cannot_close_task(registered_case, changed):
    controller, task, _store, target, _observed, _stored = registered_case
    completion = WorkerCompletion("OP_AUTH", task.task_uid, "registration", target, "member", "registered",
                                  ("artifact:observed.html",), "repair-store")
    controller.state.patch_task(task.task_uid, recovery_context_updates={
        "worker_completions": {"registration:registered": {**completion.to_dict(), **changed}},
    })
    assert controller._worker_completions(task) == []
    assert controller._worker_completion_section(task) == ""


def test_authentication_completion_is_revalidated_without_closing_parent(worker_case, monkeypatch):
    controller, task, _store, target, _observed = worker_case
    completion = WorkerCompletion("OP_AUTH", task.task_uid, "authentication", target, "member", "selected")
    assert WorkerCompletion.from_dict(completion.to_dict()) == completion
    controller._persist_worker_completion(task, completion)
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: True)
    assert controller._worker_completions(task) == [completion]
    assert controller._registration_acceptance_payload(task, []) is None
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    assert controller._worker_completions(task) == []


@pytest.mark.parametrize("value", [None, [], {"incomplete": True}])
def test_worker_completion_rejects_invalid_records(value):
    with pytest.raises(ValueError):
        WorkerCompletion.from_dict(value)


@pytest.mark.parametrize("historical_failure", [True, False])
def test_provisioning_repair_storage_closes_acceptance_without_another_actor(
    registered_case, monkeypatch, historical_failure,
):
    controller, task, _store, target, observed, stored = registered_case
    plan = controller.state.plan
    controller.state.acceptance_complete = False
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: {"prompt": "Register once", "tools": []})
    monkeypatch.setattr(controller, "_prepare_task_authentication", lambda _plan, selected: selected)
    monkeypatch.setattr(controller, "_task_prompt_scope_feedback", lambda *_args: [])
    monkeypatch.setattr(controller, "_task_inventory_route_feedback", lambda *_args: [])
    monkeypatch.setattr(controller, "_resolve_task_executor_base_tools", lambda *_args: [])
    monkeypatch.setattr(controller, "_run_json_text_agent", lambda *_args, **_kwargs: approval())

    def publish(uid, results):
        controller.state.acceptance_results[uid] = results
        return "acceptance published"

    monkeypatch.setattr(memory, "_record_task_acceptance", publish)
    actors = []

    def actor(_role, _prompt, _tools, _system, _policy):
        actors.append(_prompt)
        assert len(actors) <= 2, "Acceptance must not start another actor after successful repair"
        attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
        if len(actors) == 1:
            recorder = controller._worker_flow_recorder(task, target, "registration", "browser_registration", attempt,
                                                       ("member",))
            recorder(login_url=f"{target}/signup", steps=[{"action": "form_submission", "url": f"{target}/signup"}])
            return workflow.TaskExecutorCycleResult(text="Submitted registration", outcomes=[observed],
                                                   recovery_required=historical_failure)
        return workflow.TaskExecutorCycleResult(text="Stored registered identity", outcomes=[stored])

    controller.work_runner = actor
    monkeypatch.setattr(controller, "_evaluate_task", lambda *_args, **_kwargs: workflow.WorkflowDecision(
        status="done", reason="Registered identity persisted",
    ))
    controller._run_task_in_trace(plan, plan.phases[0], task)
    assert len(actors) == 2
    result = controller.state.list_task_acceptance_results(task.task_uid)[0]
    assert "Registered identity persisted" in result.summary
    assert "mapping approved" in result.summary
    assert controller.state.tasks[0].status == "done"
    # A controller reload must close an omitted acceptance result without another browser actor or flow review.
    controller.state.acceptance_results.clear()
    reloaded = workflow.MultiAgentWorkflowController(
        runtime=controller.runtime, budget=BudgetConfig(max_duration_minutes=60), state_store=controller.state,
    )
    for name in ("_build_task_prompt", "_prepare_task_authentication", "_task_prompt_scope_feedback",
                 "_task_inventory_route_feedback", "_resolve_task_executor_base_tools", "_evaluate_task"):
        monkeypatch.setattr(reloaded, name, getattr(controller, name))
    reloaded.work_runner = Mock(side_effect=AssertionError("Completed registration must not be repeated"))
    reloaded._run_task_in_trace(plan, plan.phases[0], task)
    reloaded.work_runner.assert_not_called()
    assert "Registered identity persisted" in reloaded.state.list_task_acceptance_results(task.task_uid)[0].summary


@pytest.mark.parametrize("purpose", ["authentication", "registration"])
@pytest.mark.parametrize("existing", [False, True])
def test_completed_worker_flow_is_published_once_or_updates_existing(worker_case, purpose, existing):
    controller, task, store, target, outcome = worker_case
    roles = ("member",) if purpose == "registration" else ()
    kind = "browser_registration" if purpose == "registration" else "browser_form"
    if purpose == "registration":
        task = replace(task, recovery_context={"conditional_phase": {"registration_flow": {"url": f"{target}/start"}}})
    recorder_attempt = controller._worker_flow_attempt(task, target, purpose, roles)
    recorder = controller._worker_flow_recorder(task, target, purpose, kind, recorder_attempt, roles)
    recorder(login_url=f"{target}/start", steps=[{"url": f"{target}/start", "action": "navigation"}])
    assert not store.list_authentication_flows(target, purpose=purpose)
    if existing:
        stored = store.upsert_authentication_flow("OP_AUTH", recorder_attempt["candidate"], status="validated")
        controller._worker_flow_attempts.clear()
        recorder_attempt = controller._worker_flow_attempt(task, target, purpose, roles)
        recorder = controller._worker_flow_recorder(task, target, purpose, kind, recorder_attempt, roles)
        recorder(login_url=f"{target}/start" if purpose == "registration" else f"{target}/changed")
        assert recorder_attempt["candidate"]["flow_id"] == stored["flow_id"]
    recorder_attempt["successful"] = True
    controller.text_runner.return_value = json.dumps(approval())
    repair = Mock()
    assert controller._refine_worker_flow(task, target, purpose, recorder_attempt, [outcome], repair, [recorder]) == []
    rows = store.list_authentication_flows(target, purpose=purpose)
    assert len(rows) == 1
    assert rows[0]["status"] == "validated"
    descriptor = rows[0]["descriptor"]
    assert descriptor["worker_review"]["task_uid"] == task.task_uid
    assert descriptor["evidence_refs"] == ["artifact:observed.html"]
    assert set(descriptor["worker_review"]["approval_digests"]) == set(descriptor["evidence_refs"])
    assert descriptor["login_url"].endswith("changed" if existing and purpose == "authentication" else "start")
    controller._refine_worker_flow(task, target, purpose, recorder_attempt, [outcome], repair, [recorder])
    repair.assert_not_called()
    controller.text_runner.assert_called_once()


@pytest.mark.parametrize("purpose", ["authentication", "registration"])
@pytest.mark.parametrize("success", [False, True])
def test_worker_critic_corrections_resume_without_repeating_success(worker_case, purpose, success):
    controller, task, store, target, outcome = worker_case
    roles = ("member",) if purpose == "registration" else ()
    attempt = controller._worker_flow_attempt(task, target, purpose, roles)
    recorder = controller._worker_flow_recorder(
        task,
        target,
        purpose,
        "browser_registration" if roles else "browser_form",
        attempt,
        roles,
    )
    recorder(login_url=f"{target}/start")
    attempt["successful"] = success
    controller.text_runner.side_effect = [
        json.dumps({"approved": False, "feedback": ["Record the final step"]}),
        json.dumps(approval()),
    ]

    @tool(name="browser_perform_action")
    def submission():
        return "submitted"

    def repair(prompt, tools):
        names = {workflow.get_tool_name(item) for item in tools}
        assert ("browser_perform_action" in names) is not attempt["successful"]
        assert (
            "same credential or registration identity" in prompt
            if not attempt["successful"] else "do not create another" in prompt
        )
        recorder(login_url=f"{target}/start", steps=[{"url": f"{target}/final", "action": "submit"}])
        attempt["successful"] = True
        return workflow.TaskExecutorCycleResult("repaired", [])

    repair = Mock(side_effect=repair)
    controller._refine_worker_flow(task, target, purpose, attempt, [outcome], repair, [recorder, submission])
    assert repair.call_count == (2 if not success and purpose == "authentication" else 1)
    row = store.list_authentication_flows(target, purpose=purpose)[0]
    assert row["descriptor"]["steps"][0]["action"] == "form_submission"
    assert row["descriptor"]["worker_review"]["cycle"] == 2


@pytest.mark.parametrize("cause", ["rejected", "bad_evidence", "exception", "no_candidate", "no_success"])
def test_failed_flow_review_does_not_publish_or_remove_functional_state(worker_case, cause):
    controller, task, store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    if cause != "no_candidate":
        recorder(login_url=f"{target}/login")
    attempt["successful"] = cause != "no_success"
    controller.text_runner.side_effect = RuntimeError("model unavailable") if cause == "exception" else None
    controller.text_runner.return_value = json.dumps(
        approval("artifact:unobserved.html")
        if cause == "bad_evidence"
        else {"approved": False, "feedback": ["Missing proof"]}
        if cause == "rejected"
        else approval()
    )
    repair = Mock(return_value=workflow.TaskExecutorCycleResult("unchanged", []))
    controller._refine_worker_flow(task, target, "authentication", attempt, [outcome], repair, [recorder])
    assert not store.list_authentication_flows(target)
    assert controller.state.tasks[0].status == "active"
    assert "authentication_flow_gap" in controller.state.tasks[0].recovery_context
    gap = controller.state.list_tasks()[0].recovery_context["authentication_flow_gap"]
    assert gap["stop_reason"] == (
        "candidate_or_evidence_unavailable" if cause == "no_candidate" else
        "functional_recovery_failed" if cause == "no_success" else
        "review_error" if cause in {"exception", "bad_evidence"} else "review_limit_reached"
    )
    assert gap["reviews"] == (0 if cause in {"no_candidate", "no_success"} else 1 if cause in {"exception", "bad_evidence"} else 3)
    assert attempt["successful"] == (cause != "no_success")
    assert repair.call_count <= controller.flow_refinement_cycles - (cause != "no_candidate")


@pytest.mark.parametrize("error", [TimeoutError, ConnectionError])
def test_worker_flow_critic_retries_transient_errors(worker_case, error):
    controller, task, store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    recorder(login_url=f"{target}/login")
    attempt["successful"] = True
    controller.text_runner.side_effect = [error("temporary"), json.dumps(approval())]
    controller._refine_worker_flow(task, target, "authentication", attempt, [outcome], Mock(), [recorder])
    assert controller.text_runner.call_count == 2
    assert store.list_authentication_flows(target)[0]["status"] == "validated"


@pytest.mark.parametrize(
    "credential_type,kind",
    [("username_password", "browser_form"), ("api_key", "api_key"), ("oauth2_client", "oauth2_client")],
)
def test_login_worker_without_flow_records_and_reviews_using_production_hooks(
    worker_case, monkeypatch, credential_type, kind
):
    controller, task, store, target, _outcome = worker_case
    credential = {"credential_id": "selected", "credential_type": credential_type, "target": target, "role": "member"}
    ready = {"value": False}
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: ready["value"])
    monkeypatch.setattr(workflow, "authentication_context_validation_url", lambda *_args: f"{target}/protected")
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)
    controller.text_runner.return_value = json.dumps(approval())
    created = []

    @contextmanager
    def factory(role, tools, _system):
        agent = create_outcome_agent(monkeypatch, role, tools)
        created.append(agent)

        def run(prompt, _policy):
            assert "discover the entry point" in prompt
            journal = agent._cyber_callback_handler.tool_outcome_journal
            snapshot = journal.snapshot()
            complete_tool_call(agent, "browser_get_page_html", {}, "HTML content saved to artifact: observed.html")
            recorder = next(
                item for item in tools if workflow.get_tool_name(item) == f"record_{kind}_authentication_flow"
            )
            result = recorder(
                login_url=f"{target}/login",
                **({"validation_url": f"{target}/protected"} if kind in {"api_key", "oauth2_client"} else {}),
            )
            complete_tool_call(agent, workflow.get_tool_name(recorder), {}, result)
            assert not store.list_authentication_flows(target)
            ready["value"] = True
            return workflow.TaskExecutorCycleResult("authenticated", journal.since(snapshot))

        yield run
        agent.cleanup()

    controller.executor_session_factory = factory
    setup = workflow.AuthenticationSetupRequest(
        task_uid=task.task_uid,
        workstream="authentication",
        targets=(target,),
        allowed_origins=(target,),
        credentials=(credential,),
    )
    result = controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert result.browser_session_ready
    row = store.list_authentication_flows(target)[0]
    assert row["descriptor"]["validation_url"] == f"{target}/protected"
    assert row["descriptor"]["kind"] == kind
    assert len(created) == 1


@pytest.mark.parametrize(
    "steps",
    [
        [{"url": "https://other.test/step", "action": "navigation"}],
        [{"url": "https://target.test/step?token=secret", "action": "navigation"}],
        [{"url": "https://target.test/step", "action": "arbitrary"}],
        [{"url": "https://target.test/step", "action": "submit", "values": {"password": "secret"}}],
        [{"url": "https://target.test/step", "action": "submit", "field_names": [123]}],
    ],
)
def test_recorded_steps_reject_secrets_unknown_actions_and_scope_expansion(worker_case, steps):
    controller, task, store, target, _outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task, target, "registration", "browser_registration", attempt, ("member",)
    )
    with pytest.raises(ValueError):
        recorder(login_url=f"{target}/signup", steps=steps)
    assert attempt["candidate"] is None
    assert not store.list_authentication_flows(target, purpose="registration")


@pytest.mark.parametrize(
    "case", ["registered", "provided", "invalid", "other_target", "other_role", "failed", "malformed"]
)
def test_registration_success_requires_this_workers_durable_credential(worker_case, case):
    controller, task, store, target, outcome = worker_case
    record = store.store_credential(
        "OP_AUTH",
        {
            "credential_type": "username_password",
            "target": "https://other.test" if case == "other_target" else target,
            "role": "other" if case == "other_role" else "member",
            "origin": "provided" if case == "provided" else "registered",
            "payload": {"username": "one", "password": "hidden"},
            "management_policy": "operation",
            "status": "invalid" if case == "invalid" else "unknown",
        },
    )
    storage = replace(
        outcome,
        tool_name="store_credential",
        success=case != "failed",
        raw_output_summary="bad json" if case == "malformed" else json.dumps({"stored": True, "credential": record}),
    )
    assert controller._registered_worker_success(task, target, ("member",), [storage]) == (case == "registered")


def test_old_descriptor_versions_are_not_worker_starting_flows(worker_case):
    controller, task, store, target, _outcome = worker_case
    store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "purpose": "registration",
            "target": target,
            "kind": "browser_registration",
            "flow_version": 2,
            "provenance": "observed_worker_flow",
            "roles": ["member"],
            "login_url": f"{target}/signup",
        },
        status="validated",
    )
    assert controller._worker_flow_attempt(task, target, "registration", ("member",))["existing"] is None


def test_registration_wizard_records_all_steps_and_does_not_replay_creation(worker_case):
    from fastapi.testclient import TestClient

    from tests.fixtures.authentication_app import RegistrationWizardState, registration_wizard_app

    controller, task, store, target, initial = worker_case
    state = RegistrationWizardState()
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task, target, "registration", "browser_registration", attempt, ("member",)
    )
    journal = ToolOutcomeJournal()
    steps = []
    outcomes = [initial]
    with TestClient(registration_wizard_app(state), base_url=target) as client:
        assert client.get("/signup/profile").status_code == 409
        assert client.get("/signup/confirm").status_code == 409
        for route, fields in (
            ("/signup", {"email": "one@example.test"}),
            ("/signup/profile", {"displayName": "One"}),
            ("/signup/confirm", {"password": "hidden-password"}),
        ):
            assert client.get(route).status_code == 200
            response = client.post(route, json=fields)
            assert response.status_code == 200
            steps.append({"url": target + route, "action": "form_submission", "field_names": list(fields)})
            path = Path(workflow.resolve_operation_artifact_path("artifact:observed.html")).with_name(
                f"step-{len(steps)}.html",
            )
            path.write_text(response.text)
            outcomes.append(
                journal.append(
                    tool_use_id=route,
                    tool_name="browser_perform_action",
                    success=True,
                    correctable=False,
                    tool_input={},
                    output=f"Observed step saved to artifact: {path}",
                )
            )
        stored = json.loads(
            credentials.store_credential(
                credential_type="username_password",
                values={"username": "one@example.test", "password": "hidden-password"},
                target=target,
                role="member",
                origin="registered",
                evidence_refs=["artifact:observed.html"],
            )
        )
        outcomes.append(
            journal.append(
                tool_use_id="store",
                tool_name="store_credential",
                success=True,
                correctable=False,
                tool_input={},
                output=json.dumps(stored),
            )
        )
        recorder(
            login_url=f"{target}/signup",
            steps=steps[:2],
            identity_fields=["email"],
            required_fields=["email", "displayName", "password"],
        )
        attempt["success_check"] = lambda observed: controller._registered_worker_success(
            task, target, ("member",), observed
        )
        controller.text_runner.side_effect = [
            json.dumps({"approved": False, "feedback": ["Record confirmation step"]}),
            json.dumps(approval()),
        ]

        def repair(_prompt, tools):
            assert all(workflow.get_tool_name(item) != "browser_perform_action" for item in tools)
            recorder(login_url=f"{target}/signup", steps=steps, success_redirect_url=f"{target}/login")
            return workflow.TaskExecutorCycleResult("completed steps", [])

        controller._refine_worker_flow(task, target, "registration", attempt, outcomes, repair, [recorder])
    assert state.submissions == ["identity", "profile", "confirmation"]
    assert len(state.accounts) == 1
    row = store.list_authentication_flows(target, purpose="registration")[0]
    assert row["descriptor"]["steps"] == steps
    assert "hidden-password" not in json.dumps(row["descriptor"])
    assert "one@example.test" not in json.dumps(row["descriptor"])


def test_definitive_credential_rejection_does_not_retry_or_publish(worker_case):
    controller, task, store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    attempt["stop_check"] = lambda: True
    repair = Mock()
    controller._refine_worker_flow(task, target, "authentication", attempt, [outcome], repair, [])
    repair.assert_not_called()
    controller.text_runner.assert_not_called()
    assert not store.list_authentication_flows(target)


def test_flow_refinement_config_has_no_discovery_fallback(worker_case):
    controller, _task, _store, _target, _outcome = worker_case
    controller.runtime.config_manager.getenv_int = lambda name, default: (
        0 if name == "CYBER_WORKFLOW_FLOW_REFINEMENT_CYCLES" else default
    )
    assert controller._flow_refinement_cycle_count() == 1
    controller.runtime.config_manager = None
    assert controller._flow_refinement_cycle_count() == 3




@pytest.mark.parametrize("credential_type", ["api_key", "oauth2_client"])
@pytest.mark.parametrize("explicit_url", [False, True])
def test_key_worker_validates_updated_mapping_or_observed_endpoint(
    worker_case, monkeypatch, credential_type, explicit_url
):
    controller, task, _store, target, _outcome = worker_case
    credential = {"credential_id": "selected", "credential_type": credential_type, "target": target, "role": "member"}
    original = {"flow_id": "flow", "kind": credential_type, "validation_url": f"{target}/old"}
    call = Mock(return_value='{"ready": true}')
    monkeypatch.setattr(workflow, "establish_credential_authenticated_context", call)
    monkeypatch.setattr(controller, "_refine_worker_flow", Mock(return_value=[]))
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)

    def run(_role, _prompt, tools, _system, _policy):
        by_name = {workflow.get_tool_name(item): item for item in tools}
        by_name[f"record_{credential_type}_authentication_flow"](
            login_url=f"{target}/login",
            validation_url=f"{target}/updated",
        )
        tool = by_name["establish_credential_authenticated_context"]
        tool(**({"validation_url": f"{target}/observed"} if explicit_url else {}))
        return workflow.TaskExecutorCycleResult("ready", [])

    controller.work_runner = run
    setup = workflow.AuthenticationSetupRequest(
        task_uid=task.task_uid,
        workstream="authentication",
        targets=(target,),
        allowed_origins=(target,),
        credentials=(credential,),
        authentication_flows=(original,),
    )
    controller._run_authentication_credential_worker(task, setup, credential, (original,), [])
    call.assert_called_once_with(
        credential_id="selected",
        validation_url=f"{target}/{'observed' if explicit_url else 'updated'}",
    )


def test_worker_step_runtime_schema_advertises_canonical_actions(worker_case):
    controller, task, _store, target, _outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task,
        target,
        "registration",
        "browser_registration",
        attempt,
        ("member",),
    )
    schema = workflow.get_tool_spec(recorder)["inputSchema"]["json"]
    item = schema["properties"]["steps"]["items"]
    assert item["properties"]["action"]["enum"] == ["navigation", "form_submission", "redirect", "verification"]
    assert item["additionalProperties"] is False
    assert "credential_id" not in schema["properties"]
    recorder(login_url=f"{target}/signup", steps=[{"url": f"{target}/signup", "action": "navigate"}])
    assert attempt["candidate"]["steps"][0]["action"] == "navigation"


@pytest.mark.parametrize("kind", ["api_key", "oauth2_client", "browser_form"])
def test_login_mapping_reuse_selects_an_applicable_credential_method(worker_case, kind):
    controller, task, store, target, _outcome = worker_case
    store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "target": target,
            "purpose": "authentication",
            "kind": kind,
            "flow_version": authentication.AUTHENTICATION_FLOW_VERSION,
            "provenance": "observed_worker_flow",
            "login_url": f"{target}/login",
            "validation_url": f"{target}/protected",
        },
        status="validated",
    )
    attempt = controller._worker_flow_attempt(task, target, "authentication", kinds=("browser_form",))
    assert bool(attempt["existing"]) == (kind == "browser_form")


@pytest.mark.parametrize("authorized", [False, True])
def test_redirect_flow_steps_require_controller_assigned_identity_provider_origin(worker_case, authorized):
    controller, task, _store, target, _outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(
        task,
        target,
        "authentication",
        "browser_redirect",
        attempt,
        allowed_origins=(target, "https://identity.test") if authorized else (target,),
    )
    steps = [{"url": "https://identity.test/login", "action": "navigation"}]
    if authorized:
        recorder(login_url=f"{target}/login", steps=steps)
        assert attempt["candidate"]["steps"][0]["url"] == "https://identity.test/login"
    else:
        with pytest.raises(ValueError, match="target boundary"):
            recorder(login_url=f"{target}/login", steps=steps)


@pytest.mark.parametrize("purpose,kind", [("authentication", "browser_form"), ("registration", "browser_registration")])
def test_missing_functional_completion_never_publishes_an_approved_mapping(worker_case, purpose, kind):
    controller, task, store, target, outcome = worker_case
    roles = ("member",) if purpose == "registration" else ()
    attempt = controller._worker_flow_attempt(task, target, purpose, roles)
    recorder = controller._worker_flow_recorder(task, target, purpose, kind, attempt, roles)
    recorder(login_url=f"{target}/start", steps=[{"url": f"{target}/start", "action": "navigation"}])
    attempt["success_check"] = lambda _outcomes: False
    controller.text_runner.return_value = json.dumps(approval())
    controller._refine_worker_flow(
        task,
        target,
        purpose,
        attempt,
        [outcome],
        Mock(return_value=workflow.TaskExecutorCycleResult("pending", [])),
        [recorder],
    )
    assert not store.list_authentication_flows(target, purpose=purpose)
    assert controller.state.tasks[0].status == "active"




def test_flow_step_does_not_expand_path_boundary_through_target_origin(worker_case):
    _controller, _task, _store, target, _outcome = worker_case
    with pytest.raises(ValueError, match="target boundary"):
        authentication._normalize_flow_steps(
            f"{target}/assigned",
            [{"url": f"{target}/unassigned", "action": "navigation"}],
            [target],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action,expected", [("submit", "success"), ("invented", "error")])
async def test_worker_step_action_normalization_through_tool_runtime(worker_case, action, expected):
    controller, task, _store, target, _outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task,
        target,
        "registration",
        "browser_registration",
        attempt,
        ("member",),
    )
    events = [
        event
        async for event in recorder.stream(
            {
                "toolUseId": "record-step",
                "name": "record_authentication_flow",
                "input": {"login_url": f"{target}/signup", "steps": [{"url": f"{target}/signup", "action": action}]},
            },
            {},
        )
    ]
    assert events[-1]["tool_result"]["status"] == expected
    if expected == "success":
        assert attempt["candidate"]["steps"][0]["action"] == "form_submission"
    else:
        assert attempt["candidate"] is None


@pytest.mark.parametrize("metadata_only", [False, True])
def test_worker_repair_policy_retains_bounds_and_changes_terminal_action(worker_case, metadata_only):
    controller, task, _store, target, _outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    attempt["successful"] = metadata_only

    def predicate():
        return None

    original = workflow.AgentRunPolicy(
        max_agent_calls=12,
        max_model_turns=24,
        terminal_check=predicate,
        required_tool_names={"capture"},
    )
    policy = controller._worker_flow_repair_policy(original, attempt, [recorder])
    assert policy.max_agent_calls == 12
    assert policy.max_model_turns == 24
    if metadata_only:
        assert policy.terminal_check is None
        assert policy.required_tool_names == {"record_authentication_flow"}
        assert policy.terminal_reason == "worker_flow_metadata_staged"
    else:
        assert policy is original
        assert policy.terminal_check is predicate


@pytest.mark.parametrize("existing", [False, True])
def test_header_bindings_have_one_effective_contract(worker_case, existing):
    controller, task, _store, target, _outcome = worker_case
    bindings = ({"header_name": "Authorization", "storage_key": "token", "value_template": "Bearer {value}"},)
    flow = {"storage_header_bindings": []} if existing else {}
    setup = workflow.AuthenticationSetupRequest(
        task.task_uid,
        "authentication",
        (target,),
        (target,),
        ({"credential_id": "selected", "credential_type": "username_password", "target": target},),
        authentication_flows=(flow,) if existing else (),
        client_storage_header_bindings=bindings,
    )
    assert "Authorization" in setup.credential_worker_toon()
    assert "Authorization,token,Bearer {value}" in setup.to_toon()
    assert workflow._effective_storage_header_bindings(flow, bindings) == bindings
    assert workflow._effective_storage_header_bindings({"storage_header_bindings": list(bindings)}) == bindings
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    attempt["controller_bindings"] = bindings
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    properties = workflow.get_tool_spec(recorder)["inputSchema"]["json"]["properties"]
    assert "storage_header_bindings" not in properties
    recorder(login_url=f"{target}/login")
    assert attempt["candidate"]["storage_header_bindings"] == list(bindings)
    with pytest.raises(ValueError, match="cannot be overridden"):
        recorder(login_url=f"{target}/login", storage_header_bindings=[])


@pytest.mark.parametrize("state", ["success", "rejected", "recoverable"])
def test_login_repair_preserves_policy_and_returns_to_critic(worker_case, monkeypatch, state):
    controller, task, store, target, _outcome = worker_case
    credential = {
        "credential_id": "selected",
        "credential_type": "username_password",
        "target": target,
        "role": "member",
    }
    ready = {"value": False}
    rejection = {"outcome": "unknown"}
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: ready["value"])
    monkeypatch.setattr(workflow, "authentication_context_validation_url", lambda *_args: f"{target}/protected")
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: rejection)
    controller.text_runner.return_value = json.dumps(approval())
    policies = []

    @contextmanager
    def factory(role, tools, _system):
        agent = create_outcome_agent(monkeypatch, role, tools)

        def run(prompt, policy):
            policies.append(policy)
            agent._cyber_terminal_tool_hook.configure(policy)
            journal = agent._cyber_callback_handler.tool_outcome_journal
            if len(policies) == 1:
                return workflow.TaskExecutorCycleResult("turn limit", [], reason="stalled")
            assert policy is policies[0]
            assert policy.max_agent_calls == 12 and policy.max_model_turns == 24
            assert "authentication only" in prompt
            complete_tool_call(agent, "browser_get_page_html", {}, "HTML content saved to artifact: observed.html")
            recorder = next(
                item for item in tools if workflow.get_tool_name(item) == "record_browser_form_authentication_flow"
            )
            recorder_result = recorder(login_url=f"{target}/login")
            complete_tool_call(agent, workflow.get_tool_name(recorder), {}, recorder_result)
            if state == "success":
                ready["value"] = True
            elif state == "rejected":
                rejection["outcome"] = "credential_rejected"
            event = complete_tool_call(
                agent,
                "capture_browser_authenticated_context",
                {},
                "context result",
                status="success" if ready["value"] else "error",
            )
            terminal = event.invocation_state.get("request_state", {}).get("policy_terminal")
            if state == "recoverable":
                assert terminal is None
            else:
                assert terminal["reason"] == (
                    "authentication_credential_worker_done" if state == "success" else "credential_rejected"
                )
            return workflow.TaskExecutorCycleResult(
                "attempt", journal.entries(), reason=terminal["reason"] if terminal else "stalled"
            )

        yield run

    controller.executor_session_factory = factory
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert len(policies) == 2
    rows = store.list_authentication_flows(target)
    assert bool(rows) is (state == "success")
    assert controller.text_runner.call_count == (1 if state == "success" else 0)


@pytest.mark.parametrize("classification", ["authentication", "registration", "unknown"])
def test_inventory_route_hint_uses_structured_classification(worker_case, monkeypatch, classification):
    controller, task, store, target, _outcome = worker_case
    plan = store.get_plan("OP_AUTH")
    task = replace(task, evidence=["artifact:inventory.json", "artifact:inventory.json"])
    manifest = {
        "items": [
            {
                "kind": "workflow",
                "attributes": {
                    "client_route": {"classification": classification, "url": f"{target}/login?token=ignored"},
                },
            }
        ]
    }
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", lambda *_args: (manifest, "digest"))
    hints = controller._authentication_flow_hints(plan, task)
    assert len(hints) == (1 if classification == "authentication" else 0)
    endpoints = controller._authentication_discovery_endpoints(plan, task, (target,), hints)
    navigation = [entry["url"] for entry in endpoints if entry["role"] == "navigation"]
    assert (f"{target}/login" in navigation) is (classification == "authentication")
    if hints:
        assert hints[0]["kind"] == ""
        assert hints[0]["advisory_only"] is True


@pytest.mark.parametrize(
    "redirect,expected",
    [
        ("https://target.test/login", True),
        ("https://other.test/login", False),
        ("https://user:secret@target.test/login", False),
    ],
)
def test_validated_registration_redirect_is_only_a_scoped_navigation_hint(worker_case, redirect, expected):
    controller, task, store, target, _outcome = worker_case
    store.upsert_authentication_flow(
        "OP_AUTH",
        {
            "target": target,
            "purpose": "registration",
            "kind": "browser_registration",
            "flow_version": authentication.REGISTRATION_FLOW_VERSION,
            "success_redirect_url": redirect,
        },
        status="validated",
    )
    hints = controller._authentication_flow_hints(store.get_plan("OP_AUTH"), task)
    assert bool(hints) is expected
    assert not store.list_authentication_flows(target, purpose="authentication")


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, "target", "role", "origin"])
async def test_provisioning_storage_binding_enforced_by_runtime_schema(worker_case, monkeypatch, override):
    controller, task, store, target, _outcome = worker_case
    task = replace(
        task,
        recovery_context={
            "conditional_phase": {
                "kind": "credential_provisioning",
                "registration_flow": {"target": target, "roles": ["member"]},
                "credential_slot": {"role": "member", "ordinal": 1},
            }
        },
    )
    captured = []

    @tool(name="store_credential")
    def persist(credential_type: str, values: dict, target: str = "", role: str = "", origin: str = "") -> str:
        """Store one registration credential."""
        captured.append((target, role, origin, credential_type, values))
        return "stored"

    monkeypatch.setattr(workflow, "build_role_tools", lambda *_args, **_kwargs: [persist])
    tools = controller._resolve_task_executor_base_tools(store.get_plan("OP_AUTH"), task, {})
    bound = next(item for item in tools if workflow.get_tool_name(item) == "store_credential")
    schema = workflow.get_tool_spec(bound)["inputSchema"]["json"]
    assert not {"target", "role", "origin"} & schema["properties"].keys()
    assert schema["additionalProperties"] is False
    inputs = {"credential_type": "username_password", "values": {"username": "test", "password": "secret"}}
    if override:
        inputs[override] = "unassigned"
    events = [
        event async for event in bound.stream({"toolUseId": "store", "name": "store_credential", "input": inputs}, {})
    ]
    result = events[-1]["tool_result"]
    assert result["status"] == ("error" if override else "success")
    assert bool(captured) is (override is None)
    if captured:
        assert captured[0][:3] == (target, "member", "registered")


@pytest.mark.parametrize("tool_value", [object(), tool(name="store_credential")(lambda values: str(values))])
def test_required_controller_binding_fails_closed(tool_value):
    with pytest.raises(workflow.WorkflowInvariantError, match="Required controller tool binding"):
        workflow._bind_single_choice_tool(tool_value, {"target": "https://target.test"}, required=True)


@pytest.mark.parametrize("required_fields", [[], ["email"]])
def test_registration_requiredness_is_separate_from_observed_controls(worker_case, required_fields):
    controller, task, _store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task, target, "registration", "browser_registration", attempt, ("member",)
    )
    recorder(
        login_url=f"{target}/signup",
        required_fields=required_fields,
        steps=[{"url": f"{target}/signup", "action": "submit", "field_names": ["email", "company"]}],
    )
    assert attempt["candidate"].get("required_fields", []) == required_fields
    prompt = controller._flow_refinement_critic_prompt("registration", target, attempt["candidate"], [outcome], [])
    assert "Filling a field does not establish requiredness" in prompt
    assert "Missing required markers do not prove optionality" in prompt
    assert "Do not repeat registration to classify fields" in prompt


@pytest.mark.parametrize("initial_limit", [False, True])
def test_provisioning_stops_at_storage_then_reviews_and_accepts_without_resubmission(
    worker_case,
    monkeypatch,
    initial_limit,
):
    controller, task, store, target, _outcome = worker_case
    task = replace(
        task,
        objective="Register one member",
        recovery_context={
            "conditional_phase": {
                "kind": "credential_provisioning",
                "registration_flow": {"target": target, "url": f"{target}/signup", "roles": ["member"]},
                "credential_slot": {"role": "member", "ordinal": 1},
            }
        },
    )
    controller.state.tasks[0] = task
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: {"prompt": task.objective, "tools": []})
    monkeypatch.setattr(controller, "_task_prompt_scope_feedback", lambda *_args: [])
    monkeypatch.setattr(controller, "_prepare_task_authentication", lambda *_args: task)
    monkeypatch.setattr(
        controller,
        "_evaluate_task",
        lambda *_args, **_kwargs: workflow.WorkflowDecision(
            status="done" if controller.state.list_task_acceptance_results(task.task_uid) else "continue",
            reason="Acceptance needs recording",
        ),
    )
    monkeypatch.setattr(workflow, "build_role_tools", lambda *_args, **_kwargs: [credentials.store_credential])
    controller.text_runner.return_value = json.dumps(approval())
    calls = []
    identities = []

    @contextmanager
    def factory(role, tools, _system):
        agent = create_outcome_agent(monkeypatch, role, tools)
        journal = agent._cyber_callback_handler.tool_outcome_journal
        names = {workflow.get_tool_name(item): item for item in tools}

        def run(_prompt, policy):
            calls.append(policy)
            assert policy is not None
            agent._cyber_terminal_tool_hook.configure(policy)
            if initial_limit and len(calls) == 1:
                return workflow.TaskExecutorCycleResult("SDK limit", [], reason="stalled")
            if policy.terminal_reason == "registration_worker_done":
                assert policy.max_agent_calls == 8 and policy.max_model_turns == 32
                assert policy.terminal_check() is None
                result = names["store_credential"](
                    credential_type="username_password",
                    values={"username": "registered@test.invalid", "password": "hidden"},
                    evidence_refs=["artifact:observed.html"],
                )
                event = complete_tool_call(agent, "store_credential", {}, result)
                identities.append(json.loads(result)["credential"]["credential_id"])
                assert (
                    event.invocation_state["request_state"]["policy_terminal"]["reason"] == "registration_worker_done"
                )
            elif policy.terminal_reason == "worker_flow_metadata_staged":
                assert policy.max_agent_calls == 8 and policy.max_model_turns == 32
                assert "store_credential" not in names
                result = names["record_authentication_flow"](
                    login_url=f"{target}/signup", success_redirect_url=f"{target}/login"
                )
                complete_tool_call(agent, "browser_get_page_html", {}, "HTML content saved to artifact: observed.html")
                event = complete_tool_call(agent, "record_authentication_flow", {}, result)
                assert (
                    event.invocation_state["request_state"]["policy_terminal"]["reason"]
                    == "worker_flow_metadata_staged"
                )
            else:
                assert "store_credential" not in names
                controller.state.record_task_acceptance(
                    task,
                    {
                        "status": "satisfied",
                        "disposition": "observation",
                        "summary": "Registered identity stored",
                        "evidence_refs": ["artifact:observed.html"],
                    },
                )
                complete_tool_call(agent, "record_task_acceptance", {}, '{"complete": true}')
            return workflow.TaskExecutorCycleResult("completed", journal.entries(), reason=policy.terminal_reason)

        run.live_outcomes = journal.entries
        yield run

    controller.executor_session_factory = factory
    plan = store.get_plan("OP_AUTH")
    controller._run_task_in_trace(plan, plan.phases[0], task)
    assert len(identities) == 1
    assert len(calls) == (3 if initial_limit else 2)
    assert store.list_authentication_flows(target, purpose="registration")[0]["status"] == "validated"
    assert controller.state.tasks[0].status == "done"


@pytest.mark.parametrize("candidate_first", [False, True])
def test_browser_capture_uses_known_headers_with_or_without_staged_flow(worker_case, monkeypatch, candidate_first):
    import asyncio

    controller, task, _store, target, _outcome = worker_case
    credential = {
        "credential_id": "selected",
        "credential_type": "username_password",
        "target": target,
        "role": "member",
    }
    bindings = ({"header_name": "Authorization", "storage_key": "token", "value_template": "{value}"},)
    capture = AsyncMock(return_value='{"authentication_ready":true}')
    monkeypatch.setattr(workflow, "capture_browser_authenticated_context", capture)
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)
    monkeypatch.setattr(controller, "_refine_worker_flow", Mock(return_value=[]))

    def run(_role, prompt, tools, _system, policy):
        assert "Authorization" in prompt
        assert policy.terminal_check() is None
        names = {workflow.get_tool_name(item): item for item in tools}
        if candidate_first:
            names["record_browser_form_authentication_flow"](login_url=f"{target}/login")
        asyncio.run(names["capture_browser_authenticated_context"](validation_url=f"{target}/protected"))
        return workflow.TaskExecutorCycleResult("captured", [])

    controller.work_runner = run
    setup = workflow.AuthenticationSetupRequest(
        task.task_uid,
        "authentication",
        (target,),
        (target,),
        (credential,),
        client_storage_header_bindings=bindings,
    )
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    capture.assert_awaited_once_with(
        credential_id="selected",
        storage_header_bindings=list(bindings),
        request_header_names=[],
        validation_url=f"{target}/protected",
        validation_marker="",
    )


def test_registration_critic_correction_removes_unsupported_requiredness_only(worker_case):
    controller, task, store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "registration", ("member",))
    recorder = controller._worker_flow_recorder(
        task, target, "registration", "browser_registration", attempt, ("member",)
    )
    steps = [{"url": f"{target}/signup", "action": "form_submission", "field_names": ["email", "company"]}]
    recorder(login_url=f"{target}/signup", required_fields=["email", "company"], steps=steps)
    attempt["successful"] = True
    Path(workflow.resolve_operation_artifact_path("artifact:observed.html")).write_text(
        '<form><input name="email" required><input name="company"></form>',
    )
    controller.text_runner.side_effect = [
        json.dumps({"approved": False, "feedback": ["Only email is proven required; company requiredness is unknown"]}),
        json.dumps(approval()),
    ]

    def repair(_prompt, tools):
        assert {workflow.get_tool_name(item) for item in tools} == {"record_authentication_flow"}
        recorder(login_url=f"{target}/signup", required_fields=["email"], steps=steps)
        return workflow.TaskExecutorCycleResult("staged", [], reason="worker_flow_metadata_staged")

    controller._refine_worker_flow(task, target, "registration", attempt, [outcome], repair, [recorder])
    descriptor = store.list_authentication_flows(target, purpose="registration")[0]["descriptor"]
    assert descriptor["required_fields"] == ["email"]
    assert descriptor.get("optional_fields", []) == []
    assert descriptor["steps"] == steps


@pytest.mark.parametrize(
    "roles,slot,target_value",
    [
        ([], "", "https://target.test"),
        (["member", "admin"], "member", "https://target.test"),
        (["member"], "admin", "https://target.test"),
        (["member"], "member", ""),
    ],
)
def test_provisioning_storage_refuses_ambiguous_or_missing_task_binding(
    worker_case, monkeypatch, roles, slot, target_value
):
    controller, task, store, _target, _outcome = worker_case
    task = replace(
        task,
        recovery_context={
            "conditional_phase": {
                "kind": "credential_provisioning",
                "registration_flow": {"target": target_value, "roles": roles},
                "credential_slot": {"role": slot},
            }
        },
    )
    monkeypatch.setattr(workflow, "build_role_tools", lambda *_args, **_kwargs: [credentials.store_credential])
    with pytest.raises(workflow.WorkflowInvariantError, match="task-bound target and credential role"):
        controller._resolve_task_executor_base_tools(store.get_plan("OP_AUTH"), task, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [None, [], {"unexpected": "value"}])
async def test_bound_tool_rejects_invalid_raw_inputs_before_sdk_normalization(inputs):
    called = []

    @tool
    def invoke_subject(subject: str) -> str:
        """Execute the controller subject."""
        called.append(subject)
        return subject

    bound = workflow._bind_single_choice_tool(invoke_subject, {"subject": "assigned"}, required=True)
    events = [
        event
        async for event in bound.stream(
            {"toolUseId": "invoke", "name": "invoke_subject", "input": inputs},
            {},
        )
    ]
    assert events[-1]["tool_result"]["status"] == "error"
    assert not called


def test_authentication_navigation_hints_preserve_path_scope(worker_case, monkeypatch):
    controller, task, store, target, _outcome = worker_case
    scoped = target + "/assigned"
    plan = replace(store.get_plan("OP_AUTH"), targets=[workflow.OperationTarget("target", scoped, "network")])
    task = replace(task, evidence=["artifact:inventory.json"])
    manifest = {
        "items": [
            {
                "kind": "workflow",
                "attributes": {
                    "client_route": {
                        "classification": "authentication",
                        "url": target + "/unassigned/login",
                    }
                },
            }
        ]
    }
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", lambda *_args: (manifest, "digest"))
    assert controller._authentication_flow_hints(plan, task) == ()
    endpoints = controller._authentication_discovery_endpoints(
        plan,
        task,
        (scoped,),
        ({"kind": "", "login_url": target + "/unassigned/login"},),
    )
    assert [entry["url"] for entry in endpoints] == [scoped]


@pytest.fixture
def production_registered_case(registered_case, monkeypatch):
    """Exercise the production wrapper with durable SQLite state and no task-list attribute."""
    from types import SimpleNamespace

    controller, task, store, target, observed, stored = registered_case

    def list_tasks(phase=None, status=None):
        return [item for item in store.get_tasks("OP_AUTH")
                if (phase is None or item.phase == phase) and (not status or item.status in status)]

    client = SimpleNamespace(
        list_tasks=list_tasks,
        patch_task=lambda **kwargs: store.patch_task("OP_AUTH", **kwargs),
        get_active_plan=lambda **_kwargs: store.get_plan("OP_AUTH"),
        list_finding_records=lambda: store.list_findings("OP_AUTH"),
        list_task_acceptance_results=lambda uid: store.list_task_acceptance_results("OP_AUTH", uid),
    )
    monkeypatch.setattr(workflow, "get_memory_client", lambda **_kwargs: client)
    controller.state = workflow.WorkflowStateStore("OP_AUTH")
    return controller, task, store, target, observed, stored


@pytest.mark.parametrize("approved", [True, False])
def test_completion_reload_through_production_state_store(production_registered_case, approved):
    controller, task, store, _target, _observed, stored = production_registered_case
    assert not hasattr(controller.state, "tasks")
    payload = controller._registration_acceptance_payload(task, [stored], {
        "mapping_approved": approved, "existing": {"flow_id": "reviewed"} if approved else None,
    })
    reloaded = workflow.MultiAgentWorkflowController(
        runtime=controller.runtime, budget=BudgetConfig(max_duration_minutes=60),
        state_store=workflow.WorkflowStateStore("OP_AUTH"),
    )
    assert reloaded._registration_acceptance_payload(task, []) == payload
    assert reloaded._worker_completions(task)[0].mapping_approved is approved
    assert store.get_tasks("OP_AUTH")[-1].recovery_context.get("worker_completions")


@pytest.mark.parametrize("changed", [{"operation_id": "foreign"}, {"task_uid": "other"},
                                    {"purpose": "unknown"}, {"functional_success": False}])
def test_invalid_completion_with_production_state_store(production_registered_case, changed):
    controller, task, store, target, _observed, _stored = production_registered_case
    completion = WorkerCompletion("OP_AUTH", task.task_uid, "registration", target, "member", "registered",
                                  ("artifact:observed.html",), "store")
    store.patch_task("OP_AUTH", task.task_uid, recovery_context_updates={
        "worker_completions": {"registration:registered": {**completion.to_dict(), **changed}},
    })
    assert controller._worker_completions(task) == []


def test_unusable_completion_with_production_state_store(production_registered_case):
    controller, task, store, _target, _observed, stored = production_registered_case
    assert controller._registration_acceptance_payload(task, [stored])
    store.record_credential_status("OP_AUTH", "registered", "invalid", "operation", "Rejected credential", [])
    assert controller._worker_completions(task) == []


def test_missing_task_completion_fallback_and_stale_task_precedence(production_registered_case):
    controller, task, store, target, _observed, _stored = production_registered_case
    completion = WorkerCompletion("OP_AUTH", task.task_uid, "registration", target, "member", "registered",
                                  ("artifact:observed.html",), "store")
    stale = task
    controller._persist_worker_completion(task, completion)
    assert "worker_completions" not in stale.recovery_context
    assert controller._worker_completions(stale) == [completion]
    controller._persist_worker_completion(stale, replace(completion, purpose="authentication"))
    current = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert set(current.recovery_context["worker_completions"]) == {
        "registration:registered", "authentication:registered",
    }
    missing = replace(task, task_uid="not-persisted", recovery_context={
        "worker_completions": {"registration:registered": replace(completion, task_uid="not-persisted").to_dict()},
    })
    assert controller._worker_completions(missing) == [replace(completion, task_uid="not-persisted")]


@pytest.mark.parametrize("target,target_type", [("https://target.test", "network"),
                                               ("ssh://target.test", "network"), ("/repo/project", "filesystem")])
def test_evaluator_without_completions_uses_production_state_store(production_registered_case, monkeypatch,
                                                                  target, target_type):
    controller, _task, store, _target, _observed, _stored = production_registered_case
    plan = store.get_plan("OP_AUTH")
    ordinary = store.get_tasks("OP_AUTH")[0]
    plan = replace(plan, targets=[memory.OperationTarget("target", target, target_type)])
    monkeypatch.setattr(controller, "_memory_summary", lambda *_args, **_kwargs: "")
    prompt = controller._task_evaluator_prompt(plan, plan.phases[0], ordinary)
    assert isinstance(prompt, str)
    assert controller._worker_completion_section(ordinary) == ""


@pytest.mark.parametrize("persisted", [True, False])
def test_authentication_attempt_reload_uses_production_state_store(production_registered_case, persisted):
    controller, task, store, target, _observed, _stored = production_registered_case
    store.patch_task("OP_AUTH", task.task_uid, recovery_context_updates={
        "authentication_worker_attempts": {"registered": {"target": target, "status": "exhausted"}},
    })
    if not persisted:
        task = replace(task, task_uid="missing-task", recovery_context={
            "authentication_worker_attempts": {"registered": {"target": target, "status": "exhausted"}},
        })
    controller.state = workflow.WorkflowStateStore("OP_AUTH")
    credential = {"credential_id": "registered", "target": target, "role": "member",
                  "credential_type": "username_password"}
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    result = controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert result.terminal_reason == "authentication_attempt_exhausted"
    assert result.browser_session_ready


def test_controller_does_not_access_state_tasks_attribute():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(workflow.MultiAgentWorkflowController))
    forbidden = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                 and node.attr == "tasks" and isinstance(node.value, ast.Attribute) and node.value.attr == "state"]
    assert forbidden == [], "Use the state-store list_tasks() interface"


def test_retryable_authentication_attempt_reloads_from_sqlite(production_registered_case, monkeypatch):
    controller, task, store, target, _observed, _stored = production_registered_case
    credential = {"credential_id": "registered", "credential_type": "username_password",
                  "role": "member", "target": target}
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    runner = Mock(return_value=type("Result", (), {"reason": "stalled", "details": {"tool_call_counts": {"shell": 24}}})())
    monkeypatch.setattr(controller, "_run_worker_agent", runner)
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    persisted = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert persisted.recovery_context["authentication_worker_attempts"]["registered"]["status"] == "retryable"
    assert "authentication_worker_attempts" not in task.recovery_context
    database_path = store.db_path
    # Store methods close their own connections; a new instance reloads durable state.
    reopened = SQLiteApplicationStore(database_path, target)
    from types import SimpleNamespace

    client = SimpleNamespace(
        list_tasks=lambda **_kwargs: reopened.get_tasks("OP_AUTH"),
        patch_task=lambda **kwargs: reopened.patch_task("OP_AUTH", **kwargs),
    )
    monkeypatch.setattr(workflow, "get_memory_client", lambda **_kwargs: client)
    monkeypatch.setattr(workflow, "_get_database_store", lambda: reopened)
    reloaded = workflow.MultiAgentWorkflowController(
        runtime=controller.runtime, budget=BudgetConfig(max_duration_minutes=60),
        state_store=workflow.WorkflowStateStore("OP_AUTH"),
    )
    runner.reset_mock()
    reads = Mock(wraps=reloaded.state.list_tasks)
    monkeypatch.setattr(reloaded.state, "list_tasks", reads)
    monkeypatch.setattr(reloaded, "_run_worker_agent", runner)
    result = reloaded._run_authentication_credential_worker(task, setup, credential, (), [])
    reads.assert_called()
    assert result.terminal_reason != "authentication_attempt_exhausted"
    assert runner.call_count == 2
    second = {**credential, "credential_id": "second"}
    reloaded._run_authentication_credential_worker(task, replace(setup, credentials=(second,)), second, (), [])
    assert runner.call_count == 4
    latest = next(item for item in reopened.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert set(latest.recovery_context["authentication_worker_attempts"]) == {"registered", "second"}


@pytest.mark.parametrize("purpose", ["registration", "authentication"])
def test_flow_critic_uses_bound_recorder_contract(worker_case, purpose):
    controller, task, _store, target, outcome = worker_case
    kind = "browser_registration" if purpose == "registration" else "browser_form"
    attempt = controller._worker_flow_attempt(task, target, purpose)
    recorder = controller._worker_flow_recorder(task, target, purpose, kind, attempt)
    values = {"login_url": f"{target}/start", "steps": [{
        "url": f"{target}/start", "action": "form_submission", "field_names": ["First name 👤", "🔐"],
    }]}
    if purpose == "authentication":
        values["request_header_names"] = ["X-Session", "Tauser"]
    else:
        values["identity_fields"] = ["First name 👤"]
    recorder(**values)
    fields = workflow.get_tool_spec(recorder)["inputSchema"]["json"]["properties"]
    assert set(attempt["reviewable_fields"]) == set(fields)
    candidate = {**attempt["candidate"], "worker_review": {"internal": True}, "flow_id": "internal"}
    prompt = controller._flow_refinement_critic_prompt(
        purpose, target, candidate, [outcome], [], reviewable_fields=attempt["reviewable_fields"],
    )
    projected = json.loads(prompt.split("Candidate from successful worker execution:\n")[1].split("\nAuthorized")[0])
    assert set(projected) <= set(fields)
    assert "worker_review" not in projected and "flow_id" not in projected
    assert "backend payload keys are not mandatory" in prompt
    if purpose == "registration":
        assert "request_header_names" not in prompt
        assert projected["identity_fields"] == ["First name 👤"]
    else:
        assert projected["request_header_names"] == ["X-Session", "Tauser"]
        assert "not all HTTP headers" in prompt


@pytest.mark.parametrize("initial_candidate", [False, True])
@pytest.mark.parametrize("limit,approved_at", [(1, 1), (1, None), (3, 3), (3, None)])
def test_flow_review_counts_actual_reviews_and_stops_metadata_repairs(
    worker_case, monkeypatch, initial_candidate, limit, approved_at,
):
    controller, task, store, target, outcome = worker_case
    controller.flow_refinement_cycles = limit
    attempt = controller._worker_flow_attempt(task, target, "registration")
    recorder = controller._worker_flow_recorder(task, target, "registration", "browser_registration", attempt)
    if initial_candidate:
        recorder(login_url=f"{target}/signup")
    attempt["successful"] = True
    critiques = []

    def critique(*_args, **_kwargs):
        critiques.append(True)
        return {"approved": len(critiques) == approved_at, "feedback": ["Correct observed label"],
                "supporting_evidence_refs": ["artifact:observed.html"]}

    monkeypatch.setattr(controller, "_run_json_text_agent", critique)

    @tool(name="browser_perform_action")
    def submit_again():
        raise AssertionError("Successful registration cannot be repeated")

    def repair(prompt, tools):
        assert "do not create another account, resubmit, or log in again" in prompt
        assert "browser_perform_action" not in {workflow.get_tool_name(item) for item in tools}
        recorder(login_url=f"{target}/signup", identity_fields=["Email 📧"])
        return workflow.TaskExecutorCycleResult("recorded", [], reason="worker_flow_metadata_staged")

    repairs = Mock(side_effect=repair)
    events = []
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)
    controller._refine_worker_flow(task, target, "registration", attempt, [outcome], repairs, [recorder, submit_again])
    assert len(critiques) == (approved_at or limit)
    assert repairs.call_count == (not initial_candidate) + (approved_at or limit) - 1
    assert bool(store.list_authentication_flows(target, purpose="registration")) is (approved_at is not None)
    review = next(event for event in reversed(events) if event["type"] == "flow_refinement_review")
    assert review["reviews"] == len(critiques)
    assert review["functional_success"]
    assert review["stop_reason"] == ("approved" if approved_at else "review_limit_reached")
    if approved_at is None:
        gap = controller.state.list_tasks()[0].recovery_context["registration_flow_gap"]
        assert gap["reviews"] == limit and gap["stop_reason"] == "review_limit_reached"
        assert not attempt.get("mapping_approved")


def test_flow_critic_receives_authoritative_validation_proof(worker_case):
    controller, _task, _store, target, outcome = worker_case
    candidate = {
        "kind": "browser_form", "login_url": target + "/login",
        "validation_url": target + "/earlier", "request_header_names": ["Tauser"],
    }
    metadata = {"validation_url": target + "/verified", "evidence_refs": ["artifact:proof.json"]}
    prompt = controller._flow_refinement_critic_prompt(
        "authentication", target, candidate, [outcome], [],
        validation_metadata=metadata, authorized_references=["artifact:proof.json"],
    )
    assert target + "/verified" in prompt
    assert target + "/earlier" not in prompt
    assert "artifact:proof.json" in prompt
    assert "supersedes earlier recorder output" in prompt


@pytest.mark.parametrize("kind", ["username_password", "api_key"])
def test_completed_authentication_mapping_review_survives_session_expiry(worker_case, monkeypatch, kind):
    controller, task, store, target, outcome = worker_case
    credential = {"credential_id": "selected", "credential_type": kind, "target": target, "role": "member"}
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)
    capture_result = json.dumps({
        "status": "valid", "validation_url": target + "/verified", "evidence_refs": ["artifact:observed.html"],
    })
    browser_capture = AsyncMock(return_value=capture_result)
    key_capture = Mock(return_value=capture_result)
    monkeypatch.setattr(workflow, "capture_browser_authenticated_context", browser_capture)
    monkeypatch.setattr(workflow, "establish_credential_authenticated_context", key_capture)
    controller.text_runner.side_effect = [
        json.dumps({"approved": False, "feedback": ["Record the observed field labels"]}),
        json.dumps(approval()),
    ]
    runs = []

    @contextmanager
    def factory(_role, tools, _system):
        def run(prompt, _policy, chosen=None):
            selected = chosen or tools
            runs.append(prompt)
            recorder = next(item for item in selected if workflow.get_tool_name(item).startswith("record_"))
            recorder(
                login_url=target + "/login",
                **({"validation_url": target + "/verified"} if kind == "api_key" else {}),
            )
            if any(workflow.get_tool_name(item) in {
                "capture_browser_authenticated_context", "establish_credential_authenticated_context",
            } for item in tools):
                capture = next(item for item in tools if workflow.get_tool_name(item) in {
                    "capture_browser_authenticated_context", "establish_credential_authenticated_context",
                })
                if kind == "username_password":
                    import asyncio

                    asyncio.run(capture())
                else:
                    capture(validation_url=target + "/verified")
                assert controller.state.list_tasks()[0].recovery_context["worker_completions"]
            else:
                assert "do not create another account" in prompt
                assert not any(workflow.get_tool_name(item) == "browser_perform_action" for item in selected)
            return workflow.TaskExecutorCycleResult("completed", [outcome], reason="completed")

        yield run

    controller.executor_session_factory = factory
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert len(runs) == 2
    assert browser_capture.call_count + key_capture.call_count == 1
    descriptor = store.list_authentication_flows(target)[0]["descriptor"]
    assert descriptor["validation_url"] == target + "/verified"
    assert descriptor["worker_review"]["reviews"] == 2
    retained = controller.state.list_tasks()[0].recovery_context["worker_completions"]
    completion = WorkerCompletion.from_dict(next(iter(retained.values())))
    assert completion.evidence_refs == ("artifact:observed.html",)
    assert completion.mapping_approved
    # Historical completion cannot satisfy fresh session requirements.
    assert controller._worker_completions(task) == []
    # A new credential must establish its own context, rather than inherit completed validation.
    controller.text_runner.side_effect = None
    controller.text_runner.return_value = json.dumps(approval())
    second = {**credential, "credential_id": "second"}
    controller._run_authentication_credential_worker(
        task, replace(setup, credentials=(second,)), second, (), [],
    )
    assert len(runs) == 3
    assert browser_capture.call_count + key_capture.call_count == 2
    assert len(controller.state.list_tasks()[0].recovery_context["worker_completions"]) == 2


def test_controller_validation_proof_is_retained_with_published_mapping(worker_case):
    controller, task, store, target, outcome = worker_case
    proof = Path(workflow._operation_output_root()) / "validation.json"
    proof.write_text(json.dumps({"proof_method": "anonymous_access_denied"}))
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    recorder(login_url=target + "/login")
    attempt["successful"] = True
    attempt["validation_url"] = target + "/verified"
    attempt["validation_metadata"] = {
        "validation_url": target + "/verified", "evidence_refs": [f"artifact:{proof}"],
    }
    controller.text_runner.return_value = json.dumps(approval())
    controller._refine_worker_flow(
        task, target, "authentication", attempt, [outcome], Mock(), [recorder],
    )
    descriptor = store.list_authentication_flows(target)[0]["descriptor"]
    assert set(descriptor["evidence_refs"]) == {"artifact:observed.html", "artifact:validation.json"}
    assert set(descriptor["worker_review"]["approval_digests"]) == set(descriptor["evidence_refs"])


@pytest.mark.parametrize("kind", ["username_password", "api_key"])
def test_authentication_stops_failed_recovery_and_records_capture_reason(worker_case, monkeypatch, kind):
    controller, task, store, target, outcome = worker_case
    credential = {"credential_id": "selected", "credential_type": kind, "target": target, "role": "member"}
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)
    reason = "AUTH_CAPTURE_PROOF_FAILED: public and authenticated responses are identical"
    monkeypatch.setattr(workflow, "capture_browser_authenticated_context", AsyncMock(side_effect=ValueError(reason)))
    monkeypatch.setattr(workflow, "establish_credential_authenticated_context", Mock(side_effect=ValueError(reason)))
    runs = []

    @contextmanager
    def factory(_role, tools, _system):
        def run(prompt, _policy):
            runs.append(prompt)
            capture = next(item for item in tools if workflow.get_tool_name(item) in {
                "capture_browser_authenticated_context", "establish_credential_authenticated_context",
            })
            with pytest.raises(ValueError, match="AUTH_CAPTURE_PROOF_FAILED"):
                if kind == "username_password":
                    import asyncio

                    asyncio.run(capture())
                else:
                    capture(validation_url=target + "/public")
            return workflow.TaskExecutorCycleResult("failed", [outcome], reason="stalled")

        yield run

    controller.executor_session_factory = factory
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert len(runs) == 2
    controller.text_runner.assert_not_called()
    assert not store.list_authentication_flows(target)
    gap = controller.state.list_tasks()[0].recovery_context["authentication_flow_gap"]
    assert gap["stop_reason"] == "functional_recovery_failed"
    assert gap["feedback"] == [reason]
    assert gap["reviews"] == 0


@pytest.mark.parametrize("kind", ["browser_form", "browser_redirect", "browser_mfa"])
def test_browser_flow_replay_excludes_previous_identity_verification(kind):
    steps = [
        {"action": "navigation", "url": "https://target.test/login"},
        {"action": "form_submission", "url": "https://target.test/login", "field_names": ["🔑"]},
        {"action": "redirect", "url": "https://target.test/home"},
        {"action": "verification", "url": "https://target.test/users/previous/photo"},
    ]
    flow = {"kind": kind, "steps": steps, "validation_url": steps[-1]["url"]}
    setup = workflow.AuthenticationSetupRequest(
        "task", "authentication", ("https://target.test",), ("https://target.test",),
        ({"credential_type": "username_password"},), (flow,),
    )
    prompt = setup.credential_worker_toon()
    assert "previous" not in prompt
    assert "navigation" in prompt and "form_submission" in prompt and "redirect" in prompt
    assert "🔑" in prompt
    assert flow["steps"] == steps and flow["validation_url"] == steps[-1]["url"]
    normalized = workflow._normalize_authentication_verification(flow, "https://target.test/users/current/photo")
    assert normalized["steps"][:-1] == steps[:-1]
    assert normalized["steps"][-1]["url"] == normalized["validation_url"]
    assert workflow._normalize_authentication_verification(normalized, normalized["validation_url"]) == normalized


@pytest.mark.parametrize("kind", ["api_key", "oauth2_client", "browser_registration"])
def test_verification_normalization_preserves_non_browser_authentication_steps(kind):
    flow = {"kind": kind, "validation_url": "https://target.test/check", "steps": [
        {"action": "verification", "url": "https://target.test/check"},
    ]}
    assert workflow._normalize_authentication_verification(flow) == flow
    assert workflow._normalize_authentication_verification(flow, "https://target.test/new")["steps"] == flow["steps"]


@pytest.mark.parametrize("repair_required", [False, True])
def test_authentication_review_and_persistence_use_current_verification(worker_case, repair_required):
    controller, task, store, target, outcome = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)

    def record_old_verification():
        recorder(login_url=target + "/login", steps=[
            {"action": "verification", "url": target + "/users/previous/photo"},
            {"action": "verification", "url": target + "/users/previous/photo"},
        ])

    record_old_verification()
    attempt.update(successful=True, validation_url=target + "/users/current/photo")
    review_count = 0

    def critique(_role, prompt, _tools, _system):
        nonlocal review_count
        assert "/users/previous/photo" not in prompt
        assert "/users/current/photo" in prompt
        review_count += 1
        return json.dumps(
            {"approved": False, "feedback": ["Repair metadata"]}
            if repair_required and review_count == 1 else approval()
        )

    controller.text_runner.side_effect = critique

    def repair(_prompt, _tools):
        record_old_verification()
        return workflow.TaskExecutorCycleResult("metadata repaired", [])

    controller._refine_worker_flow(task, target, "authentication", attempt, [outcome], repair, [recorder])
    row = store.list_authentication_flows(target, purpose="authentication")[0]
    assert row["descriptor"]["steps"] == [{
        "action": "verification", "url": target + "/users/current/photo", "field_names": [],
    }]
    assert row["descriptor"]["validation_url"] == target + "/users/current/photo"


@pytest.fixture
def replay_worker_case(worker_case, monkeypatch):
    controller, task, store, target, observed = worker_case
    flow = {
        "target": target, "purpose": "authentication", "kind": "browser_form",
        "flow_version": authentication.AUTHENTICATION_FLOW_VERSION, "provenance": "observed_worker_flow",
        "login_url": target + "/login", "allowed_origins": [target], "request_header_names": ["X-Session"],
        "evidence_refs": ["artifact:old-operation.html"], "worker_review": {"cycle": 1},
        "replay_version": 1, "replay_actions": [
            {"action": "navigate", "url": target + "/login"},
            {"action": "fill", "locator": "#email", "credential_field": "email"},
            {"action": "click", "locator": "#submit"},
        ],
    }
    stored = store.upsert_authentication_flow("OP_AUTH", flow, status="validated")
    flow["flow_id"] = stored["flow_id"]
    monkeypatch.setattr(workflow, "reset_authentication_browser_session", AsyncMock())
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)
    capture = AsyncMock(return_value=json.dumps({
        "status": "valid", "validation_url": target + "/users/current/photo",
        "credential_header_names": ["x-session"], "evidence_refs": ["artifact:observed.html"],
    }))
    monkeypatch.setattr(workflow, "capture_browser_authenticated_context", capture)
    calls = []

    def build(identifier, _origins, attempt, bound_capture, *, observations=None):
        @tool(name="perform_login_action")
        async def perform_login_action(action: str, **arguments):
            calls.append((identifier, action))
            attempt["replay_actions"].append({"action": action, **arguments})
            if action == "click":
                await bound_capture()
            return "performed"
        return perform_login_action

    monkeypatch.setattr(workflow, "build_login_action_tool", build)
    credential = {"credential_id": "first", "credential_type": "username_password", "target": target, "role": "member"}
    setup = workflow.AuthenticationSetupRequest(task.task_uid, "authentication", (target,), (target,), (credential,))
    return controller, task, store, target, flow, capture, calls, credential, setup, observed


def test_approved_login_replay_uses_fresh_proof_for_each_account_without_agents(replay_worker_case):
    controller, task, store, target, flow, capture, calls, credential, setup, _observed = replay_worker_case
    controller.executor_session_factory = Mock(side_effect=AssertionError("Replay must not create an agent"))
    events = []
    controller._emit_workflow_event = events.append
    before = store.list_authentication_flows(target)[0]
    for identifier in ("first", "second"):
        selected = {**credential, "credential_id": identifier}
        result = controller._run_authentication_credential_worker(task, setup, selected, (), [])
        assert result.terminal_reason == "unchanged_replay"
    assert capture.await_count == 2
    assert [identifier for identifier, action in calls if action == "click"] == ["first", "second"]
    assert controller.text_runner.call_count == 0
    after = store.list_authentication_flows(target)[0]
    assert after["descriptor"] == before["descriptor"]
    assert after["evidence_refs"] == before["evidence_refs"]
    assert after["last_validated_at"] >= before["last_validated_at"]
    assert all(event["review_skipped"] for event in events if event["type"] == "authentication_replay")
    completions = controller.state.list_tasks()[0].recovery_context["worker_completions"]
    assert len(completions) == 2
    assert all(value["evidence_refs"] == ["artifact:observed.html"] for value in completions.values())
    assert all(value["flow_id"] == flow["flow_id"] for value in completions.values())


@pytest.mark.parametrize("cause", [
    "changed_transport", "changed_binding", "missing_control", "invalid_script", "unvalidated", "capture_exception",
])
def test_login_replay_fallback_reviews_changes_without_repeating_success(replay_worker_case, monkeypatch, cause):
    controller, task, store, target, flow, capture, calls, credential, setup, observed = replay_worker_case
    if cause == "changed_transport":
        capture.return_value = json.dumps({
            "status": "valid", "validation_url": target + "/private",
            "credential_header_names": ["Tauser"], "evidence_refs": ["artifact:observed.html"],
        })
    elif cause == "changed_binding":
        setup = replace(setup, client_storage_header_bindings=({
            "header_name": "X-Session", "storage_key": "session", "value_template": "{value}",
        },))
    elif cause == "capture_exception":
        capture.side_effect = RuntimeError("capture transport failure")
    elif cause == "invalid_script":
        flow["replay_actions"] = [{"action": "click", "locator": "#submit"}]
        store.upsert_authentication_flow("OP_AUTH", flow, status="validated")
    elif cause == "unvalidated":
        capture.side_effect = ValueError("not authenticated")
    else:
        def build(_identifier, _origins, attempt, _capture, *, observations=None):
            @tool(name="perform_login_action")
            async def action(action: str, **arguments):
                if action == "fill":
                    raise ValueError("missing control")
                attempt["replay_actions"].append({"action": action, **arguments})
                return "performed"
            return action
        monkeypatch.setattr(workflow, "build_login_action_tool", build)
    runs = []

    @contextmanager
    def factory(_role, _tools, _system):
        def run(prompt, _policy, _chosen=None):
            runs.append(prompt)
            assert "Continue from the current browser state" in prompt
            assert "Never repeat a successful submission" in prompt
            if cause == "missing_control":
                assert '"next_login_action": {"action": "fill"' in prompt
                assert len(calls) == 0
            return workflow.TaskExecutorCycleResult("fallback", [observed])
        yield run

    controller.executor_session_factory = factory
    refinement = Mock(return_value=[])
    monkeypatch.setattr(controller, "_refine_worker_flow", refinement)
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    refinement.assert_called_once()
    attempt = refinement.call_args.args[3]
    if cause in {"changed_transport", "changed_binding"}:
        assert not runs  # Only metadata review is needed after fresh authentication succeeded.
        if cause == "changed_transport":
            assert attempt["candidate"]["request_header_names"] == ["Tauser"]
        else:
            assert attempt["candidate"]["storage_header_bindings"]
    else:
        assert len(runs) == 1
    assert store.list_authentication_flows(target)[0]["descriptor"]["request_header_names"] == ["X-Session"]


def test_login_context_and_outcomes_omit_unrelated_and_large_observations(worker_case):
    controller, task, _store, target, observed = worker_case
    setup = workflow.AuthenticationSetupRequest(
        task.task_uid, "authentication", (target,), (target,), ({"target": target},),
        ({"kind": "browser_form", "login_url": target + "/login"},),
        authentication_flow_hints=({"kind": "browser_form", "login_url": "https://unrelated.test/login"},),
    )
    assert "unrelated.test" not in setup.credential_worker_toon()
    huge = replace(observed, input_summary="secret input", output_summary="large body " * 10000,
                   raw_output_summary="large body " * 10000)
    rendered = controller._login_outcome_section([huge])
    assert "large body" not in rendered and "secret input" not in rendered
    assert all(ref in rendered for ref in observed.artifact_refs)
    failed = replace(huge, success=False, output_summary="failure " * 10000)
    assert len(controller._login_outcome_section([failed])) < 1000


@pytest.mark.parametrize("complete,valid", [(True, True), (False, True), (True, False)])
@pytest.mark.parametrize("repair", [False, True])
def test_controller_attaches_only_complete_login_scripts_before_review(worker_case, complete, valid, repair):
    controller, task, store, target, observed = worker_case
    attempt = controller._worker_flow_attempt(task, target, "authentication")
    actions = [{"action": "navigate", "url": target + ("/login" if valid else "/other")}]
    attempt.update(
        replay_actions=actions, replay_complete=complete, successful=True, validation_url=target + "/private",
    )
    recorder = controller._worker_flow_recorder(task, target, "authentication", "browser_form", attempt)
    recorder(login_url=target + "/login")
    # Successful actions after initial recording must also be attached at review time.
    actions.append({"action": "select", "locator": "#mode", "value": "rest"})
    controller.text_runner.side_effect = (
        [json.dumps({"approved": False, "feedback": ["Correct labels"]}), json.dumps(approval())]
        if repair else [json.dumps(approval())]
    )

    def repair_metadata(_prompt, _tools):
        recorder(login_url=target + "/login")
        return workflow.TaskExecutorCycleResult("recorded", [])

    controller._refine_worker_flow(task, target, "authentication", attempt, [observed], repair_metadata, [recorder])
    descriptor = store.list_authentication_flows(target)[0]["descriptor"]
    if complete and valid:
        assert descriptor["replay_version"] == 1
        assert descriptor["replay_actions"] == actions
        assert "replay_actions" in controller.text_runner.call_args.args[1]
    else:
        assert "replay_actions" not in descriptor


def test_replay_cannot_use_prior_context_as_fresh_execution_proof(replay_worker_case, monkeypatch):
    controller, task, _store, _target, _flow, capture, calls, credential, setup, observed = replay_worker_case
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: True)
    capture.side_effect = ValueError("new login did not validate")

    @contextmanager
    def factory(_role, _tools, _system):
        yield lambda *_args: workflow.TaskExecutorCycleResult("incomplete", [observed])

    controller.executor_session_factory = factory
    refinement = Mock(return_value=[])
    monkeypatch.setattr(controller, "_refine_worker_flow", refinement)
    controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert len(calls) == 3
    assert refinement.call_args.args[3]["functional_completed"] is False
    assert not refinement.call_args.args[3]["mapping_approved"]


def test_replay_credential_rejection_stops_without_another_agent(replay_worker_case, monkeypatch):
    controller, task, _store, _target, _flow, _capture, calls, credential, setup, _observed = replay_worker_case
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: {"outcome": "credential_rejected"})
    controller.executor_session_factory = Mock(side_effect=AssertionError("Rejected credentials must stop"))
    result = controller._run_authentication_credential_worker(task, setup, credential, (), [])
    assert result.terminal_reason == "credential_rejected"
    assert not calls
    state = controller.state.list_tasks()[0].recovery_context["authentication_worker_attempts"]
    assert state["first"]["status"] == "exhausted"


@pytest.mark.parametrize("purpose", ["authentication", "registration"])
@pytest.mark.parametrize("approved", [True, False])
def test_critic_limit_synthesis_preserves_exact_returned_evidence(worker_case, tmp_path, purpose, approved):
    controller, _task, _store, _target, _observed = worker_case
    (tmp_path / "observed.html").write_text("<head>" + "x" * 2000 + "</head>\n<form>📧 Password</form>")
    calls = []

    def review(role, prompt, tools, _system):
        calls.append(prompt)
        if tools:
            reader = next(item for item in tools if workflow.get_tool_name(item) == "read_artifact")
            result = reader(path="artifact:observed.html")
            journal = ToolOutcomeJournal()
            outcome = journal.append(
                tool_use_id="read", tool_name="read_artifact", success=True, correctable=False,
                tool_input={"path": "artifact:observed.html"}, output=result,
            )
            assert "📧" not in outcome.output_summary
            raise workflow.EvaluatorArtifactReadLimitExceeded("read limit", [outcome])
        assert "📧 Password" in prompt
        assert "Exact evidence returned" in prompt
        assert "Artifact access is closed" in prompt
        return json.dumps(approval() if approved else {"approved": False, "feedback": ["Wrong field"]})

    controller.text_runner.side_effect = review
    result = controller._run_json_text_agent(
        f"{purpose}_flow_critic", "Review observed fields", [], "Review evidence",
        tool_factory=lambda: controller._flow_refinement_artifact_tools(["artifact:observed.html"]),
    )
    assert result["approved"] is approved
    assert len(calls) == 2
    assert list((tmp_path / "artifacts" / "review_evidence").glob("*/index.jsonl"))


def test_flow_read_allowance_scales_and_duplicate_groups_remain_availability_only(worker_case, tmp_path):
    controller, _task, _store, _target, _observed = worker_case
    references = []
    for index in range(5):
        path = tmp_path / f"capture{index}.txt"
        path.write_text("same" if index < 2 else f"different {index}")
        references.append("artifact:" + path.name)
    reader, _searcher = controller._flow_refinement_artifact_tools(references)
    for reference in references:
        assert reference in reader(path=reference)
    assert controller._flow_artifact_groups([*references, "artifact:missing"]) == [
        references[:2], *[[ref] for ref in references[2:]], ["artifact:missing"],
    ]
    assert controller._flow_observation_section({}) == ""
    from modules.tools.browser_flow_observations import BrowserFlowObservations
    attempt = {}
    BrowserFlowObservations(tmp_path, attempt).record("https://target.test/signup", [], {"action": "navigate"})
    assert "navigate" in controller._flow_observation_section(attempt)


def test_familiar_login_navigation_learns_complete_script_and_resets_attempt_facts(replay_worker_case, monkeypatch):
    controller, task, store, target, flow, _capture, calls, credential, setup, observed = replay_worker_case
    flow.pop("replay_version")
    flow.pop("replay_actions")
    store.upsert_authentication_flow("OP_AUTH", flow, status="validated")
    attempt = controller._worker_flow_attempt(task, target, "authentication", kinds=("browser_form",))
    attempt["observation_refs"] = ["artifact:previous-account.json"]
    @tool(name="browser_goto_url")
    async def navigation(url: str) -> str:
        """Navigate to a login page."""
        raise AssertionError("The controller must use the typed navigation executor")
    refined = Mock(return_value=[])
    monkeypatch.setattr(controller, "_refine_worker_flow", refined)
    @contextmanager
    def factory(_role, tools, _system):
        names = {workflow.get_tool_name(item): item for item in tools}
        def run(_prompt, _policy, _selected=None):
            asyncio.run(names["browser_goto_url"](url=target + "/login"))
            asyncio.run(names["perform_login_action"](action="fill", locator="#email", credential_field="email"))
            asyncio.run(names["perform_login_action"](action="click", locator="#submit"))
            return workflow.TaskExecutorCycleResult("completed", [observed])
        yield run
    controller.executor_session_factory = factory
    controller._run_authentication_credential_worker(task, setup, credential, (), [navigation])
    attempt = refined.call_args.args[3]
    assert attempt["replay_complete"]
    assert [item["action"] for item in attempt["replay_actions"]] == ["navigate", "fill", "click"]
    assert calls == [("first", "navigate"), ("first", "fill"), ("first", "click")]
    assert attempt["observation_refs"] == []
    assert attempt["functional_completed"]
