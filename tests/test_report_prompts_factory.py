from unittest.mock import patch
from modules.prompts.factory import (
    generate_findings_summary_table,
    get_report_executive_system_prompt,
    get_report_finding_system_prompt,
    get_report_observation_system_prompt,
    get_report_appendix_system_prompt
)


def test_findings_summary_uses_canonical_metadata_location_and_confidence():
    table = generate_findings_summary_table([
        {
            "category": "finding",
            "severity": "HIGH",
            "content": "Reflected XSS",
            "validation_status": "verified",
            "metadata": {"target": "/vulnerabilities/xss_r/", "confidence": "92"},
        }
    ])

    assert "/vulnerabilities/xss_r/" in table
    assert "92.0%" in table
    assert "Verified" in table


def test_findings_summary_marks_genuinely_missing_confidence_as_not_available():
    table = generate_findings_summary_table([
        {
            "category": "finding",
            "severity": "HIGH",
            "content": "Verified claim",
            "validation_status": "verified",
        }
    ])

    assert "| - | Verified | N/A |" in table

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_executive_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "executive security reporting specialist" in get_report_executive_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Executive Prompt Template"
    assert get_report_executive_system_prompt() == "Executive Prompt Template"
    mock_load.assert_called_with("report_agent_executive_system_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_finding_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical security writer" in get_report_finding_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Finding Prompt Template"
    assert get_report_finding_system_prompt() == "Finding Prompt Template"
    mock_load.assert_called_with("report_agent_finding_system_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_observation_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical security writer" in get_report_observation_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Observation Prompt Template"
    assert get_report_observation_system_prompt() == "Observation Prompt Template"
    mock_load.assert_called_with("report_agent_observation_system_prompt.md")

@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_appendix_system_prompt(mock_load):
    # Test fallback
    mock_load.return_value = None
    assert "technical documentation specialist" in get_report_appendix_system_prompt()
    
    # Test template loading
    mock_load.return_value = "Appendix Prompt Template"
    assert get_report_appendix_system_prompt() == "Appendix Prompt Template"
    mock_load.assert_called_with("report_agent_appendix_system_prompt.md")
