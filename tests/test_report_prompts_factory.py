import re
from pathlib import Path
from unittest.mock import patch

from modules.prompts.factory import (
    format_evidence_for_report,
    generate_findings_summary_table,
    get_report_appendix_system_prompt,
    get_report_critic_system_prompt,
    get_report_executive_system_prompt,
    get_report_finding_system_prompt,
    get_report_next_steps_system_prompt,
    get_report_observation_system_prompt,
)


PROMPT_TEMPLATE_DIR = Path(__file__).parents[1] / "src" / "modules" / "prompts" / "templates"
OPERATION_PLUGIN_DIR = Path(__file__).parents[1] / "src" / "modules" / "operation_plugins"


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
            "### Claim Status",
            "#### Verified Risk",
            "#### Findings Requiring Validation",
            "#### Informational Observations",
            "#### Coverage Status",
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


def test_module_report_prompts_preserve_python_owned_facts():
    """Module guidance may specialize prose, but must not reclaim report facts from Python."""
    report_prompts = sorted(OPERATION_PLUGIN_DIR.glob("*/report_prompt.md"))
    agent_prompts = sorted(OPERATION_PLUGIN_DIR.glob("*/report_agent_*prompt.md"))

    assert report_prompts
    assert agent_prompts
    for prompt_path in report_prompts:
        prompt = prompt_path.read_text(encoding="utf-8")
        assert "<deterministic_reporting_boundary>" in prompt, prompt_path
        assert "Python owns" in prompt, prompt_path
        assert "artifact references" in prompt, prompt_path
        assert "metrics" in prompt, prompt_path

    for prompt_path in agent_prompts:
        prompt = prompt_path.read_text(encoding="utf-8")
        assert "Python" in prompt, prompt_path


def test_module_executive_prompts_define_informational_observation_context():
    executive_prompts = sorted(
        list(OPERATION_PLUGIN_DIR.glob("*/report_agent_executive_system_prompt.md"))
        + list(OPERATION_PLUGIN_DIR.glob("*/report_agent_finding_executive_prompt.md"))
    )

    assert executive_prompts
    for prompt_path in executive_prompts:
        prompt = prompt_path.read_text(encoding="utf-8")
        assert "informational" in prompt.lower(), prompt_path
        assert "do not" in prompt.lower(), prompt_path
        assert "severity" in prompt.lower(), prompt_path


def test_appendix_prompt_defines_unified_reportable_tools():
    prompt = _read_report_prompt("report_agent_appendix_system_prompt.md")

    assert "reportable operational-tool list" in prompt


def test_high_risk_module_overrides_do_not_reclaim_canonical_report_data():
    ctf_prompt = (OPERATION_PLUGIN_DIR / "ctf" / "report_agent_finding_executive_prompt.md").read_text(
        encoding="utf-8"
    )
    recon_prompt = (OPERATION_PLUGIN_DIR / "web_recon" / "report_agent_executive_system_prompt.md").read_text(
        encoding="utf-8"
    )
    context_prompt = (OPERATION_PLUGIN_DIR / "context_navigator" / "report_agent_observation_system_prompt.md").read_text(
        encoding="utf-8"
    )
    threat_prompt = (OPERATION_PLUGIN_DIR / "threat_emulation" / "report_agent_executive_system_prompt.md").read_text(
        encoding="utf-8"
    )

    assert "without declaring CAPTURED/NOT CAPTURED" in ctf_prompt
    assert "without listing or clustering" in recon_prompt
    assert "without assigning completion markers" in context_prompt
    assert "do not create ATT&CK mappings" in threat_prompt


def test_findings_summary_uses_canonical_metadata_location_without_confidence():
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
    assert "Verified" in table
    assert "Confidence" not in table
    assert "92.0%" not in table


def test_findings_summary_omits_confidence_when_missing():
    table = generate_findings_summary_table([
        {
            "category": "finding",
            "severity": "HIGH",
            "content": "Verified claim",
            "validation_status": "verified",
        }
    ])

    assert "| Verified | N/A |" not in table
    assert "Confidence" not in table


def test_findings_summary_preserves_full_canonical_title():
    title = "User Enumeration and Lack of Rate Limiting on /vulnerabilities/brute"
    table = generate_findings_summary_table([
        {
            "category": "finding",
            "severity": "MEDIUM",
            "parsed": {"vulnerability": title, "where": "/vulnerabilities/brute"},
            "validation_status": "verified",
        }
    ])

    assert title in table


def test_finding_report_prompt_and_evidence_omit_confidence():
    prompt = get_report_finding_system_prompt()
    evidence = format_evidence_for_report(
        [
            {
                "category": "finding",
                "severity": "HIGH",
                "confidence": "95",
                "validation_status": "verified",
                "parsed": {
                    "vulnerability": "Reflected XSS",
                    "where": "/search",
                    "evidence": "artifact:artifacts/proof.txt",
                    "confidence": "95",
                },
            }
        ]
    )

    assert "Confidence" not in prompt
    assert "Confidence" not in evidence
    assert "**Severity:** HIGH" in evidence

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
