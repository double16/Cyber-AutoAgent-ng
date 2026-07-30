import json
from unittest.mock import MagicMock, patch

import pytest

from modules.handlers.report_generator import (
    _append_artifact_evidence,
    _append_inline_review_feedback,
    _apply_next_steps_budget_scope,
    _cleanup_report_agent,
    _configured_nonnegative_int,
    _extract_text_from_result,
    _format_artifact_excerpt,
    _format_next_steps_appendix,
    _format_taxonomy_mappings,
    _format_taxonomy_coverage_tables,
    _format_target_coverage,
    _ground_report_item,
    _has_artifact_reference,
    _artifact_references,
    _normalize_report_category,
    _normalize_budget_config,
    _normalize_artifact_reference,
    _next_steps_fallback,
    _parse_latest_operation_log,
    _run_next_steps_refinement,
    _run_report_refinement,
    _select_artifact_excerpt,
    _validate_report_critique,
    _validate_next_steps,
    build_report_sections,
    generate_security_report,
)
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, Task, clear_memory_client
from tests.helpers.acceptance import make_acceptance


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
        "metadata": {"artifacts": ["artifacts/op/real.txt"]},
    }

    grounded = _ground_report_item("### Issue\nEvidence: artifacts/invented.txt", item)

    assert "artifacts/invented.txt" not in grounded
    assert "artifact:artifacts/op/real.txt" in grounded
    assert "### Issue" in grounded
    assert "Unsupported artifact references were removed" in grounded


def test_artifact_reference_parser_accepts_canonical_and_bare_paths_without_false_positives():
    text = (
        "Reference: `artifact:artifacts/positive_control.txt`, outputs/proof.log, "
        "artifacts/secondary.txt and artifact_id:legacy.txt. "
        "Artifact: label, // comment, /vulnerabilities/xss_s, and https://host/outputs/proof.log."
    )

    references = _artifact_references(text)

    assert references == {
        "artifact:artifacts/positive_control.txt",
        "artifact:outputs/proof.log",
        "artifact:artifacts/secondary.txt",
        "artifact:artifacts/legacy.txt",
    }
    assert _has_artifact_reference(text)
    assert _normalize_artifact_reference("`outputs/proof.log`,") == "artifact:outputs/proof.log"


def test_grounding_normalizes_bare_reference_and_preserves_markdown_syntax():
    item = {"metadata": {"artifacts": ["artifact:artifacts/proof.txt"]}}

    grounded = _ground_report_item(
        "### Finding\nReference: `artifacts/proof.txt`\nPHP comment: // keep this",
        item,
    )

    assert "unsupported artifact reference removed" not in grounded
    assert "`artifacts/proof.txt`" in grounded
    assert "// keep this" in grounded


def test_append_artifact_evidence_includes_relevant_sensitive_excerpt(monkeypatch, tmp_path):
    artifact = tmp_path / "proof.txt"
    artifact.write_text(
        "unrelated header\n"
        "HTTP/1.1 200 OK\n"
        "<script>alert('XSS_PROVED')</script> Authorization: Bearer confidential-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "modules.handlers.report_generator._artifact_path_from_ref",
        lambda _reference: str(artifact),
    )
    item = {
        "title": "Stored XSS",
        "content": "The stored script alert was reproduced.",
        "metadata": {"artifacts": ["artifact:artifacts/proof.txt"]},
    }

    report = _append_artifact_evidence("### Stored XSS", item)

    assert "#### Artifact Evidence Excerpts" in report
    assert "artifact:artifacts/proof.txt" in report
    assert "lines 1-3" in report
    assert "Authorization: Bearer confidential-token" in report


def test_append_artifact_evidence_ignores_target_paths_and_unreadable_artifacts(monkeypatch):
    item = {
        "content": "Affected endpoint /vulnerabilities/xss",
        "metadata": {"artifacts": ["artifact:artifacts/missing.txt"]},
    }
    monkeypatch.setattr(
        "modules.handlers.report_generator._artifact_path_from_ref",
        lambda _reference: (_ for _ in ()).throw(ValueError("missing")),
    )

    assert _append_artifact_evidence("### Finding", item) == "### Finding"


