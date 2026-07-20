import json
from unittest.mock import MagicMock, patch

import pytest

from modules.handlers.report_generator import (
    _extract_text_from_result,
    _format_target_coverage,
    _ground_report_item,
    _has_artifact_reference,
    _normalize_report_category,
    build_report_sections,
    generate_security_report,
)
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, Task, clear_memory_client


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


def test_ground_report_item_rejects_invented_artifact_paths():
    item = {
        "title": "Verified issue",
        "content": "Supported claim",
        "severity": "HIGH",
        "metadata": {"artifacts": ["/outputs/op/real.txt"]},
    }

    grounded = _ground_report_item("### Issue\nEvidence: /transcripts/invented.txt", item)

    assert "/transcripts/invented.txt" not in grounded
    assert "/outputs/op/real.txt" in grounded


def test_extract_text_from_result_markdown_table():
    # Test that a table without a preceding empty line gets one
    mock_result = MagicMock()
    mock_result.message = {
        "content": [
            {"text": "Text\n| T |\n| --- |\n| C |"}
        ]
    }
    extracted = _extract_text_from_result(mock_result)
    assert extracted == "Text\n\n| T |\n| --- |\n| C |"

    # Test that a table WITH a preceding empty line is NOT modified further
    mock_result.message = {
        "content": [
            {"text": "Text\n\n| T |\n| --- |\n| C |"}
        ]
    }
    extracted = _extract_text_from_result(mock_result)
    assert extracted == "Text\n\n| T |\n| --- |\n| C |"

    # Test multiple tables
    mock_result.message = {
        "content": [
            {"text": "T1\n| T1 |\n| --- |\nT2\n| T2 |\n| --- |"}
        ]
    }
    extracted = _extract_text_from_result(mock_result)
    assert extracted == "T1\n\n| T1 |\n| --- |\nT2\n\n| T2 |\n| --- |"

def test_extract_text_from_result_empty():
    assert _extract_text_from_result(None) == ""
    
    mock_result = MagicMock()
    mock_result.message = {}
    assert _extract_text_from_result(mock_result) == ""


def test_report_category_helpers_cover_structured_and_free_form_artifacts():
    assert _has_artifact_reference({"artifacts": ["/tmp/control.txt"]}) is True
    assert _has_artifact_reference({"evidence": ["saved at artifacts/control.txt"]}) is True
    assert _has_artifact_reference(["", "no path", None]) is False
    assert _has_artifact_reference(7) is False

    assert _normalize_report_category("signal", {}, "", {}) == "observation"
    assert _normalize_report_category("plan", {}, "", {}) == "plan"
    assert _normalize_report_category(
        "finding",
        {"validation_status": "verified", "artifacts": ["/tmp/proof.txt"]},
        "Negative control saved at /tmp/control.txt",
        {},
    ) == "finding"
    assert _normalize_report_category(
        "finding",
        {"status": "verified", "proof_pack": "legacy"},
        "Control case without an artifact",
        {"evidence": "/tmp/proof.txt"},
    ) == "validation_failure"


def test_format_target_coverage_counts_scoped_tasks_and_report_items():
    plan = OperationPlan(
        objective="Assess targets",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[
            OperationTarget(target_id="target-1", value="http://one.test", type="network"),
            OperationTarget(target_id="target-2", value="http://two.test", type="network"),
        ],
    )
    tasks = [
        Task("task-1", "One", "Check one", 1, "done", target_scope="subset", target_ids=["target-1"]),
        Task("task-2", "All", "Check all", 1, "done"),
    ]
    evidence = [
        {"category": "finding", "metadata": {"target": "http://one.test"}, "content": "confirmed"},
        {"category": "validation_failure", "metadata": {"target": "http://two.test"}, "content": "pending"},
    ]

    coverage = _format_target_coverage(plan, tasks, evidence)

    assert "| target-1 | network | `http://one.test` | 2 | 1 | 0 |" in coverage
    assert "| target-2 | network | `http://two.test` | 1 | 0 | 1 |" in coverage


@pytest.fixture(autouse=True)
def memory_client_clear():
    clear_memory_client()


