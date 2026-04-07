import pytest
import os
import json
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime
from modules.handlers.report_generator import generate_security_report, _extract_text_from_result, build_report_sections
from modules.tools.memory import clear_memory_client


def test_extract_text_from_result():
    # Test normal extraction
    mock_result = MagicMock()
    mock_result.message = {
        "content": [
            {"text": "  # Heading 1\n"},
            {"text": "\t## Heading 2\n"},
            {"text": "Some normal text\n"},
            {"text": "    ### Heading 3 with spaces\n"}
        ]
    }
    
    extracted = _extract_text_from_result(mock_result)
    assert "# Heading 1\n" in extracted
    assert "## Heading 2\n" in extracted
    assert "### Heading 3 with spaces\n" in extracted
    assert "Some normal text\n" in extracted
    
    # Verify no leading spaces before headings
    lines = extracted.splitlines()
    assert lines[0] == "# Heading 1"
    assert lines[1] == "## Heading 2"
    assert lines[2] == "Some normal text"
    assert lines[3] == "### Heading 3 with spaces"

def test_extract_text_from_result_empty():
    assert _extract_text_from_result(None) == ""
    
    mock_result = MagicMock()
    mock_result.message = {}
    assert _extract_text_from_result(mock_result) == ""


@pytest.fixture(autouse=True)
def memory_client_clear():
    clear_memory_client()


@patch("modules.handlers.report_generator.get_memory_client")
@pytest.mark.skip(reason="Not sure we want to downgrade findings in this way")
def test_report_builder_downgrade_logic(mock_get_client, tmp_path):
    op_id = "OP_DOWNGRADE_TEST"
    output_dir = tmp_path / "outputs"
    os.environ["CYBER_AGENT_OUTPUT_DIR"] = str(output_dir)

    # Mock list_memories to return findings with various validation statuses
    mock_client = mock_get_client.return_value
    mock_client.list_memories.return_value = [
        {
            "id": "1",
            "memory": "[VULNERABILITY] Verified with Proof [WHERE] /a [EVIDENCE] proof exists",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "CRITICAL",
                "validation_status": "verified",
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]}
            },
        },
        {
            "id": "2",
            "memory": "[VULNERABILITY] Unverified but HAS Proof [WHERE] /c",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "MEDIUM",
                "validation_status": "unverified",
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]}
            },
        },
        {
            "id": "3",
            "memory": "[VULNERABILITY] Hypothesis [WHERE] /d",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "LOW",
                "validation_status": "hypothesis"
            },
        },
    ]

    # Create the proof file
    proof_file = tmp_path / "proof.txt"
    proof_file.write_text("proof")

    # Run build_report_sections
    sections = build_report_sections(op_id, "example.com", "Test Objective")

    evidence = sections.get("raw_evidence", [])

    # Check item 1: Should remain a finding
    item1 = next(e for e in evidence if e["id"] == "1")
    assert item1["category"] == "finding", "Item 1 should remain a finding"

    # Check item 2: Should be downgraded to observation (unverified)
    item3 = next(e for e in evidence if e["id"] == "2")
    assert item3["category"] == "observation", "Item 2 should be downgraded to observation (unverified)"

    # Check item 3: Should be downgraded to observation (hypothesis)
    item4 = next(e for e in evidence if e["id"] == "3")
    assert item4["category"] == "observation", "Item 3 should be downgraded to observation (hypothesis)"