def test_artifact_excerpt_selection_handles_empty_long_and_unmatched_content(tmp_path):
    empty_artifact = tmp_path / "empty.txt"
    empty_artifact.write_text("\n\n", encoding="utf-8")
    assert _select_artifact_excerpt(str(empty_artifact), {"proof"}) == []

    artifact = tmp_path / "unmatched.txt"
    artifact.write_text(
        "\n" + "x" * 1100 + "\n" + "\n".join(f"ordinary line {index}" for index in range(20)),
        encoding="utf-8",
    )
    excerpt = _select_artifact_excerpt(str(artifact), {"absent-keyword"}, max_lines=6)

    assert len(excerpt) == 6
    assert excerpt[0][0] == 2
    assert excerpt[0][1].endswith("[line excerpt truncated]")


def test_format_artifact_excerpt_reports_disjoint_line_ranges():
    formatted = _format_artifact_excerpt(
        "artifact:artifacts/proof.txt",
        [(1, "first"), (2, "second"), (5, "fifth")],
    )

    assert "lines 1-2, 5" in formatted
    assert "1: first" in formatted
    assert "5: fifth" in formatted


def test_taxonomy_coverage_tables_count_verified_findings_once_and_link_anchors():
    taxonomy = {
        "cwe": [
            {"id": "CWE-79", "name": "XSS", "url": "https://cwe.test/79"},
            {"id": "CWE-79", "name": "XSS", "url": "https://cwe.test/79"},
        ],
        "mitre_attack": [{"id": "T1190", "name": "Exploit Public-Facing Application", "url": "https://attack.test/T1190"}],
    }
    content = _format_taxonomy_coverage_tables(
        [
            {"id": "one", "anchor": "#finding-one", "title": "Stored XSS", "metadata": {"taxonomy": taxonomy}},
            {"id": "two", "anchor": "#finding-two", "title": "Other XSS", "metadata": {"taxonomy": taxonomy}},
        ]
    )

    assert "### CWE Coverage" in content
    assert "| [CWE-79](https://cwe.test/79) | XSS | 2 |" in content
    assert "[Stored XSS](#finding-one)" in content
    assert "### MITRE ATT&CK Coverage" in content
    assert "| [T1190](https://attack.test/T1190) | Exploit Public-Facing Application | 2 |" in content


def test_taxonomy_coverage_tables_have_verified_empty_state():
    content = _format_taxonomy_coverage_tables([])

    assert content.count("Taxonomy annotation was not attempted for verified findings") == 2


def test_taxonomy_mapping_provenance_shows_active_source_and_configured_urls():
    content = _format_taxonomy_mappings(
        {
            "cwe": [],
            "mitre_attack": [],
            "provenance": {
                "source": "snapshot",
                "version": "official-refresh",
                "configured_refresh_urls": ["https://catalog.example/taxonomy.json"],
            },
        }
    )

    assert "source: `snapshot`; version: `official-refresh`" in content
    assert "Configured refresh URL(s): `https://catalog.example/taxonomy.json`" in content


def test_taxonomy_reporting_distinguishes_failed_and_completed_unmapped_annotations():
    failed = _format_taxonomy_mappings(
        {"cwe": [], "mitre_attack": []},
        {"status": "failed", "error": "invalid taxonomy schema"},
    )
    completed = _format_taxonomy_coverage_tables(
        [
            {
                "title": "Verified finding",
                "metadata": {
                    "taxonomy": {"cwe": [], "mitre_attack": []},
                    "taxonomy_annotation": {"status": "completed"},
                },
            }
        ]
    )

    assert failed.count("Taxonomy annotation failed: invalid taxonomy schema") == 2
    assert completed.count("Taxonomy annotation completed, but no supported mappings were recorded.") == 2


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


def _agent_result(text):
    result = MagicMock()
    result.message = {"content": [{"text": text}]}
    return result


