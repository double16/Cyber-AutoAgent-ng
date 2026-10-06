"""Worker-owned flow review, publication, and continuation invariants."""

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from strands import tool

from modules.agents import multi_agent_workflow as workflow
from modules.config.types import BudgetConfig
from modules.handlers.tool_recovery import ToolOutcomeJournal
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
@pytest.mark.parametrize("existing", [False, True])
def test_completed_worker_flow_is_published_once_or_updates_existing(worker_case, purpose, existing):
    controller, task, store, target, outcome = worker_case
    roles = ("member",) if purpose == "registration" else ()
    kind = "browser_registration" if purpose == "registration" else "browser_form"
    recorder_attempt = controller._worker_flow_attempt(task, target, purpose, roles)
    recorder = controller._worker_flow_recorder(task, target, purpose, kind, recorder_attempt, roles)
    recorder(login_url=f"{target}/start", steps=[{"url": f"{target}/start", "action": "navigation"}])
    assert not store.list_authentication_flows(target, purpose=purpose)
    if existing:
        stored = store.upsert_authentication_flow("OP_AUTH", recorder_attempt["candidate"], status="validated")
        controller._worker_flow_attempts.clear()
        recorder_attempt = controller._worker_flow_attempt(task, target, purpose, roles)
        recorder = controller._worker_flow_recorder(task, target, purpose, kind, recorder_attempt, roles)
        recorder(login_url=f"{target}/changed")
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
    assert descriptor["login_url"].endswith("changed" if existing else "start")
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
        assert ("browser_perform_action" in names) is not success
        assert (
            "same credential or registration identity" in prompt if not success else "do not create another" in prompt
        )
        recorder(login_url=f"{target}/start", steps=[{"url": f"{target}/final", "action": "submit"}])
        attempt["successful"] = True
        return workflow.TaskExecutorCycleResult("repaired", [])

    repair = Mock(side_effect=repair)
    controller._refine_worker_flow(task, target, purpose, attempt, [outcome], repair, [recorder, submission])
    assert repair.call_count == 1
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
    assert attempt["successful"] == (cause != "no_success")
    assert repair.call_count <= controller.flow_refinement_cycles - 1


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
        [{"url": "https://target.test/step", "action": "submit", "field_names": ["value@example.test"]}],
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


@pytest.mark.parametrize("kind", ["api_form", "browser_form"])
def test_existing_username_password_flow_keeps_both_login_methods_available(worker_case, kind):
    controller, _task, _store, _target, _outcome = worker_case

    @tool(name="browser_goto_url")
    def navigate(url: str) -> str:
        """Navigate to an observed login step."""
        return url

    @tool(name="ensure_authenticated_context")
    def api_login(
        credential_id: str,
        flow_id: str,
        login_url: str,
        validation_url: str,
        request_format: str,
        additional_fields: dict | None,
    ) -> str:
        """Establish an API login context."""
        return credential_id

    tools = controller._authentication_credential_worker_tools(
        [navigate, api_login],
        {"credential_id": "credential", "credential_type": "username_password"},
        {"kind": kind, "flow_id": "flow"},
    )
    assert {workflow.get_tool_name(item) for item in tools} == {"browser_goto_url", "ensure_authenticated_context"}


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


@pytest.mark.parametrize("kind", ["api_key", "oauth2_client", "browser_form", "api_form"])
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
    attempt = controller._worker_flow_attempt(task, target, "authentication", kinds=("browser_form", "api_form"))
    assert bool(attempt["existing"]) == (kind in {"browser_form", "api_form"})


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


def test_api_form_worker_can_run_when_browser_reset_is_unavailable(worker_case, monkeypatch):
    controller, task, _store, target, _outcome = worker_case
    credential = {
        "credential_id": "selected",
        "credential_type": "username_password",
        "target": target,
        "role": "member",
    }
    flow = {"kind": "api_form", "login_url": f"{target}/login", "validation_url": f"{target}/protected"}
    monkeypatch.setattr(
        workflow, "reset_authentication_browser_session", AsyncMock(side_effect=RuntimeError("no browser"))
    )
    monkeypatch.setattr(controller, "_refine_worker_flow", Mock(return_value=[]))
    monkeypatch.setattr(workflow, "authentication_context_is_valid", lambda *_args: False)
    monkeypatch.setattr(workflow, "authentication_attempt_result", lambda *_args: None)

    def run(_role, _prompt, tools, _system, _policy):
        names = {workflow.get_tool_name(tool) for tool in tools}
        assert "ensure_authenticated_context" in names
        assert "capture_browser_authenticated_context" not in names
        assert not any(name.startswith("browser_") for name in names)
        return workflow.TaskExecutorCycleResult("API setup", [])

    controller.work_runner = run
    setup = workflow.AuthenticationSetupRequest(
        task_uid=task.task_uid,
        workstream="authentication",
        targets=(target,),
        allowed_origins=(target,),
        credentials=(credential,),
        authentication_flows=(flow,),
    )
    assert controller._run_authentication_credential_worker(task, setup, credential, (flow,), []).browser_session_ready


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
    assert len(policies) == (3 if state == "recoverable" else 2)
    rows = store.list_authentication_flows(target)
    assert bool(rows) is (state == "success")
    assert controller.text_runner.call_count == (0 if state == "rejected" else 1 if state == "success" else 2)


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
    assert "filling a field does not prove" in prompt
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
        validation_url=f"{target}/protected",
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