@patch("modules.handlers.report_generator.ReportGenerator")
@patch("modules.handlers.report_generator.get_output_path")
@patch("modules.handlers.report_generator.build_report_sections")
@patch("modules.handlers.report_generator.get_config_manager")
def test_generate_security_report_success(mock_get_config, mock_build_sections, mock_get_output_path, mock_report_gen, tmp_path):
    target = "example.com"
    objective = "Test Objective"
    operation_id = "OP123"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    mock_get_output_path.return_value = str(output_dir)
    
    mock_config = MagicMock()
    mock_config.get_provider.return_value = "test_provider"
    mock_config.get_llm_config.return_value.model_id = "test_model"
    mock_config.get_swarm_config.return_value.llm.model_id = "test_swarm_model"
    mock_get_config.return_value = mock_config

    mock_build_sections.return_value = {
        "evidence_count": 1,
        "steps_executed": 5,
        "overview": "Overview content",
        "findings_table": "Findings table",
        "risk_assessment": "Risk assessment",
        "severity_counts": {"HIGH": 1},
        "summary_table": "Summary table",
        "raw_evidence": [
            {
                "id": "f1",
                "title": "High Finding",
                "severity": "HIGH",
                "category": "finding",
                "content": "Finding content"
            }
        ],
        "operation_plan": {},
        "operation_tasks": [],
        "tools_summary": ""
    }

    # Mock Agent and its response
    mock_agent = MagicMock()
    mock_report_gen.create_report_agent.return_value = mock_agent
    
    mock_result = MagicMock()
    mock_result.message = {"content": [{"text": "## Section Content\n"}]}
    mock_agent.return_value = mock_result

    report_file = tmp_path / "final_report.md"
    
    generate_security_report(
        target=target,
        objective=objective,
        operation_id=operation_id,
        config_params={"steps_executed": 5, "tools_used": ["nmap"]},
        filename=str(report_file)
    )

    # Verify report file exists and contains expected sections
    assert report_file.exists()
    content = report_file.read_text()
    assert "# SECURITY ASSESSMENT REPORT" in content
    assert "## TABLE OF CONTENTS" in content
    assert "## Section Content" in content
    assert f"Operation ID: {operation_id}" in content

    # Verify that intermediate files were created
    assert (output_dir / "security_assessment_report.json").exists()
    assert (output_dir / "report_executive_summary.md").exists()
    assert (output_dir / "report_findings_header.md").exists()
    # finding_1_High_Finding.md
    assert (output_dir / "finding_1_High_Finding.md").exists()
    assert (output_dir / "report_methodology.md").exists()

@patch("modules.handlers.report_generator.build_report_sections")
def test_generate_security_report_no_evidence(mock_build_sections, tmp_path):
    mock_build_sections.return_value = {"evidence_count": 0}
    
    report_file = tmp_path / "no_report.md"
    
    generate_security_report(
        target="example.com",
        objective="Test",
        operation_id="OP123",
        config_params={},
        filename=str(report_file)
    )
    
    assert not report_file.exists()

@patch("modules.handlers.report_generator.ReportGenerator")
@patch("modules.handlers.report_generator.get_output_path")
@patch("modules.handlers.report_generator.build_report_sections")
@patch("modules.handlers.report_generator.get_config_manager")
def test_generate_security_report_observations(mock_get_config, mock_build_sections, mock_get_output_path, mock_report_gen, tmp_path):
    target = "example.com"
    objective = "Test Objective"
    operation_id = "OP456"
    output_dir = tmp_path / "output_obs"
    output_dir.mkdir()
    mock_get_output_path.return_value = str(output_dir)
    
    mock_config = MagicMock()
    mock_config.get_provider.return_value = "test_provider"
    mock_config.get_llm_config.return_value.model_id = "test_model"
    mock_config.get_swarm_config.return_value.llm.model_id = "test_swarm_model"
    mock_get_config.return_value = mock_config

    mock_build_sections.return_value = {
        "evidence_count": 1,
        "steps_executed": 1,
        "overview": "Overview",
        "findings_table": "",
        "risk_assessment": "",
        "severity_counts": {},
        "summary_table": "",
        "raw_evidence": [
            {
                "id": "o1",
                "title": "Some Observation",
                "severity": "INFO",
                "category": "observation",
                "content": "Observation content"
            }
        ],
        "operation_plan": {},
        "operation_tasks": [],
        "tools_summary": ""
    }

    mock_agent = MagicMock()
    mock_report_gen.create_report_agent.return_value = mock_agent
    mock_agent.return_value.message = {"content": [{"text": "Observation detail"}]}

    report_file = tmp_path / "obs_report.md"
    
    generate_security_report(
        target=target,
        objective=objective,
        operation_id=operation_id,
        config_params={},
        filename=str(report_file)
    )

    assert report_file.exists()
    content = report_file.read_text()
    assert "OBSERVATIONS AND DISCOVERIES" in content
    assert "Observation detail" in content
    assert (output_dir / "report_observations_header.md").exists()
    assert (output_dir / "observation_1_Some_Observation.md").exists()

if __name__ == "__main__":
    pytest.main([__file__])