def test_report_refinement_feeds_each_critic_rejection_back_to_actor():
    actor = MagicMock(
        side_effect=[
            _agent_result("## Draft one"),
            _agent_result("## Draft two"),
            _agent_result("## Final draft"),
        ]
    )
    critic = MagicMock(
        side_effect=[
            _agent_result(r'''```json
            {"approved": false, "feedback": ["Fix the "risk" wording",],}
            ```'''),
            _agent_result('{"approved": false, "feedback": ["Cite the evidence artifact"]}'),
        ]
    )

    content, final_critique = _run_report_refinement(
        actor,
        critic,
        "Canonical source data",
        "Executive summary",
        "Executive section requirements",
        refinement_cycles=2,
        json_retries=1,
    )

    assert content == "## Final draft"
    assert final_critique == {"approved": False, "feedback": ["Cite the evidence artifact"]}
    assert actor.call_count == 3
    assert critic.call_count == 2
    assert "## Draft one" in actor.call_args_list[1].args[0]
    assert 'Fix the \\"risk\\" wording' in actor.call_args_list[1].args[0]
    assert "## Draft two" in actor.call_args_list[2].args[0]
    assert "Cite the evidence artifact" in actor.call_args_list[2].args[0]


def test_report_refinement_configuration_defaults_and_clamps_values():
    config = MagicMock()
    config.getenv_int.side_effect = [2, -3, "invalid"]

    assert _configured_nonnegative_int(config, "SETTING", 7) == 2
    assert _configured_nonnegative_int(config, "SETTING", 7) == 0
    assert _configured_nonnegative_int(config, "SETTING", 7) == 7

    config.getenv_int.side_effect = RuntimeError("unavailable")
    assert _configured_nonnegative_int(config, "SETTING", 7) == 7


def test_cleanup_report_agent_tolerates_cleanup_failure():
    agent = MagicMock()
    agent.cleanup.side_effect = RuntimeError("cleanup failed")

    _cleanup_report_agent(None, "unused")
    _cleanup_report_agent(agent, "test agent")

    agent.cleanup.assert_called_once()

@pytest.mark.parametrize(
    ("critique", "error"),
    [
        ({"approved": "yes", "feedback": []}, "approved must be a boolean"),
        ({"approved": False, "feedback": "revise"}, "feedback must be a list"),
        ({"approved": True, "feedback": ["unneeded"]}, "must have empty feedback"),
        ({"approved": False, "feedback": []}, "require feedback"),
    ],
)
def test_validate_report_critique_rejects_invalid_schema(critique, error):
    with pytest.raises(ValueError, match=error):
        _validate_report_critique(critique)


def test_report_refinement_stops_on_critic_approval():
    actor = MagicMock(return_value=_agent_result("## Approved draft"))
    critic = MagicMock(return_value=_agent_result('{"approved": true, "feedback": []}'))

    content, final_critique = _run_report_refinement(
        actor,
        critic,
        "Canonical source data",
        "Finding: Example",
        "Finding requirements",
        refinement_cycles=2,
        json_retries=1,
    )

    assert content == "## Approved draft"
    assert final_critique is None
    actor.assert_called_once()
    critic.assert_called_once()


def test_report_refinement_zero_cycles_runs_only_initial_actor():
    actor = MagicMock(return_value=_agent_result("## Unreviewed draft"))

    content, final_critique = _run_report_refinement(
        actor,
        None,
        "Canonical source data",
        "Assessment methodology",
        "Methodology requirements",
        refinement_cycles=0,
        json_retries=1,
    )

    assert content == "## Unreviewed draft"
    assert final_critique is None
    actor.assert_called_once()


def test_report_refinement_retries_invalid_critic_json_without_failing_report():
    actor = MagicMock(side_effect=[_agent_result("Draft"), _agent_result("Revised draft")])
    critic = MagicMock(side_effect=[_agent_result("not json"), _agent_result('{"approved": "yes"}')])

    content, final_critique = _run_report_refinement(
        actor,
        critic,
        "Canonical source data",
        "Observation: Example",
        "Observation requirements",
        refinement_cycles=1,
        json_retries=1,
    )

    assert content == "Revised draft"
    assert final_critique == {
        "approved": False,
        "feedback": ["The report critic did not return a valid structured review after 2 attempt(s)."],
    }
    assert critic.call_count == 2
    assert "could not be parsed" in critic.call_args_list[1].args[0]
    assert final_critique["feedback"][0] in actor.call_args_list[1].args[0]


def test_inline_report_review_note_keeps_feedback_in_originating_section():
    note = _append_inline_review_feedback(
        "### Latest actor report",
        {"approved": False, "feedback": ["Verify the claimed impact"]},
    )

    assert note.startswith("### Latest actor report")
    assert "**Further Review Required**" in note
    assert "- Verify the claimed impact" in note
    assert '"approved"' not in note
    assert "```json" not in note


