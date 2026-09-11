from unittest.mock import MagicMock

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController


def test_task_creator_repair_prompt_targeted_fixes():
    # Mocking self.state and other dependencies
    workflow = MagicMock(spec=MultiAgentWorkflowController)
    # We want to test the static or instance method _task_creator_repair_prompt
    # Since it's an instance method, we need a real instance or a mock that can call it.
    
    # Actually, it's easier to just call the method from the class if it doesn't use much of self.
    # But it does use self._task_creator_correction_count() indirectly in _task_creator_contract,
    # though _task_creator_repair_prompt itself doesn't call it.
    
    workflow._task_creator_repair_prompt = MultiAgentWorkflowController._task_creator_repair_prompt.__get__(workflow)
    
    # Test limits fix
    reason = "tasks.0.limits Input should be a valid dictionary"
    prompt = workflow._task_creator_repair_prompt(failure_reason=reason)
    assert "- FIX: Use an object for `limits`" in prompt
    
    # Test criteria fix
    reason = "tasks.0.criteria.0 Input should be a valid dictionary"
    prompt = workflow._task_creator_repair_prompt(failure_reason=reason)
    assert "- FIX: Each criterion in the list must be an object" in prompt


def test_task_creator_repair_prompt_distinguishes_scope_and_http_route_errors():
    workflow = MagicMock(spec=MultiAgentWorkflowController)
    workflow._task_creator_repair_prompt = MultiAgentWorkflowController._task_creator_repair_prompt.__get__(workflow)

    route_prompt = workflow._task_creator_repair_prompt(
        failure_reason="task_proposal:multi_route_http: proposal[1] contains multiple distinct endpoint routes: /, /login"
    )
    assert "HTTP-only route-splitting error" in route_prompt
    assert "Preserve its methods" in route_prompt
    assert "one proposal per endpoint route" in route_prompt

    scope_prompt = workflow._task_creator_repair_prompt(
        failure_reason="procedure proposal cannot consume an inventory-wide moving collection; use canonical snapshot_refs"
    )
    assert "generic moving-scope error" in scope_prompt
    assert "existing canonical snapshot_ref" in scope_prompt
    assert "HTTP-only route-splitting error" not in scope_prompt

    boundary_prompt = workflow._task_creator_repair_prompt(
        failure_reason="explicit service targets permit only their registered port"
    )
    assert "exact registered service boundary" in boundary_prompt

    finding_prompt = workflow._task_creator_repair_prompt(
        failure_reason="finding-dependent task proposal includes unavailable finding_refs"
    )
    assert "canonical finding_refs" in finding_prompt
    
    # Test extra fields fix
    reason = "tasks.0.name Extra inputs are not permitted [type=extra_forbidden]"
    prompt = workflow._task_creator_repair_prompt(failure_reason=reason)
    assert "- FIX: Remove extra fields like `name` or `work_type`" in prompt
    
    # Test multiple fixes
    reason = "limits dict error and criteria list error"
    prompt = workflow._task_creator_repair_prompt(failure_reason=reason)
    assert "- FIX: Use an object for `limits`" in prompt
    assert "- FIX: Each criterion in the list must be an object" in prompt

def test_task_creator_contract_structure():
    workflow = MagicMock(spec=MultiAgentWorkflowController)
    workflow._task_creator_correction_count.return_value = 3
    workflow.state = MagicMock()
    workflow.state.list_finding_records.return_value = []
    
    plan = MagicMock()
    plan.targets = []
    phase = MagicMock()
    phase.id = 1
    
    # Mocking internal prompt methods
    workflow._phase_task_contract_prompt.return_value = "Phase contract context"
    workflow._task_creator_contract = MultiAgentWorkflowController._task_creator_contract.__get__(workflow)
    
    contract = workflow._task_creator_contract(plan, phase)
    assert "Every proposal MUST follow this exact structure:" in contract
    assert "1. `limits` MUST be a dictionary/object" in contract
    assert "2. `criteria` MUST be a list containing exactly one object" in contract