@patch("modules.handlers.report_generator.get_memory_client")
def test_report_builder_downgrade_logic(mock_get_client, tmp_path, monkeypatch):
    op_id = "OP_DOWNGRADE_TEST"
    output_dir = tmp_path / "outputs"
    monkeypatch.setenv("CYBER_AGENT_OUTPUT_DIR", str(output_dir))

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
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]},
                "negative_control_artifacts": [str(tmp_path / "negative-control.txt")],
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
        {
            "id": "4",
            "memory": "[FINDING] Verified claim without a control [EVIDENCE] /tmp/proof.txt",
            "metadata": {
                "category": "finding",
                "operation_id": op_id,
                "severity": "HIGH",
                "validation_status": "verified",
                "proof_pack": {"artifacts": [str(tmp_path / "proof.txt")]},
            },
        },
        {
            "id": "5",
            "memory": "[OBSERVATION] Endpoint /api/items returned 404",
            "metadata": {
                "category": "observation",
                "operation_id": op_id,
                "severity": "HIGH",
            },
        },
    ]

    # Create the proof file
    (tmp_path / "proof.txt").write_text("proof")
    (tmp_path / "negative-control.txt").write_text("control")

    # Run build_report_sections
    sections = build_report_sections(op_id, "example.com", "Test Objective")

    evidence = sections.get("raw_evidence", [])

    # Check item 1: Should remain a finding
    item1 = next(e for e in evidence if e["id"] == "1")
    assert item1["category"] == "finding", "Item 1 should remain a finding"

    # Unverified claims remain visible as validation failures.
    item3 = next(e for e in evidence if e["id"] == "2")
    assert item3["category"] == "validation_failure"

    # Check item 3: Should be downgraded to observation (hypothesis)
    item4 = next(e for e in evidence if e["id"] == "3")
    assert item4["category"] == "validation_failure"

    missing_control = next(e for e in evidence if e["id"] == "4")
    assert missing_control["category"] == "validation_failure"
    assert missing_control["severity"] == "HIGH"

    endpoint_observation = next(e for e in evidence if e["id"] == "5")
    assert endpoint_observation["category"] == "observation"
    assert endpoint_observation["severity"] == "INFO"

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
        config_params={
            "steps_executed": 5,
            "tools_used": ["nmap"],
            "completion_status": {
                "assessment_complete": False,
                "workflow_complete": False,
                "termination_reason": "stalled",
                "termination_message": "No actions taken after 25 attempts",
                "incomplete_reason": "Workflow stalled before assessment_complete=true.",
            },
        },
        filename=str(report_file)
    )

    # Verify report file exists and contains expected sections
    assert report_file.exists()
    content = report_file.read_text()
    assert "# SECURITY ASSESSMENT REPORT" in content
    assert "## TABLE OF CONTENTS" in content
    assert "**Assessment Status: Incomplete**" in content
    assert "Termination reason: `stalled`." in content
    assert "Do not interpret the absence of verified findings as absence of vulnerabilities." in content
    assert "## Section Content" in content
    assert f"Operation ID: {operation_id}" in content

    # Verify that intermediate files were created
    assert (output_dir / "security_assessment_report.json").exists()
    report_json = json.loads((output_dir / "security_assessment_report.json").read_text())
    assert report_json["completion_status"]["assessment_complete"] is False
    assert report_json["completion_status"]["termination_reason"] == "stalled"
    assert (output_dir / "report_executive_summary.md").exists()
    assert (output_dir / "report_findings_header.md").exists()
    # finding_1_High_Finding.md
    assert (output_dir / "finding_1_High_Finding.md").exists()
    assert (output_dir / "report_target_coverage.md").exists()
    assert (output_dir / "report_methodology.md").exists()

@patch("modules.handlers.report_generator.build_report_sections")
def test_generate_security_report_no_evidence(mock_build_sections, tmp_path):
    mock_build_sections.return_value = {"evidence_count": 0}
    callback_handler = MagicMock()
    
    report_file = tmp_path / "no_report.md"
    
    generate_security_report(
        target="example.com",
        objective="Test",
        operation_id="OP123",
        config_params={},
        callback_handler=callback_handler,
        filename=str(report_file)
    )
    
    assert not report_file.exists()
    callback_handler.emit_ui_event.assert_not_called()