def test_latest_operation_log_parser_uses_only_final_session(tmp_path):
    old_metrics = {
        "type": "metrics_update",
        "metrics": {
            "totalTokens": 9000,
            "duration": "50m",
            "budget": {"maxDurationMinutes": 60, "maxTokens": 10000, "maxCost": 5},
        },
    }
    new_metrics = {
        "type": "metrics_update",
        "metrics": {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
            "duration": "5m",
            "cost": 0.25,
            "budget": {"maxDurationMinutes": 30, "maxTokens": None, "maxCost": 1.5},
        },
    }
    new_tool = {"type": "tool_start", "tool_name": "nmap"}
    new_failure = {"type": "tool_end", "tool_name": "nmap", "outcome": "error"}
    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-01 10:00:00",
                event(old_metrics),
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-02 11:00:00",
                "Operation OP_LATEST initiated",
                event(new_metrics),
                event(new_tool),
                event(new_failure),
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["session_started"] == "2026-01-02 11:00:00"
    assert parsed["operation_id"] == "OP_LATEST"
    assert parsed["metrics"]["total_tokens"] == 150
    assert parsed["metrics"]["duration"] == "5m"
    assert parsed["configured_budget"] == {"duration": 30, "cost": 1.5}
    assert parsed["tools_used"] == ["nmap"]
    assert parsed["tool_failures"] == {"nmap:error": 1}


def _next_steps_data(budgets):
    return {
        "coverage_gaps": ["One route remains unassessed"],
        "recommended_next_steps": ["Assess the remaining route"],
        "completion_criteria": ["Record a terminal result for every route"],
        "budget_recommendations": [
            {
                "dimension": dimension,
                "current": current,
                "recommended": current * 2,
                "rationale": "Estimated from the remaining coverage.",
            }
            for dimension, current in budgets.items()
        ],
        "agent_improvements": ["Improve route inventory tracking"],
        "tooling_improvements": ["Capture structured scanner failures"],
        "manual_investigations": ["Confirm business authorization expectations with the owner"],
    }


def test_next_steps_validation_allows_only_configured_budgets_and_requires_duration():
    configured = _normalize_budget_config(
        {"maxDurationMinutes": 60, "maxTokens": None, "maxCost": 4.0}
    )
    data = _next_steps_data(configured)

    validated = _validate_next_steps(data, configured)
    markdown = _format_next_steps_appendix(validated)

    assert configured == {"duration": 60, "cost": 4.0}
    assert "## APPENDIX B: RECOMMENDED NEXT STEPS" in markdown
    assert "### Coverage Gaps" in markdown
    assert "### Completion Criteria" in markdown
    assert "| Duration (minutes) | 60 | 120 |" in markdown
    assert "| Cost (USD) | 4.0 | 8.0 |" in markdown
    assert "Token" not in markdown

    elapsed_current = _next_steps_data(configured)
    elapsed_current["budget_recommendations"][0]["current"] = 17
    normalized_elapsed = _validate_next_steps(elapsed_current, configured)
    assert normalized_elapsed["budget_recommendations"][0]["current"] == 60

    lower_than_configured = _next_steps_data(configured)
    lower_than_configured["budget_recommendations"][0].update({"current": 17, "recommended": 30})
    with pytest.raises(ValueError, match="cannot be lower"):
        _validate_next_steps(lower_than_configured, configured)

    invalid = _next_steps_data(configured)
    invalid["budget_recommendations"].append(
        {"dimension": "tokens", "current": 100, "recommended": 200, "rationale": "Not configured"}
    )
    with pytest.raises(ValueError, match="was not configured"):
        _validate_next_steps(invalid, configured)

    with pytest.raises(ValueError, match="duration budget is required"):
        _validate_next_steps(_next_steps_data({}), {})


