from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
from modules.tools.tool_catalog import get_shell_command_execution_capabilities
from unittest.mock import MagicMock

def test_canonical_execution_method_alignment():
    # Existing behavior preserved
    assert MultiAgentWorkflowController._canonical_execution_method("inspect") == "analyze"
    assert MultiAgentWorkflowController._canonical_execution_method("analysis") == "analyze"
    
    # New alignment for web/http
    assert MultiAgentWorkflowController._canonical_execution_method("web_inspect") == "request"
    assert MultiAgentWorkflowController._canonical_execution_method("http_inspect") == "request"
    assert MultiAgentWorkflowController._canonical_execution_method("web_recon") == "request"
    
    # Tokens intersection in _canonical_execution_method
    assert MultiAgentWorkflowController._canonical_execution_method("web_security_inspect") == "request"
    assert MultiAgentWorkflowController._canonical_execution_method("analyze_http_headers") == "request"
    
    # Regular analyze tokens
    assert MultiAgentWorkflowController._canonical_execution_method("source_review") == "analyze"

def test_tool_execution_capabilities_updated():
    # Verify environment.yaml changes are loaded
    curl_caps = get_shell_command_execution_capabilities("curl")
    assert "request" in curl_caps
    assert "analyze" in curl_caps
    
    whatweb_caps = get_shell_command_execution_capabilities("whatweb")
    assert "request" in whatweb_caps
    assert "analyze" in whatweb_caps
    
    httpx_caps = get_shell_command_execution_capabilities("httpx")
    assert "request" in httpx_caps
    assert "analyze" in httpx_caps
    
    katana_caps = get_shell_command_execution_capabilities("katana")
    assert "crawl" in katana_caps
    assert "analyze" in katana_caps

def test_task_creator_contract_includes_guidance():
    controller = MagicMock()
    controller.runtime.config.objective = "test"
    phase = MagicMock()
    phase.id = "phase-1"
    phase.criteria = "test"
    phase.requires_finding_candidates = False
    
    # Use the real method implementation
    controller._task_creator_contract = MultiAgentWorkflowController._task_creator_contract.__get__(controller, MultiAgentWorkflowController)
    
    # Mock dependencies
    controller._task_creator_correction_count.return_value = 1
    controller._phase_task_contract_prompt.return_value = ""
    controller._task_creator_finding_context.return_value = ""
    controller._eligible_snapshot_handles.return_value = ""
    controller._memory_prompt_guidance.return_value = ""
    controller._memory_summary.return_value = ""
    controller.state.list_finding_records.return_value = []
    
    # We need to mock inventory_manifest_contract_text which is a global function
    import modules.agents.multi_agent_workflow as maw
    old_func = maw.inventory_manifest_contract_text
    maw.inventory_manifest_contract_text = MagicMock(return_value="inventory manifest contract")
    try:
        contract = controller._task_creator_contract(MagicMock(), phase)
    finally:
        maw.inventory_manifest_contract_text = old_func
    
    assert "specify \"request\" or \"http_request\" in `methods`" in contract
    assert "rather than" in contract
    assert "\"inspect\" or \"recon\"" in contract
