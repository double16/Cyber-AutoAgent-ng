import re
from pathlib import Path
from unittest.mock import patch

from modules.prompts.factory import (
    generate_findings_summary_table,
    get_report_appendix_system_prompt,
    get_report_critic_system_prompt,
    get_report_executive_system_prompt,
    get_report_finding_system_prompt,
    get_report_next_steps_system_prompt,
    get_report_observation_system_prompt,
)


PROMPT_TEMPLATE_DIR = Path(__file__).parents[1] / "src" / "modules" / "prompts" / "templates"


def _read_report_prompt(name):
    return (PROMPT_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _assert_in_order(text, values):
    cursor = -1
    for value in values:
        cursor = text.index(value, cursor + 1)


def test_free_form_report_prompts_use_fact_free_canonical_markdown_layouts():
    expected_headings = {
        "report_agent_executive_system_prompt.md": [
            "## EXECUTIVE SUMMARY",
            "### Assessment Context",
            "### Risk Assessment",
            "### Attack Path Analysis",
            "### Key Findings",
        ],
        "report_agent_finding_system_prompt.md": [
            "### {{TITLE_FROM_FINDING_DATA}}",
            "#### Evidence",
            "#### MITRE ATT&CK Mapping",
            "#### CWE Mapping",
            "#### Impact",
            "#### Remediation",
            "#### Steps to Reproduce",
            "#### Attack Path Analysis",
            "#### STEPS",
            "#### TECHNICAL APPENDIX",
        ],
        "report_agent_observation_system_prompt.md": [
            "### {{TITLE_FROM_OBSERVATION_DATA}}",
            "#### Evidence",
            "#### Analysis",
            "#### Steps to Reproduce",
        ],
        "report_agent_appendix_system_prompt.md": [
            "### Assessment Methodology",
            "### Tools Utilized",
            "### Execution Metrics",
            "### Operation Plan",
            "### Operation Tasks",
            "### Methodology Limitations",
        ],
    }

    for template_name, headings in expected_headings.items():
        prompt = _read_report_prompt(template_name)
        assert '<canonical_markdown_layout format_only="true">' in prompt
        assert "never as operation data" in prompt or "never as finding data" in prompt or "never as observation data" in prompt
        assert "Never copy a placeholder" in prompt
        _assert_in_order(prompt, headings)
        assert not re.search(r"\bCVE-\d+\b|\bCWE-\d+\b", prompt, re.IGNORECASE)
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", prompt)


def test_report_critic_rejects_placeholder_leakage_and_layout_drift():
    prompt = _read_report_prompt("report_agent_critic_system_prompt.md")

    assert "Reject unresolved `{{PLACEHOLDER}}` text" in prompt
    assert "missing required headings" in prompt
    assert "incorrect heading order" in prompt
    assert "explicit module-specific override" in prompt


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


@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_critic_system_prompt(mock_load):
    mock_load.return_value = None
    assert "report-section drafts" in get_report_critic_system_prompt()

    mock_load.return_value = "Critic Prompt Template"
    assert get_report_critic_system_prompt() == "Critic Prompt Template"
    mock_load.assert_called_with("report_agent_critic_system_prompt.md")


@patch("modules.prompts.factory.load_prompt_template")
def test_get_report_next_steps_system_prompt(mock_load):
    mock_load.return_value = None
    assert "recommended next steps" in get_report_next_steps_system_prompt()

    mock_load.return_value = "Next Steps Prompt Template"
    assert get_report_next_steps_system_prompt() == "Next Steps Prompt Template"
    mock_load.assert_called_with("report_agent_next_steps_system_prompt.md")