def test_next_steps_fallback_derives_guidance_from_incomplete_workflow():
    source = {
        "completion_status": {"assessment_complete": False},
        "phase_coverage": [
            {"phase_id": 1, "title": "Discovery", "status": "partial_failure", "task_status_counts": {"partial_failure": 1}},
            {"phase_id": 3, "title": "Impact", "status": "blocked", "task_status_counts": {"partial_failure": 2}},
        ],
        "task_status_counts": {"done": 2, "partial_failure": 3},
        "validation_candidates": [{"id": "candidate-1"}],
        "latest_run": {"metrics": {"duration": "18m"}, "tool_failures": {"shell:error": 1}},
    }

    fallback = _next_steps_fallback({"duration": 60}, source, "invalid JSON")
    markdown = _format_next_steps_appendix(fallback)

    assert fallback["coverage_gaps"]
    assert fallback["recommended_next_steps"]
    assert fallback["completion_criteria"]
    assert fallback["agent_improvements"]
    assert fallback["tooling_improvements"]
    assert "Phase 1 (Discovery) is partial failure" in markdown
    assert "shell:error" in markdown
    assert "Automated next-step generation returned invalid structured data" in markdown


def test_incomplete_next_steps_use_continuation_duration_budget_by_default():
    configured = {"duration": 60, "cost": 4.0}
    data = _next_steps_data(configured)
    data["budget_recommendations"][0]["recommended"] = 120

    normalized = _apply_next_steps_budget_scope(
        data,
        configured,
        {
            "completion_status": {"assessment_complete": False},
            "phase_coverage": [{"phase_id": 1, "status": "partial_failure"}],
        },
    )

    duration = normalized["budget_recommendations"][0]
    assert duration["recommended"] == 60
    assert "Continue the existing operation" in duration["rationale"]
    assert "new-operation total" in duration["rationale"]


def test_incomplete_next_steps_keep_new_operation_budget_when_rerun_is_explicit():
    configured = {"duration": 60}
    data = _next_steps_data(configured)
    data["recommended_next_steps"] = ["Rerun as a new operation with a clean task plan"]
    data["budget_recommendations"][0]["recommended"] = 120

    normalized = _apply_next_steps_budget_scope(
        data,
        configured,
        {"completion_status": {"assessment_complete": False}, "phase_coverage": []},
    )

    recommendation = normalized["budget_recommendations"][0]
    assert recommendation["recommended"] == 120
    assert "Start a new operation" in recommendation["rationale"]


def test_next_steps_fallback_allows_empty_non_budget_lists_for_completed_workflow():
    fallback = _next_steps_fallback(
        {"duration": 60},
        {"completion_status": {"assessment_complete": True}, "phase_coverage": []},
        "invalid JSON",
    )

    assert fallback["coverage_gaps"] == []
    assert fallback["recommended_next_steps"] == []
    assert fallback["completion_criteria"] == []


def test_next_steps_final_rejection_keeps_latest_actor_data():
    configured = {"duration": 60}
    first = _next_steps_data(configured)
    revised = _next_steps_data(configured)
    revised["coverage_gaps"] = ["Revised latest coverage gap"]
    actor = MagicMock(side_effect=[_agent_result(json.dumps(first)), _agent_result(json.dumps(revised))])
    critic = MagicMock(
        return_value=_agent_result('{"approved": false, "feedback": ["Clarify the coverage gap"]}')
    )

    data, critique = _run_next_steps_refinement(
        actor,
        critic,
        "Source data",
        "Requirements",
        configured,
        {},
        refinement_cycles=1,
        json_retries=0,
    )

    assert data["coverage_gaps"] == ["Revised latest coverage gap"]
    assert critique == {"approved": False, "feedback": ["Clarify the coverage gap"]}
    assert "Clarify the coverage gap" in actor.call_args_list[1].args[0]


def test_next_steps_refinement_uses_deterministic_fallback_without_critic():
    actor = MagicMock(return_value=_agent_result("not JSON"))
    critic = MagicMock()
    source = {
        "completion_status": {"assessment_complete": False},
        "phase_coverage": [{"phase_id": 2, "title": "Validation", "status": "blocked"}],
    }

    data, critique = _run_next_steps_refinement(
        actor,
        critic,
        "Source data",
        "Requirements",
        {"duration": 60},
        source,
        refinement_cycles=1,
        json_retries=0,
    )

    assert data["coverage_gaps"]
    assert critique is None
    critic.assert_not_called()


