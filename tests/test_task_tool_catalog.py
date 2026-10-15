"""Runtime task tool membership is shared by prompts, normalization, and execution."""

import json

import pytest

from modules.agents import multi_agent_workflow as workflow
from modules.config.types import BudgetConfig
from modules.handlers.utils import get_tool_name
from tests.test_multi_agent_workflow import FakeState, Task, _plan, _runtime, _tool


@pytest.fixture
def catalog_case():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 3})
    runtime.core_tools_list = [_tool("http_request"), _tool("shell")]
    runtime.tools_list = runtime.core_tools_list
    runtime.optional_tools_list = [_tool("authenticated_http_request"), _tool("capture_browser_authenticated_context"),
                                   _tool("runtime_probe"), _tool("checkout_credential")]
    task = Task(task_uid="comparison", title="Compare identities", objective="Compare access", phase=1, status="active",
                recovery_context={"phase_task_contract": {"workstream": "authenticated_credential_coverage"}})
    plan = _plan()
    controller = workflow.MultiAgentWorkflowController(
        runtime=runtime, budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(plan, [task]),
    )
    return controller, plan, task


def test_dynamic_refresh_and_required_auth_tools_are_supplied_before_review(catalog_case):
    controller, plan, task = catalog_case
    normalized = controller._normalize_task_prompt_spec({
        "prompt": "Compare the selected identities", "tools": ["http_request", "authenticated_http_request",
        "refresh_authenticated_context", "runtime_probe"], "shell_commands": [],
    }, task)
    assert normalized["tools"] == ["runtime_probe"]
    inventory = controller._task_tool_inventory(plan, task)
    supplied = {get_tool_name(tool) for group in ("core_tools", "controller_task_tools") for tool in inventory[group]}
    assert {"http_request", "authenticated_http_request", "refresh_authenticated_context"} <= supplied
    optional = {get_tool_name(tool) for tool in inventory["optional_tools"]}
    assert "runtime_probe" in optional and not supplied & optional
    for prompt in (
        controller._task_prompt_builder_prompt(plan, plan.phases[0], task),
        controller._task_prompt_critic_prompt(plan, plan.phases[0], task, normalized),
        controller._task_prompt_revision_prompt(plan, plan.phases[0], task, normalized, []),
    ):
        assert "Automatically supplied task tools" in prompt and "refresh_authenticated_context" in prompt
        assert "Selectable optional tools" in prompt


def test_builder_core_selections_do_not_trigger_extra_critic_cycles(catalog_case):
    controller, plan, task = catalog_case
    calls = []

    def model(role, prompt, _tools, _system):
        calls.append(role)
        if role == "task_prompt_builder":
            return json.dumps({"prompt": "Compare the assigned resource using authenticated_http_request",
                               "tools": ["http_request", "authenticated_http_request", "refresh_authenticated_context",
                                         "runtime_probe"], "shell_commands": []})
        assert role == "task_prompt_critic"
        assert "Python has validated tool membership" in prompt
        assert '"tools":' not in prompt.split("## Proposed task prompt draft")[1]
        return '{"issues":[]}'

    controller.text_runner = model
    spec = controller._build_task_prompt(plan, plan.phases[0], task)
    assert calls == ["task_prompt_builder", "task_prompt_critic"]
    assert spec["tools"] == ["runtime_probe"]
    actual_names = sorted(
        get_tool_name(tool) for tool in controller._resolve_task_executor_base_tools(plan, task, spec)
    )
    assert spec["_reviewed_tool_names"] == actual_names


@pytest.mark.parametrize("core,optional", [("http_request", "http_inspector"), ("network_connect", "network_probe"),
                                          ("read_file", "source_analyzer")])
def test_tool_inventory_handles_http_network_and_source_tools(core, optional):
    runtime = _runtime()
    runtime.core_tools_list = [_tool(core)]
    runtime.tools_list = runtime.core_tools_list
    runtime.optional_tools_list = [_tool(optional)]
    plan = _plan()
    task = Task(task_uid="protocol", title="Inspect assigned subject", objective="Inspect", phase=1, status="active")
    controller = workflow.MultiAgentWorkflowController(runtime=runtime, budget=BudgetConfig(max_duration_minutes=60),
                                                       state_store=FakeState(plan, [task]))
    result = controller._normalize_task_prompt_spec(
        {"prompt": "Inspect assigned subject", "tools": [core, optional]}, task,
    )
    assert result["tools"] == [optional]
    tools = controller._resolve_task_executor_base_tools(plan, task, result)
    assert {core, optional} <= {get_tool_name(tool) for tool in tools}


@pytest.mark.parametrize("selection", ["unknown_tool", "refresh_authenticated_context", "checkout_credential"])
def test_normalization_rejects_unavailable_or_unauthorized_tools(selection):
    runtime = _runtime()
    runtime.optional_tools_list = [_tool("checkout_credential")]
    plan = _plan()
    task = Task(task_uid="ordinary", title="Inspect", objective="Inspect", phase=1, status="active")
    controller = workflow.MultiAgentWorkflowController(runtime=runtime, budget=BudgetConfig(max_duration_minutes=60),
                                                       state_store=FakeState(plan, [task]))
    with pytest.raises(workflow.TaskPromptBuildError, match="unknown or unavailable"):
        controller._normalize_task_prompt_spec({"prompt": "Inspect", "tools": [selection]}, task)


@pytest.mark.parametrize("category,names,approved", [
    ("tool_availability", ["authenticated_http_request", "refresh_authenticated_context"], True),
    ("tool_availability", ["unavailable_tool"], False),
    ("scope", [], False),
    ("objective", ["authenticated_http_request"], False),
    ("evidence", [], False),
    ("execution_policy", [], False),
])
def test_critic_availability_is_reconciled_without_discarding_semantic_issues(catalog_case, category, names, approved):
    controller, plan, task = catalog_case
    data = {"issues": [{"category": category, "tool_names": names, "message": "Review concern"}]}
    controller._validate_task_prompt_critique(data)
    resolved = controller._resolve_task_prompt_critique(plan, task, {"tools": []}, data)
    assert resolved["approved"] is approved
    assert resolved["feedback"] == ([] if approved else ["Review concern"])


def test_critic_rejects_availability_issue_without_named_tools(catalog_case):
    controller, _plan, _task = catalog_case
    with pytest.raises(ValueError, match="require tool_names"):
        controller._validate_task_prompt_critique({"issues": [{"category": "tool_availability", "message": "missing"}]})


def test_terminal_tools_are_available_to_critic_and_scope_categories_are_authoritative(catalog_case):
    controller, plan, task = catalog_case
    data = {"issues": [{"category": "tool_availability", "tool_names": ["record_task_acceptance", "store_finding"],
                        "message": "Terminal tools are absent from the draft selections"}]}
    resolved = controller._resolve_task_prompt_critique(plan, task, {"tools": []}, data)
    assert resolved["approved"] and resolved["issues"] == []
    for category, message, expected in [("scope", "Use the assigned service", True),
                                        ("evidence", "Clarify the scope of response evidence", False)]:
        resolved = controller._resolve_task_prompt_critique(plan, task, {"tools": []}, {"issues": [
            {"category": category, "message": message},
        ]})
        assert controller._task_prompt_critique_is_hard_scope_violation(resolved) is expected