@patch("modules.handlers.report_generator.ReportGenerator")
@patch("modules.handlers.report_generator.get_output_path")
@patch("modules.handlers.report_generator.build_report_sections")
@patch("modules.handlers.report_generator.get_config_manager")
def test_generate_security_report_emits_indexed_report_progress(
    mock_get_config,
    mock_build_sections,
    mock_get_output_path,
    mock_report_gen,
    tmp_path,
):
    target = "example.com"
    objective = "Test Objective"
    operation_id = "OP_PROGRESS"
    output_dir = tmp_path / "output_progress"
    output_dir.mkdir()
    mock_get_output_path.return_value = str(output_dir)

    mock_config = MagicMock()
    mock_config.get_provider.return_value = "test_provider"
    mock_config.get_llm_config.return_value.model_id = "test_model"
    mock_config.get_swarm_config.return_value.llm.model_id = "test_swarm_model"
    mock_get_config.return_value = mock_config

    mock_build_sections.return_value = {
        "evidence_count": 3,
        "steps_executed": 5,
        "overview": "Overview content",
        "findings_table": "Findings table",
        "risk_assessment": "Risk assessment",
        "severity_counts": {"HIGH": 1, "MEDIUM": 1, "INFO": 1},
        "summary_table": "Summary table",
        "raw_evidence": [
            {
                "id": "f1",
                "title": "High Finding",
                "severity": "HIGH",
                "category": "finding",
                "content": "High finding content",
            },
            {
                "id": "f2",
                "title": "Medium Finding",
                "severity": "MEDIUM",
                "category": "finding",
                "content": "Medium finding content",
            },
            {
                "id": "o1",
                "title": "Useful Observation",
                "severity": "INFO",
                "category": "observation",
                "content": "Observation content",
            },
        ],
        "operation_plan": {},
        "operation_tasks": [],
        "tools_summary": "",
    }

    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.message = {"content": [{"text": "## Section Content\n"}]}
    mock_agent.return_value = mock_result
    mock_report_gen.create_report_agent.return_value = mock_agent
    callback_handler = MagicMock()

    generate_security_report(
        target=target,
        objective=objective,
        operation_id=operation_id,
        config_params={"steps_executed": 5, "tools_used": ["nmap"]},
        callback_handler=callback_handler,
        filename=str(tmp_path / "final_report.md"),
    )

    progress_events = [
        call.args[0]
        for call in callback_handler.emit_ui_event.call_args_list
        if call.args[0].get("type") == "progress_update"
    ]

    assert [event["report_step_index"] for event in progress_events] == [1, 2, 3, 4, 5]
    assert {event["report_step_total"] for event in progress_events} == {5}
    assert [event["report_step_kind"] for event in progress_events] == [
        "executive",
        "finding",
        "finding",
        "observation",
        "methodology",
    ]
    assert all(event["operation_stage"] == "final_report" for event in progress_events)
    assert progress_events[1]["report_step_label"] == "Finding: High Finding"
    assert progress_events[3]["report_step_label"] == "Observation: Useful Observation"
    callback_handler.set_report_items.assert_called_once_with(mock_build_sections.return_value["raw_evidence"])
    assert callback_handler.mark_report_step_started.call_count == 5
    assert all(
        call.kwargs.get("callback_handler") is not callback_handler
        for call in mock_report_gen.create_report_agent.call_args_list
    )

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
                "severity": "HIGH",
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
    assert not list(output_dir.glob("finding_*Some_Observation.md"))


@patch("modules.handlers.report_generator.ReportGenerator")
@patch("modules.handlers.report_generator.get_output_path")
@patch("modules.handlers.report_generator.build_report_sections")
@patch("modules.handlers.report_generator.get_config_manager")
def test_generate_security_report_validation_failures(
    mock_get_config, mock_build_sections, mock_get_output_path, mock_report_gen, tmp_path
):
    output_dir = tmp_path / "validation_output"
    output_dir.mkdir()
    mock_get_output_path.return_value = str(output_dir)
    config = MagicMock()
    config.get_provider.return_value = "test_provider"
    config.get_llm_config.return_value.model_id = "test_model"
    config.get_swarm_config.return_value.llm.model_id = "test_swarm_model"
    mock_get_config.return_value = config
    mock_build_sections.return_value = {
        "evidence_count": 1,
        "steps_executed": 1,
        "overview": "Overview",
        "findings_table": "",
        "risk_assessment": "",
        "severity_counts": {},
        "validation_failure_count": 1,
        "summary_table": "",
        "raw_evidence": [
            {
                "id": "v1",
                "title": "Possible authorization bypass",
                "category": "validation_failure",
                "content": "Admin data may be exposed",
                "validation_status": "failed",
                "metadata": {
                    "claimed_severity": "HIGH",
                    "validation_reason": "The response artifact contained a tool error",
                },
            }
        ],
        "operation_plan": {},
        "operation_tasks": [],
        "tools_summary": "",
    }
    agent = MagicMock()
    mock_report_gen.create_report_agent.return_value = agent
    agent.return_value.message = {"content": [{"text": "Generated section"}]}

    report_file = tmp_path / "validation_report.md"
    generate_security_report(
        target="example.com",
        objective="Test Objective",
        operation_id="OP-VALIDATION",
        config_params={},
        filename=str(report_file),
    )

    content = report_file.read_text()
    assert "FINDINGS REQUIRING VALIDATION" in content
    assert "Possible authorization bypass" in content
    assert "The response artifact contained a tool error" in content
    assert "not confirmed vulnerabilities" in content

if __name__ == "__main__":
    pytest.main([__file__])
