"""Role-aware comparison expansion, selection, and continuation repair."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from modules.authentication_requirements import (
    AuthenticationRequirement,
    AuthenticationSelectionError,
    authentication_comparison_matrix,
    authentication_role_catalog,
    credential_comparison_matches,
    select_authentication_credentials,
    validate_comparison_requirement,
)
from modules.tools.memory import OperationTarget, PlanPhase
from tests.test_worker_flows import worker_case  # noqa: F401


@pytest.mark.parametrize("target", ["https://target.test", "ssh://target.test", "/repo/project"])
def test_role_catalog_separates_design_from_available_credentials(target):
    catalog = authentication_role_catalog([
        {"credential_id": "one", "target": target, "role": "user", "status": "invalid"},
        {"credential_id": "two", "target": "other", "role": "owner"},
        {"credential_id": "empty", "target": target, "role": ""},
    ], [{"target": target, "roles": ["admin", "user", ""], "flow_id": "signup"}, {"roles": ["ignored"]}])
    assert set(catalog[target]) == {"admin", "user"}
    assert catalog[target]["user"] == ["credential:one", "registration_flow:signup"]
    assert "owner" not in catalog[target]
    assert authentication_comparison_matrix([], ["none", "account", "role"]) == []


@pytest.mark.parametrize("roles,expected", [
    (["user"], [("none", ["user"]), ("account", ["user"])]),
    (["user", "admin", "user"], [("none", ["admin"]), ("none", ["user"]),
                                  ("account", ["admin"]), ("account", ["user"]), ("role", ["admin", "user"])]),
])
def test_comparison_matrix_is_minimal_and_deduplicated(roles, expected):
    actual = authentication_comparison_matrix(roles, ["none", "account", "role", "role"])
    assert [(item.comparison, item.roles) for item in actual] == expected
    for requirement in actual:
        validate_comparison_requirement(requirement)
    with pytest.raises(ValueError, match="Unsupported"):
        authentication_comparison_matrix(roles, ["unknown"])


@pytest.mark.parametrize("comparison,roles", [("role", ["user"]), ("role", ["a", "b", "c"]),
                                             ("account", ["user", "admin"])])
def test_contradictory_declarations_are_rejected(comparison, roles):
    with pytest.raises(ValueError):
        validate_comparison_requirement(AuthenticationRequirement(comparison=comparison, roles=roles))


def test_account_comparisons_require_distinct_accounts_in_the_same_role():
    records = [
        {"credential_id": "a", "target": "target", "role": "user", "account_label": "one"},
        {"credential_id": "b", "target": "target", "role": "admin", "account_label": "two"},
        {"credential_id": "c", "target": "target", "role": "user", "account_label": "three"},
    ]
    assert not credential_comparison_matches(records[0], records[1], "account")
    assert credential_comparison_matches(records[0], records[2], "account")
    assert not credential_comparison_matches(records[0], records[0], "account")
    assert not credential_comparison_matches(records[0], {**records[2], "account_label": "one"}, "account")
    assert not credential_comparison_matches(records[0], {**records[2], "account_label": ""}, "account")
    for comparison, roles, expected in [("none", ["user"], ["a"]), ("account", ["user"], ["a", "c"]),
                                       ("role", ["admin", "user"], ["a", "b"])]:
        selected = select_authentication_credentials(
            records, ["target"], AuthenticationRequirement(comparison=comparison, roles=roles),
            is_valid=lambda record: record["credential_id"] == "a",
        )
        assert [record["credential_id"] for record in selected] == expected
    with pytest.raises(AuthenticationSelectionError):
        select_authentication_credentials(records[:1], ["target"],
                                          AuthenticationRequirement(comparison="account", roles=["user"]),
                                          is_valid=lambda _record: True)


@pytest.fixture
def matrix_case(request):
    controller, task, store, target, observed = request.getfixturevalue("worker_case")
    phase = PlanPhase(id=3, title="Authentication", status="active")
    plan = replace(controller.state.get_plan(), current_phase=3, total_phases=3, phases=[
        PlanPhase(id=1, title="Inventory", status="done"),
        PlanPhase(id=2, title="Credentials", status="done"), phase,
    ])
    controller.state.plan = plan
    controller.runtime.config.module = "web"
    store.store_plan("OP_AUTH", plan)
    task = replace(task, phase=3, target_scope="subset", target_ids=["target"], recovery_context={
        "phase_task_contract": {"module": "web", "phase_id": 3, "task_role": "mapping",
                                "workstream": "authorization_comparison", "execution_owner": "executor"},
        "authentication_requirement": {"comparison": "role", "roles": ["user"]},
    })
    controller.state.tasks = [task]
    store.store_task("OP_AUTH", task)
    return controller, task, store, plan, phase, target, observed


@pytest.mark.parametrize("target", ["https://target.test", "ssh://target.test", "/repo/project"])
def test_expansion_preserves_subjects_and_does_not_infer_roles(matrix_case, target):
    controller, task, _store, plan, phase, _target, _observed = matrix_case
    plan = replace(plan, targets=[OperationTarget("target", target, "network")])
    catalog = {target: {"user": ["credential:one"], "admin": ["registration_flow:signup"]}}
    generated = controller._expand_authentication_comparisons(plan, phase, [task], [], catalog)
    assert len(generated) == 3
    assert [item.recovery_context["authentication_requirement"] for item in generated] == [
        {"comparison": "account", "roles": ["admin"]}, {"comparison": "account", "roles": ["user"]},
        {"comparison": "role", "roles": ["admin", "user"]},
    ]
    assert all(item.acceptance.basis == task.acceptance.basis and item.target_ids == ["target"] for item in generated)
    assert controller._expand_authentication_comparisons(plan, phase, [task], generated, catalog) == []
    assert controller._expand_authentication_comparisons(plan, phase, [task], [], {}) == []
    frozen = replace(task, recovery_context={
        **task.recovery_context, "validation_auth_context": {"credential_ids": ["x"]},
    })
    assert controller._expand_authentication_comparisons(plan, phase, [frozen], [], catalog) == [frozen]
    baseline = replace(task, recovery_context={"phase_task_contract": {"workstream": "unauthenticated_baseline"}})
    assert controller._expand_authentication_comparisons(plan, phase, [baseline], [], catalog) == [baseline]


@pytest.mark.parametrize("status", ["active", "pending", "partial_failure"])
def test_single_role_continuation_replaces_bad_requirement_once(matrix_case, status):
    controller, task, store, plan, phase, target, _observed = matrix_case
    store.store_credential("OP_AUTH", {
        "credential_id": "defined-user", "credential_type": "username_password", "role": "user",
        "target": target, "origin": "provided", "status": "invalid", "management_policy": "user",
        "payload": {"username": "one", "password": "secret"},
    })
    task = replace(task, status=status, recovery_context={**task.recovery_context, "authentication_setup": {
        "reason": "role_comparison_credentials_unavailable",
    }})
    controller.state.store_task(task)
    controller._reconcile_authentication_comparisons(plan, phase)
    tasks = controller.state.list_tasks()
    assert len(tasks) == 2
    assert next(item for item in tasks if item.task_uid == task.task_uid).status == "superseded"
    replacement = next(item for item in tasks if item.task_uid != task.task_uid)
    assert replacement.replacement_of == task.task_uid
    assert replacement.recovery_context["authentication_requirement"] == {"comparison": "account", "roles": ["user"]}
    assert "authentication_setup" not in replacement.recovery_context
    assert replacement.acceptance.basis == task.acceptance.basis
    controller._reconcile_authentication_comparisons(plan, phase)
    assert controller.state.list_tasks() == tasks


def test_completed_and_frozen_tasks_are_preserved(matrix_case):
    controller, task, _store, plan, phase, _target, _observed = matrix_case
    done = replace(task, status="done")
    frozen = replace(task, task_uid="frozen", recovery_context={**task.recovery_context,
                                                              "validation_auth_context": {"credential_ids": ["x"]}})
    other_failure = replace(task, task_uid="other-failure", status="partial_failure")
    controller.state.tasks = [done, frozen, other_failure]
    controller._reconcile_authentication_comparisons(plan, phase)
    assert controller.state.list_tasks() == [done, frozen, other_failure]


def test_setup_failure_event_reports_actual_requirement(matrix_case):
    from modules.agents.multi_agent_workflow import AuthenticationSetupResult

    controller, task, _store, _plan, _phase, _target, _observed = matrix_case
    controller._record_authentication_setup(task, AuthenticationSetupResult("unavailable", reason="missing"))
    event = [item for item in controller.runtime.callback_handler.events if item["type"] == "authentication_setup"][-1]
    assert event["comparison"] == "role" and event["required_roles"] == ["user"]
    assert event["credential_count"] == 0


def test_continuation_reconciliation_is_durable_and_reload_safe(matrix_case, monkeypatch):
    from modules.agents import multi_agent_workflow as workflow

    controller, task, store, plan, phase, target, _observed = matrix_case
    store.store_credential("OP_AUTH", {
        "credential_id": "user", "credential_type": "username_password", "target": target,
        "role": "user", "origin": "provided", "status": "unknown", "management_policy": "user",
        "payload": {"username": "one", "password": "secret"},
    })

    def list_tasks(phase=None, status=None):
        return [item for item in store.get_tasks("OP_AUTH")
                if (phase is None or phase == item.phase) and (not status or item.status in status)]

    client = SimpleNamespace(
        list_tasks=list_tasks, patch_task=lambda **kwargs: store.patch_task("OP_AUTH", **kwargs),
        store_task=lambda task: store.store_task("OP_AUTH", task),
    )
    monkeypatch.setattr(workflow, "get_memory_client", lambda **_kwargs: client)
    controller.state = workflow.WorkflowStateStore("OP_AUTH")
    controller._reconcile_authentication_comparisons(plan, phase)
    saved = store.get_tasks("OP_AUTH")
    assert len(saved) == 2
    assert next(item for item in saved if item.task_uid == task.task_uid).status == "superseded"
    replacement = next(item for item in saved if item.replacement_of == task.task_uid)
    assert replacement.recovery_context["authentication_requirement"] == {"comparison": "account", "roles": ["user"]}
    controller.state = workflow.WorkflowStateStore("OP_AUTH")
    controller._reconcile_authentication_comparisons(plan, phase)
    assert store.get_tasks("OP_AUTH") == saved


@pytest.mark.parametrize("target", ["https://target.test", "ssh://target.test", "/repo/project"])
def test_production_task_submitter_expands_and_deduplicates_matrix(matrix_case, monkeypatch, target):
    from modules.tools import memory

    controller, _task, store, plan, phase, _target, _observed = matrix_case
    plan = replace(plan, targets=[OperationTarget("target-1", target, "network")])
    store.store_plan("OP_AUTH", plan)
    store.store_credential("OP_AUTH", {
        "credential_id": "role-user", "credential_type": "username_password", "target": target,
        "role": "user", "origin": "provided", "status": "valid", "management_policy": "user",
        "payload": {"username": "one", "password": "secret"},
    })
    client = SimpleNamespace(get_active_plan=lambda **_kwargs: plan,
                             store_task=lambda task, **_kwargs: store.store_task("OP_AUTH", task))
    monkeypatch.setattr(memory, "_ensure_memory_client", lambda: client)
    monkeypatch.setattr(memory, "_user_id", lambda: "target")
    proposals = [{
        "title": workstream, "objective": f"Perform {workstream} within assigned target scope",
        "methods": ["request", "analyze"], "target_ids": ["target-1"], "task_role": "mapping",
        "workstream": workstream, "criteria": [{"description": f"Record evidence for {workstream}"}],
    } for workstream in ("unauthenticated_baseline", "authenticated_credential_coverage", "authorization_comparison")]
    def transform(tasks, existing):
        return controller._expand_authentication_comparisons(plan, phase, tasks, existing)

    def submit():
        return memory.build_create_tasks_submitter(
            phase_task_contract=controller._phase_task_contract(phase), task_transformer=transform,
        )(proposals)

    assert json.loads(submit())["created_count"] == 3
    generated = [item for item in store.get_tasks("OP_AUTH") if item.task_uid != "auth-task"]
    assert len(generated) == 3
    assert {json.dumps(item.recovery_context["authentication_requirement"], sort_keys=True)
            for item in generated} == {
        json.dumps({"comparison": "none", "roles": []}, sort_keys=True),
        json.dumps({"comparison": "none", "roles": ["user"]}, sort_keys=True),
        json.dumps({"comparison": "account", "roles": ["user"]}, sort_keys=True),
    }
    assert json.loads(submit())["created_count"] == 0


def test_undefined_roles_and_unrelated_targets_do_not_generate_tasks(matrix_case):
    controller, task, _store, plan, phase, target, _observed = matrix_case
    plan = replace(plan, targets=[*plan.targets, OperationTarget("other", "other-target", "network")])
    assert controller._expand_authentication_comparisons(plan, phase, [task], [], {"other-target": {"admin": []}}) == []
    generated = controller._expand_authentication_comparisons(
        plan, phase, [replace(task, target_scope="all", target_ids=[])], [],
        {target: {"user": []}, "other-target": {"admin": []}},
    )
    assert len(generated) == 2
    assert all(item.recovery_context["authentication_requirement"]["comparison"] == "account" for item in generated)


def test_missing_identities_fail_before_prompt_building(matrix_case, monkeypatch):
    from modules.agents import multi_agent_workflow as workflow

    controller, task, store, plan, phase, _target, _observed = matrix_case
    client = SimpleNamespace(
        patch_task=lambda **kwargs: store.patch_task("OP_AUTH", **kwargs),
        list_tasks=lambda **_kwargs: store.get_tasks("OP_AUTH"),
    )
    monkeypatch.setattr(workflow, "get_memory_client", lambda **_kwargs: client)
    controller.state = workflow.WorkflowStateStore("OP_AUTH")
    monkeypatch.setattr(controller, "_authentication_agent_tool_names", lambda *_args: [
        "capture_browser_authenticated_context",
    ])
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: pytest.fail("No identities: no prompt work"))
    controller._run_task_in_trace(plan, phase, task)
    updated = next(item for item in controller.state.list_tasks() if item.task_uid == task.task_uid)
    assert updated.status == "partial_failure"
    assert updated.recovery_context["authentication_setup"]["reason"] == "role_comparison_credentials_unavailable"


def test_current_baseline_reuse_requires_matching_scope_and_method(matrix_case, monkeypatch, tmp_path):
    controller, task, _store, _plan, _phase, _target, _observed = matrix_case
    response = {"method": "GET", "url": "https://target.test/private", "outcome": "response", "status": 403}
    path = tmp_path / "anonymous.json"
    path.write_text(json.dumps({"responses": [None, response]}))
    baseline = replace(task, task_uid="baseline", status="done", evidence=[str(path)], recovery_context={
        "phase_task_contract": {"workstream": "unauthenticated_baseline"},
    })
    controller.state.store_task(baseline)
    fallback = []
    monkeypatch.setattr(controller, "_controller_baseline_request", lambda url, method: fallback.append((url, method))
                        or {**response, "method": method})
    assert controller._current_unauthenticated_response(task, response["url"], "GET") == response
    assert fallback == []
    assert controller._current_unauthenticated_response(task, response["url"], "HEAD")["method"] == "HEAD"
    assert fallback == [(response["url"], "HEAD")]
    for content in ("not JSON", "[]"):
        path.write_text(content)
        controller._current_unauthenticated_response(task, response["url"], "GET")
    path.unlink()
    controller._current_unauthenticated_response(task, response["url"], "GET")
    assert len(fallback) == 4


@pytest.mark.parametrize("anonymous_success", [True, False])
def test_generated_single_role_coverage_records_both_sides(matrix_case, monkeypatch, tmp_path, anonymous_success):
    from modules.agents import multi_agent_workflow as workflow
    from modules.tools.memory import AcceptanceBasis, AcceptanceContract, AcceptanceCriterion, EvidenceRequirement

    controller, task, _store, plan, phase, target, _observed = matrix_case
    task = replace(task, acceptance=AcceptanceContract(
        mode="coverage", basis=AcceptanceBasis(kind="snapshot", description="Frozen endpoint",
                                              source_refs=["artifact:observed.html"], snapshot_hash="frozen",
                                              item_ids=["endpoint"]),
        criteria=[AcceptanceCriterion(id="compare", description="Compare both responses",
                                      evidence_requirements=[EvidenceRequirement(kind="artifact")])],
    ), recovery_context={
        "phase_task_contract": {"module": "web", "phase_id": 3, "workstream": "authenticated_credential_coverage",
                                "task_role": "mapping", "execution_owner": "controller"},
    })
    task = controller._expand_authentication_comparisons(plan, phase, [task], [], {target: {"user": []}})[0]
    controller.state.store_task(task)
    monkeypatch.setattr(controller, "_run_authentication_agent", lambda *_args: workflow.AuthenticationSetupResult(
        "reused", ("credential",),
    ))
    monkeypatch.setattr(workflow, "_load_inventory_manifest", lambda *_args: ({"items": [{
        "id": "endpoint", "kind": "endpoint", "target_id": "target", "value": target + "/private",
        "attributes": {"interaction": {"operations": ["GET"]}},
    }]}, "frozen"))
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: {
        "outcome": "response" if anonymous_success else "error", "status": 403, "body": "anonymous", "method": "GET",
    })
    monkeypatch.setattr(workflow, "authenticated_http_request", lambda *_args, **_kwargs: json.dumps({
        "status_code": 200, "body": "authenticated",
    }))
    assert controller._run_controller_web_authenticated_coverage(plan, phase, task)
    path = tmp_path / "artifacts" / "controller_authenticated_coverage" / f"{task.task_uid}.json"
    payload = json.loads(path.read_text())
    assert bool(payload["unauthenticated_responses"]) is anonymous_success
    assert bool(payload["comparisons"]) is anonymous_success
    assert bool(payload["coverage_gaps"]) is not anonymous_success
    if anonymous_success:
        assert payload["comparisons"][0]["body_equal"] is False