def test_report_category_helpers_cover_structured_and_free_form_artifacts():
    assert _has_artifact_reference({"artifacts": ["/tmp/control.txt"]}) is True
    assert _has_artifact_reference({"evidence": ["saved at artifacts/control.txt"]}) is True
    assert _has_artifact_reference({"evidence": ["artifact:artifacts/control.txt"]}) is True
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
        Task("task-1", "One", "Check one", make_acceptance("task-1"), 1, "done", target_scope="subset", target_ids=["target-1"]),
        Task("task-2", "All", "Check all", make_acceptance("task-2"), 1, "done"),
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
                "finding_uid": "finding-1",
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
        {
            "id": "8",
            "memory": "Task acceptance for the verified command injection finding.",
            "metadata": {
                "category": "observation",
                "source": "task_acceptance",
                "task_uid": "task-verified",
                "operation_id": op_id,
            },
        },
        {
            "id": "9",
            "memory": "Task acceptance for a finding that still requires validation.",
            "metadata": {
                "category": "observation",
                "source": "task_acceptance",
                "task_uid": "task-unverified",
                "operation_id": op_id,
            },
        },
        {
            "id": "10",
            "memory": "Interim observation from the same task as a verified finding.",
            "metadata": {
                "category": "observation",
                "source": "store_observation",
                "task_uid": "task-verified",
                "operation_id": op_id,
            },
        },
        {
            "id": "11",
            "memory": "Task acceptance without task provenance remains reportable.",
            "metadata": {
                "category": "observation",
                "source": "task_acceptance",
                "operation_id": op_id,
            },
        },
        {
            "id": "6",
            "memory": "flag{wrong-format}",
            "metadata": {
                "category": "objective_validation_failure",
                "operation_id": op_id,
                "candidate_uid": "candidate-1",
                "candidate_value": "flag{wrong-format}",
                "objective_type": "flag",
                "validation_type": "objective",
                "validation_status": "failed",
                "validation_reason": "Candidate did not match the required flag format",
                "confidence": 95,
            },
        },
        {
            "id": "7",
            "memory": "FLAG{accepted}",
            "metadata": {
                "category": "objective_result",
                "operation_id": op_id,
                "candidate_uid": "candidate-2",
                "candidate_value": "FLAG{accepted}",
                "objective_type": "flag",
                "validation_type": "objective",
                "validation_status": "verified",
                "confidence": 90,
            },
        },
    ]
    mock_client.list_finding_records.return_value = [
        {
            "finding_uid": "finding-1",
            "candidate_data": {
                "source_task_uids": ["task-verified"],
                "taxonomy": {
                    "cwe": [{"id": "CWE-78"}],
                    "mitre_attack": [{"id": "T1059.004"}],
                },
                "taxonomy_annotation": {"status": "completed"},
                "final_attack_enrichment": {"status": "completed"},
            },
        },
        {
            "finding_uid": "finding-2",
            "candidate_data": {"source_task_uids": ["task-unverified"]},
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
    assert item1["metadata"]["taxonomy"]["mitre_attack"] == [{"id": "T1059.004"}]
    assert item1["metadata"]["final_attack_enrichment"]["status"] == "completed"

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
    assert "8" not in {item["id"] for item in evidence}
    assert next(item for item in evidence if item["id"] == "9")["category"] == "observation"
    assert next(item for item in evidence if item["id"] == "10")["category"] == "observation"
    assert next(item for item in evidence if item["id"] == "11")["category"] == "observation"

    rejected_objective = next(e for e in evidence if e["id"] == "6")
    confirmed_objective = next(e for e in evidence if e["id"] == "7")
    assert rejected_objective["category"] == "objective_validation_failure"
    assert confirmed_objective["category"] == "objective_result"
    assert sections["verified_findings_total"] == 1
    assert sections["finding_validation_failure_count"] == 3
    assert sections["objective_validation_failure_count"] == 1
    assert sections["objective_validation_status"] == "verified"

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
        "verified_findings_total": 1,
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
        "task_status_counts": {"done": 2, "pending": 1},
        "total_task_count": 3,
        "completed_task_count": 2,
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
    disclaimer = (
        "> **AI-Generated Content Disclaimer:** This report was generated with artificial intelligence and may "
        "contain errors, omissions, or hallucinations."
    )
    assert content.startswith(disclaimer)
    assert content.rstrip().endswith(
        "A qualified human should independently verify its findings and recommendations before relying on them."
    )
    assert content.count("AI-Generated Content Disclaimer") == 2
    assert "# SECURITY ASSESSMENT REPORT" in content
    assert "## TABLE OF CONTENTS" in content
    assert "**Assessment Status: Incomplete**" in content
    assert "Termination reason: `stalled`." in content
    assert "Do not interpret the absence of verified findings as absence of vulnerabilities." in content
    assert "## Section Content" in content
    assert "**Further Review Required**" in content
    assert "[Further Review Required](#further-review-required)" not in content
    assert '"approved"' not in content
    assert "```json" not in content
    assert f"Operation ID: {operation_id}" in content

    # Verify that intermediate files were created
    assert (output_dir / "security_assessment_report.json").exists()
    report_json = json.loads((output_dir / "security_assessment_report.json").read_text())
    assert report_json["completion_status"]["assessment_complete"] is False
    assert report_json["completion_status"]["termination_reason"] == "stalled"
    assert report_json["completion_status"]["total_task_count"] == 3
    assert report_json["completion_status"]["completed_task_count"] == 2
    assert report_json["completion_status"]["task_status_counts"] == {"done": 2, "pending": 1}
    assert (output_dir / "report_executive_summary.md").exists()
    assert (output_dir / "report_findings_header.md").exists()
    # finding_1_High_Finding.md
    assert (output_dir / "finding_1_High_Finding.md").exists()
    assert (output_dir / "report_target_coverage.md").exists()
    assert (output_dir / "report_methodology.md").exists()
    assert (output_dir / "report_recommended_next_steps.md").exists()
    assert "## APPENDIX A: ASSESSMENT METHODOLOGY" in content
    assert "## APPENDIX B: RECOMMENDED NEXT STEPS" in content
    assert "### Coverage Gaps" in content
    assert "### Completion Criteria" in content
    assert "| Duration (minutes) | 60 | 60 |" in content
    assert "AI-Generated Content Disclaimer" not in (output_dir / "report_methodology.md").read_text()

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
    mock_config.getenv_int.side_effect = lambda name, default: 0 if name == "CYBER_REPORT_REFINEMENT_CYCLES" else default
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

    mock_result = MagicMock()
    mock_result.message = {"content": [{"text": "## Section Content\n"}]}
    created_agents = []

    def create_agent(**_kwargs):
        agent = MagicMock(return_value=mock_result)
        created_agents.append(agent)
        return agent

    mock_report_gen.create_report_agent.side_effect = create_agent
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

    assert [event["report_step_index"] for event in progress_events] == [1, 2, 3, 4, 5, 6]
    assert {event["report_step_total"] for event in progress_events} == {6}
    assert [event["report_step_kind"] for event in progress_events] == [
        "executive",
        "finding",
        "finding",
        "observation",
        "methodology",
        "next_steps",
    ]
    assert all(event["operation_stage"] == "final_report" for event in progress_events)
    assert progress_events[1]["report_step_label"] == "Finding: High Finding"
    assert progress_events[3]["report_step_label"] == "Observation: Useful Observation"
    callback_handler.set_report_items.assert_called_once_with(mock_build_sections.return_value["raw_evidence"])
    assert len(created_agents) == 6
    assert len({id(agent) for agent in created_agents}) == 6
    assert all(agent.cleanup.call_count == 1 for agent in created_agents)
    assert callback_handler.mark_report_step_started.call_count == 6
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
    mock_config.getenv_int.side_effect = (
        lambda name, default: 0 if name == "CYBER_REPORT_REFINEMENT_CYCLES" else default
    )
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
    assert "FURTHER REVIEW REQUIRED" not in content
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
        "finding_validation_failure_count": 1,
        "objective_validation_status": "failed",
        "objective_validation_failure_count": 1,
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
            },
            {
                "id": "o1",
                "content": "flag{wrong}",
                "category": "objective_validation_failure",
                "validation_type": "objective",
                "validation_status": "failed",
                "metadata": {
                    "candidate_value": "flag{wrong}",
                    "objective_type": "flag",
                    "confidence": 95,
                    "validator": "task_evaluator",
                    "validation_reason": "Format mismatch",
                    "evidence_artifacts": ["artifact:flag.txt"],
                },
            },
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
    objective_content = (output_dir / "report_objective_validation.md").read_text()
    assert "## OBJECTIVE VALIDATION" in objective_content
    assert "Rejected or unresolved" in objective_content
    assert "flag{wrong}" in objective_content

if __name__ == "__main__":
    pytest.main([__file__])
