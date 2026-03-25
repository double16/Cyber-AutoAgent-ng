import pytest
from unittest.mock import patch
from modules.prompts.factory import (
    get_report_executive_system_prompt,
    get_report_finding_system_prompt,
    get_report_observation_system_prompt,
    get_report_appendix_system_prompt
)

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_executive_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "executive security reporting specialist" in get_report_executive_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Executive Prompt Template"
    assert get_report_executive_system_prompt() == "Executive Prompt Template"
    mock_load.assert_called_with("report_agent_system_executive_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_finding_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical security writer" in get_report_finding_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Finding Prompt Template"
    assert get_report_finding_system_prompt() == "Finding Prompt Template"
    mock_load.assert_called_with("report_agent_system_finding_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_observation_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical security writer" in get_report_observation_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Observation Prompt Template"
    assert get_report_observation_system_prompt() == "Observation Prompt Template"
    mock_load.assert_called_with("report_agent_system_observation_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_appendix_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical documentation specialist" in get_report_appendix_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Appendix Prompt Template"
    assert get_report_appendix_system_prompt() == "Appendix Prompt Template"
    mock_load.assert_called_with("report_agent_system_appendix_prompt.md")
