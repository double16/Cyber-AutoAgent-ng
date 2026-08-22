import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from modules.handlers import report_generator as report_generator_module
from modules.handlers.report_generator import (
    _append_artifact_evidence,
    _append_inline_review_feedback,
    _apply_next_steps_budget_scope,
    _cleanup_report_agent,
    _configured_nonnegative_int,
    _extract_text_from_result,
    _emit_report_progress,
    _format_artifact_excerpt,
    _format_execution_history,
    _format_executive_deterministic_sections,
    _format_finding_with_narrative,
    _format_summary_table,
    _format_operation_plan,
    _format_operation_tasks,
    _format_observation,
    _markdown_table_cell,
    _format_model_usage_table,
    _remove_generated_execution_metrics,
    _format_next_steps_appendix,
    _format_taxonomy_mappings,
    _format_taxonomy_coverage_tables,
    _format_target_coverage,
    _ground_report_item,
    _has_artifact_reference,
    _artifact_references,
    _omit_cross_operation_artifact_references,
    _normalize_report_category,
    _normalize_budget_config,
    _normalize_artifact_reference,
    _next_steps_fallback,
    _parse_latest_operation_log,
    _https_repository_url,
    _software_provenance,
    _run_next_steps_refinement,
    _run_report_refinement,
    _select_artifact_excerpt,
    _validate_report_critique,
    _validate_next_steps,
    _validate_report_consistency,
    _canonical_report_data,
    _canonical_report_json,
    _informational_observation_context,
    _inventory_endpoint_values,
    _is_reportable_informational_observation,
    _resolve_inventory_ids_for_display,
    _format_verified_findings_summary,
    _compact_finding_context,
    _compact_next_steps_source,
    _current_operation_report_memories,
    _validate_narrative_consistency,
    _format_report_consistency_warnings,
    build_report_sections,
    generate_deterministic_fallback_report,
    generate_security_report,
)
from modules.tools.memory import OperationPlan, OperationTarget, PlanPhase, Task, clear_memory_client
from tests.helpers.acceptance import make_acceptance


def test_canonical_report_data_owns_counts_and_json_separates_narrative():
    sections = {
        "operation_id": "OP_TEST",
        "target": "https://example.test",
        "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
        "verified_findings_total": 1,
        "raw_evidence": [{
            "id": "f-1", "title": "Stored XSS", "category": "finding", "severity": "CRITICAL",
            "metadata": {"artifacts": ["artifact:artifacts/proof.txt"]},
        }],
        "completion_status": {"assessment_complete": False},
        "phase_coverage": [{"phase_id": 1, "status": "done", "task_status_counts": {}}],
    }
    canonical = _canonical_report_data(sections)
    report = _canonical_report_json(sections, {"executive": "one critical finding"}, [])

    assert canonical["verified_findings_total"] == 1
    assert canonical["artifact_references"] == ["artifact:artifacts/proof.txt"]
    assert report["canonical"]["severity_counts"]["critical"] == 1
    assert report["narrative"]["executive"] == "one critical finding"


def test_compact_finding_context_excludes_raw_metadata_and_bounds_evidence():
    finding = {
        "title": "Stored XSS",
        "severity": "HIGH",
        "content": "evidence " * 500,
        "metadata": {"artifacts": ["artifact:artifacts/proof.txt"], "secret": "do-not-send"},
    }

    context = _compact_finding_context(finding, "https://example.test")

    assert context["target"] == "https://example.test"
    assert context["artifact_references"] == ["artifact:artifacts/proof.txt"]
    assert len(context["evidence_summary"]) <= 1200
    assert "secret" not in context


def test_compact_next_steps_source_excludes_raw_history_and_tools_used():
    source = _compact_next_steps_source(
        target="https://example.test",
        objective="Assess the target",
        completion_status={"assessment_complete": False},
        sections={
            "phase_coverage": [{"phase_id": 1, "title": "Mapping", "status": "done", "raw": "omit"}],
            "task_status_counts": {"done": 1},
            "total_task_count": 2,
            "completed_task_count": 1,
            "execution_history": "large raw history",
        },
        latest_run={
            "metrics": {"duration": "10m", "total_tokens": 100},
            "tools_used": ["shell"],
            "tool_failures": {"shell:error": 2},
        },
        configured_budget={"duration": 30},
        validation_candidates=[],
    )

    assert source["phase_coverage"] == [{"phase_id": 1, "title": "Mapping", "status": "done"}]
    assert "execution_history" not in source
    assert "tools_used" not in source
    assert source["tool_failure_counts"] == {"shell:error": 2}


def test_deterministic_renderers_keep_facts_out_of_llm_narrative():
    sections = {
        "summary_table": "| Finding |\n|---|\n| SQLi |",
        "severity_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
        "verified_findings_total": 1,
        "finding_validation_failure_count": 2,
        "observation_count": 3,
        "completion_status": {"assessment_complete": False, "incomplete_reason": "Coverage remains partial."},
        "raw_evidence": [
            {
                "title": "Unverified [claim]",
                "category": "validation_failure",
                "metadata": {"validation_reason": "<script>alert(1)</script>"},
            },
            {
                "title": "Observed [header]",
                "category": "observation",
                "content": "<script>alert(1)</script>",
            },
        ],
    }
    finding = {
        "title": "SQL injection",
        "severity": "HIGH",
        "content": "Differential response observed.",
        "metadata": {"artifacts": ["artifact:artifacts/proof.txt"]},
    }

    executive = _format_executive_deterministic_sections(sections)
    detail = _format_finding_with_narrative(finding, 0, "#### Impact\n\nSupported impact.\n\n#### Remediation\n\nPatch it.\n\n#### TECHNICAL APPENDIX\n\nNotes.")
    tasks = _format_operation_tasks({"items": ["Task,Test target,outcome,2,done,,,,,,https://example.test,1/1"]})

    assert "### Key Findings" in executive
    assert "### Claim Status" in executive
    assert "#### Evidence" in detail
    assert "#### Impact Grounding" in detail
    assert "#### Attack Path Analysis\n\nNot established from supplied evidence" in detail
    assert "| 2 | Task | done | https://example.test | 1/1 |" in tasks
    assert "Unverified \\[claim\\]" in executive
    assert "\\<script\\>alert(1)\\</script\\>" in executive
    plan = _format_operation_plan(
        {"phases": [{"id": 1, "title": "Mapping", "status": "done", "criteria": "Inventory routes"}]}
    )
    assert "| 1 | done | **Mapping:** Inventory routes |" in plan


def test_finding_narrative_omits_unbacked_secret_shaped_examples():
    finding = {
        "title": "Configuration exposure",
        "severity": "HIGH",
        "content": "The endpoint returned a database connection string.",
        "metadata": {"observed_result": "A database connection string was returned."},
    }
    narrative = (
        "#### Impact\n\nPotential exposure.\n\n#### Remediation\n\nRemove the secret.\n\n"
        "#### TECHNICAL APPENDIX\n\n```json\n{\"connection\":\"postgresql://admin:invented@db/prod\"}\n```"
    )

    detail = _format_finding_with_narrative(finding, 0, narrative)

    assert "postgresql://admin:invented@db/prod" not in detail
    assert "[omitted: not backed by supplied evidence]" in detail


def test_finding_narrative_uses_recorded_validation_steps_and_impact_evidence():
    finding = {
        "title": "Configuration exposure",
        "severity": "HIGH",
        "content": "The endpoint returned configuration data.",
        "metadata": {
            "reproduction_steps": ["Request /api/config", "Observe the response"],
            "impact_evidence_artifacts": ["artifact:artifacts/impact.txt"],
        },
    }

    detail = _format_finding_with_narrative(finding, 0, "#### Impact\n\nDemonstrated impact.")

    assert "1. Request /api/config" in detail
    assert "2. Observe the response" in detail
    assert "#### Impact Grounding" not in detail


def test_report_progress_counts_only_llm_authored_sections():
    callback = MagicMock()

    _emit_report_progress(callback, "OP_TEST", 1, 4, "validation_failure", "Requires validation")
    _emit_report_progress(callback, "OP_TEST", 2, 4, "observation", "Observation")
    _emit_report_progress(callback, "OP_TEST", 3, 4, "executive", "Executive summary")

    callback.mark_report_step_started.assert_called_once_with()
    assert callback.emit_ui_event.call_count == 3


def test_report_observations_exclude_workflow_bookkeeping_but_retain_real_observations():
    acceptance = {
        "id": "acceptance-1",
        "category": "observation",
        "content": "Task acceptance passed.",
        "metadata": {"source": "task_acceptance"},
    }
    plan = {
        "id": "plan-1",
        "category": "observation",
        "content": "Planned task record.",
        "metadata": {"source": "plan"},
    }
    published_acceptance = {
        "id": "acceptance-2",
        "category": "observation",
        "content": "Published acceptance record.",
        "metadata": {"publication_key": "task_acceptance:task-2"},
    }
    observation = {
        "id": "observation-1",
        "category": "observation",
        "content": "The endpoint disclosed a server banner.",
        "metadata": {"source": "store_observation"},
    }
    signal = {"id": "signal-1", "category": "signal", "content": "A useful signal."}
    sections = {"raw_evidence": [acceptance, plan, published_acceptance, observation, signal]}

    assert not _is_reportable_informational_observation(acceptance)
    assert not _is_reportable_informational_observation(plan)
    assert not _is_reportable_informational_observation(published_acceptance)
    assert _is_reportable_informational_observation(observation)
    assert _is_reportable_informational_observation(signal)
    assert [item["id"] for item in _canonical_report_data(sections)["observations"]] == [
        "observation-1",
        "signal-1",
    ]
    assert [item["id"] for item in _informational_observation_context(sections)] == [
        "observation-1",
        "signal-1",
    ]


def test_deterministic_summary_cannot_be_overridden_by_narrative():
    sections = {"verified_findings_total": 1, "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0}}
    summary = _format_verified_findings_summary(sections)

    assert "Verified findings: **1**" in summary
    assert "| CRITICAL | 1 |" in summary
    assert "| HIGH | 0 |" in summary


def test_narrative_consistency_flags_unknown_facts_and_incomplete_completion_claims():
    canonical = {
        "findings": [{"id": "f-1", "title": "Stored XSS"}],
        "artifact_references": ["artifact:artifacts/proof.txt"],
        "phase_coverage": [{"phase_id": 1}],
        "completion_status": {"assessment_complete": False},
    }
    warnings = _validate_narrative_consistency(
        "Finding invented-finding cites artifact:artifacts/missing.txt. All planned tasks completed and no vulnerabilities remain.",
        canonical,
    )

    assert any("unknown finding" in warning for warning in warnings)
    assert any("unregistered artifact" in warning for warning in warnings)
    assert any("incomplete" in warning for warning in warnings)


def test_narrative_consistency_ignores_workflow_terms_after_finding_keywords():
    canonical = {
        "findings": [],
        "artifact_references": [],
        "phase_coverage": [{"phase_id": 1}],
        "completion_status": {"assessment_complete": True},
    }

    warnings = _validate_narrative_consistency(
        "Vulnerability Discovery was incomplete, followed by Finding Validation.",
        canonical,
    )

    assert not any("unknown finding" in warning for warning in warnings)


def test_narrative_consistency_ignores_finding_hypotheses_without_candidates():
    canonical = {
        "findings": [],
        "artifact_references": [],
        "phase_coverage": [],
        "completion_status": {"assessment_complete": False},
    }

    warnings = _validate_narrative_consistency("Finding hypotheses remain unconfirmed.", canonical)

    assert not any("unknown finding" in warning for warning in warnings)


def test_narrative_consistency_flags_false_empty_observation_claim():
    canonical = {
        "observations": [{"id": "observation-1", "category": "observation"}],
        "findings": [],
        "artifact_references": [],
        "phase_coverage": [],
        "completion_status": {"assessment_complete": True},
    }

    warnings = _validate_narrative_consistency("No informational observations were established.", canonical)

    assert any("no informational observations" in warning.lower() for warning in warnings)
def test_format_model_usage_table_and_elapsed_time_fallbacks():
    table = _format_model_usage_table(
        [
            {
                "provider": "ollama",
                "model": "llama3",
                "context_window_tokens": 128000,
                "input_tokens": 1200,
                "output_tokens": 300,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "cost": 0.1234567,
                "inference_time_ms": 2500,
                "correction_categories": {"max_token_exhaustion": 1},
            }
        ],
        "bedrock",
        "fallback-model",
    )

    assert "| Capture Timestamp | Provider | Model | Context Window | Input Tokens | Output Tokens |" in table
    assert (
        "| N/A | ollama | llama3 | 128,000 | 1,200 | 300 | 10 | 5 | 1,500 | $0.123457 | "
        "0 hours 0 minutes | N/A | max_token_exhaustion: 1 |"
        in table
    )
    assert "| fallback-model |" not in table

    fallback = _format_model_usage_table([], "bedrock", "fallback-model", 200000)
    assert "| N/A | bedrock | fallback-model | 200,000 | 0 | 0 | 0 | 0 | 0 | $0.000000 | N/A | N/A | — |" in fallback


def test_report_model_metrics_use_all_persisted_timestamped_rows(monkeypatch):
    config_manager = MagicMock()
    config_manager.get_provider.return_value = "litellm"
    config_manager.get_llm_config.return_value.model_id = "live-model"
    persisted_rows = [
        {
            "captured_at": "2026-08-06T12:00:00.000001+00:00",
            "provider": "litellm",
            "model": "model-a",
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 12,
            "cost": 0.01,
            "inference_time_ms": 20.0,
            "context_window_tokens": 48_000,
            "model_calls": 1,
            "correction_loops": 0,
            "efficiency": 100.0,
        },
        {
            "captured_at": "2026-08-06T12:10:00.000001+00:00",
            "provider": "litellm",
            "model": "model-a",
            "input_tokens": 20,
            "output_tokens": 4,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 24,
            "cost": 0.02,
            "inference_time_ms": 40.0,
            "context_window_tokens": 48_000,
            "model_calls": 2,
            "correction_loops": 1,
            "efficiency": 66.7,
        },
    ]
    monkeypatch.setattr(report_generator_module, "list_persisted_operation_model_metrics", lambda _op: persisted_rows)

    metrics = report_generator_module._resolve_report_model_metrics(
        config_manager,
        {"metrics": {"duration": "5m"}},
        MagicMock(model_usage=lambda: [{"model": "live-model"}]),
        operation_id="OP_CONTINUED",
    )

    assert metrics["model_usage"] == persisted_rows
    table = _format_model_usage_table(metrics["model_usage"], "litellm", "live-model")
    assert table.index("2026-08-06T12:00:00.000001+00:00") < table.index("2026-08-06T12:10:00.000001+00:00")


def test_remove_generated_execution_metrics_preserves_following_appendix_section():
    content = "Narrative\n\n### Execution Metrics\n\n| generated | data |\n\n### Methodology Limitations\n\nKept."

    assert _remove_generated_execution_metrics(content) == "Narrative\n\n### Methodology Limitations\n\nKept."


def test_format_execution_history_is_stable_and_escapes_markdown_cells():
    history = _format_execution_history(
        [
            {
                "phase": 2,
                "title": "Second | task",
                "status": "partial_failure",
                "status_reason": "Needs\nfollow-up",
                "targets": "http://two.test",
                "acceptance": "0/1",
            },
            {
                "phase": 1,
                "title": "First task",
                "status": "done",
                "status_reason": "",
                "targets": "http://one.test",
                "acceptance": "1/1",
            },
        ],
        [
            {
                "phase": 1,
                "title": "First task",
                "criterion_id": "criterion-1",
                "status": "satisfied",
                "disposition": "finding_candidate",
                "summary": "Confirmed evidence",
                "evidence_refs": "artifact:artifacts/proof.txt",
            },
            {
                "phase": 2,
                "title": "Second | task",
                "criterion_id": "criterion-1",
                "status": "not_recorded",
                "disposition": "—",
                "summary": "Needs\nfollow-up",
                "evidence_refs": "—",
            },
        ],
    )

    assert history.index("| 1 | First task") < history.index("| 2 | Second \\| task")
    assert "Needs follow-up" in history
    assert "### Acceptance Outcomes" in history
    assert "Manifest" not in history
    assert "Criterion" not in history
    assert "Evidence" not in history
    assert "http://one.test" in history


def test_markdown_table_cell_escapes_external_markdown_and_html():
    assert _markdown_table_cell("<input name='username'> | [plain]") == "\\<input name='username'\\> \\| \\[plain\\]"
    assert _markdown_table_cell("already `code` and *plain*") == "already \\`code\\` and \\*plain\\*"


def test_inventory_endpoint_resolution_uses_manifest_values_and_preserves_identifiers(tmp_path):
    manifest = tmp_path / "inventory_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "endpoint-1",
                        "target_id": "target-1",
                        "kind": "service",
                        "value": "https://target.test",
                    }
                ]
            }
        )
    )
    task = MagicMock()
    task.acceptance.basis.source_refs = ("artifact:artifacts/inventory_manifest.json",)
    task.evidence = []

    with patch("modules.handlers.report_generator._artifact_path_from_ref", return_value=str(manifest)):
        endpoint_values = _inventory_endpoint_values([task])

    displayed = _resolve_inventory_ids_for_display(
        {
            "id": "endpoint-1",
            "content": "Created hypotheses for endpoint-1; endpoint-99 remains unknown.",
            "metadata": {"target_id": "target-1", "validation_reason": "Endpoint endpoint-1 responded."},
        },
        endpoint_values,
    )

    assert endpoint_values == {"endpoint-1": "https://target.test"}
    assert displayed["id"] == "endpoint-1"
    assert displayed["metadata"]["target_id"] == "target-1"
    assert displayed["content"] == "Created hypotheses for https://target.test; endpoint-99 remains unknown."
    assert displayed["metadata"]["validation_reason"] == "Endpoint https://target.test responded."


def test_inventory_endpoint_resolution_leaves_conflicting_values_unresolved(tmp_path):
    first = tmp_path / "first_manifest.json"
    second = tmp_path / "second_manifest.json"
    for path, value in ((first, "https://one.test"), (second, "https://two.test")):
        path.write_text(json.dumps({"items": [{"id": "endpoint-1", "value": value}]}))

    first_task = MagicMock()
    first_task.acceptance.basis.source_refs = ("artifact:artifacts/first_manifest.json",)
    first_task.evidence = []
    second_task = MagicMock()
    second_task.acceptance.basis.source_refs = ("artifact:artifacts/second_manifest.json",)
    second_task.evidence = []

    with patch(
        "modules.handlers.report_generator._artifact_path_from_ref",
        side_effect=[str(first), str(second)],
    ):
        endpoint_values = _inventory_endpoint_values([first_task, second_task])

    assert endpoint_values == {}
    assert _resolve_inventory_ids_for_display("endpoint-1", endpoint_values) == "endpoint-1"


def test_observation_recorded_detail_escapes_xml_html_tags_and_links():
    content = _format_observation(
        {"title": "Observed markup", "content": "Response contained <input name='user'> and </form>."},
        0,
    )

    assert "\\<input name='user'\\>" in content
    assert "\\</form\\>" in content


def test_observation_recorded_detail_removes_acceptance_prefix_and_duplicate_evidence():
    content = _format_observation(
        {
            "title": "Observed technology",
            "content": (
                "Task acceptance. Criterion criterion-1 [satisfied; observation]: Apache was identified. "
                "Evidence: artifact:artifacts/inventory_manifest.json."
            ),
        },
        0,
    )

    assert "Criterion criterion-1" not in content
    assert "Evidence: artifact:artifacts/inventory_manifest.json" not in content
    assert "Apache was identified." in content


def test_software_provenance_reads_project_manifest(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'cyber-autoagent'\nversion = '9.9.9'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(report_generator_module, "_project_root", lambda: tmp_path)

    assert _software_provenance() == {"name": "cyber-autoagent", "version": "9.9.9"}


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/example/project.git", "https://github.com/example/project"),
        ("git@github.com:example/project.git", "https://github.com/example/project"),
        ("ssh://git@github.com/example/project.git", "https://github.com/example/project"),
    ],
)
def test_repository_url_normalizes_supported_git_remote_forms(remote, expected):
    assert _https_repository_url(remote) == expected


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


def test_shared_memory_artifact_references_are_omitted_without_losing_narrative():
    value = {
        "content": "Prior result used artifact:artifacts/prior/proof.txt and remains relevant.",
        "metadata": {"evidence_refs": ["outputs/prior/request.log"], "category": "observation"},
    }

    sanitized, omitted = _omit_cross_operation_artifact_references(value)

    assert omitted == 2
    assert "remains relevant" in sanitized["content"]
    assert "artifact:artifacts/prior/proof.txt" not in sanitized["content"]
    assert sanitized["metadata"]["evidence_refs"] == ["[prior-operation artifact omitted]"]
    assert _artifact_references(sanitized) == set()


def test_shared_report_memories_exclude_prior_operation_claims_entirely():
    current, excluded, source_operations = _current_operation_report_memories(
        [
            {"id": "current", "memory": "current evidence", "metadata": {"operation_id": "OP_CURRENT"}},
            {"id": "prior", "memory": "stale version claim", "metadata": {"operation_id": "OP_PRIOR"}},
            {"id": "unknown", "memory": "unattributed claim", "metadata": {}},
        ],
        "OP_CURRENT",
    )

    assert [item["id"] for item in current] == ["current"]
    assert excluded == 2
    assert source_operations == {"OP_PRIOR", "unknown prior operation"}


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


def test_finding_taxonomy_mapping_ids_link_to_catalog_urls():
    content = _format_taxonomy_mappings(
        {
            "cwe": [
                {
                    "id": "CWE-79",
                    "name": "Improper Neutralization",
                    "confidence_band": "high",
                    "confidence": 0.95,
                    "basis": "finding terminology",
                    "rationale": "Cross-site scripting evidence",
                    "evidence": ["artifact:proof.txt"],
                    "url": "https://cwe.mitre.org/data/definitions/79.html",
                }
            ],
            "mitre_attack": [
                {
                    "id": "T1190",
                    "name": "Exploit Public-Facing Application",
                    "confidence_band": "medium",
                    "confidence": 0.8,
                    "basis": "execution trace",
                    "rationale": "Observed exploit path",
                    "evidence": ["artifact:trace.txt"],
                    "url": "https://attack.mitre.org/techniques/T1190/",
                }
            ],
        }
    )

    assert "[CWE-79](https://cwe.mitre.org/data/definitions/79.html)" in content
    assert "[T1190](https://attack.mitre.org/techniques/T1190/)" in content


def test_finding_taxonomy_mapping_without_url_remains_plain_text():
    content = _format_taxonomy_mappings(
        {
            "cwe": [
                {
                    "id": "CWE-89",
                    "name": "SQL Injection",
                    "confidence_band": "medium",
                    "confidence": 0.7,
                    "basis": "finding terminology",
                    "rationale": "Database input evidence",
                    "evidence": [],
                }
            ],
            "mitre_attack": [],
        }
    )

    assert "| CWE-89 |" in content
    assert "| [CWE-89]" not in content
    assert "| Unavailable |" in content


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


def test_operation_log_parser_filters_bookkeeping_and_extracts_shell_commands(tmp_path):
    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-02 11:00:00",
                event({"type": "tool_start", "tool_name": "create_tasks"}),
                event(
                    {
                        "type": "tool_start",
                        "tool_name": "shell",
                        "tool_id": "shell-1",
                "tool_input": {"command": ["curl -I https://example.test", "python -V"]},
                    }
                ),
                event(
                    {
                        "type": "tool_input_corrected",
                        "tool_name": "shell",
                        "tool_id": "shell-1",
                        "tool_input": {"command": "curl -s https://example.test"},
                    }
                ),
                event(
                    {
                        "type": "tool_start",
                        "tool_name": "shell",
                        "tool_input": {"command": "sudo curl -s https://example.test && env FOO=1 nmap -sV target | grep nmap"},
                    }
                ),
                event({"type": "tool_start", "tool_name": "store_observation"}),
                event({"type": "tool_start", "tool_name": "http_request"}),
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["tools_used"] == ["create_tasks", "shell", "shell", "store_observation", "http_request"]
    assert parsed["reportable_tools_used"] == ["shell", "http_request", "curl", "nmap"]


def test_operation_log_parser_silently_rejects_malformed_shell_executable_tokens(tmp_path):
    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-02 11:00:00",
                event(
                    {
                        "type": "tool_start",
                        "tool_name": "shell",
                        "tool_input": {
                            "command": "FOO=1 timeout 30 sqlmap -u target; curl -s target; id\"; search?x=1"
                        },
                    }
                ),
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["reportable_tools_used"] == ["shell", "sqlmap", "curl"]


def test_operation_log_parser_uses_execution_termination_timestamp_for_duration(tmp_path):
    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-02T11:00:00",
                event({"type": "metrics_update", "metrics": {"duration": "9m"}}),
                event({"type": "operation_terminated", "timestamp": "2026-01-02T11:30:00"}),
                event({"type": "operation_finalized", "timestamp": "2026-01-02T11:45:30"}),
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["metrics"]["duration"] == "30m 0s"


def test_latest_operation_log_parser_uses_assessment_model_usage_snapshot(tmp_path):
    usage = [
        {
            "provider": "litellm",
            "model": "example-model",
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "cost": 0.04,
            "inference_time_ms": 250,
            "context_window_tokens": 48000,
            "efficiency": 100.0,
            "model_calls": 2,
            "correction_loops": 0,
        }
    ]
    snapshot = {
        "type": "model_usage_snapshot",
        "operation_id": "OP_USAGE",
        "stage": "assessment_complete",
        "metrics": {"modelUsage": usage},
    }
    later_metrics = {
        "type": "metrics_update",
        "metrics": {"modelUsage": [{"model": "report-agent"}]},
    }
    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-02 11:00:00",
                "Operation OP_USAGE initiated",
                f"__CYBER_EVENT__{json.dumps(snapshot)}__CYBER_EVENT_END__",
                f"__CYBER_EVENT__{json.dumps(later_metrics)}__CYBER_EVENT_END__",
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["metrics"]["model_usage"] == usage


def test_operation_log_parser_uses_execution_session_before_report_only_session(tmp_path):
    usage = [
        {
            "provider": "ollama",
            "model": "assessment-model",
            "input_tokens": 300,
            "output_tokens": 100,
            "total_tokens": 400,
            "cost": 0.0,
            "inference_time_ms": 900,
            "model_calls": 4,
            "correction_loops": 1,
        }
    ]

    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    execution_init = {
        "type": "operation_init",
        "operation_id": "OP_HISTORY",
        "operation_mode": "execution",
        "budget": {"maxDurationMinutes": 180, "maxTokens": None, "maxCost": None},
    }
    execution_snapshot = {
        "type": "model_usage_snapshot",
        "metrics": {
            "modelUsage": usage,
            "inputTokens": 300,
            "outputTokens": 100,
            "totalTokens": 400,
            "duration": "25m",
        },
    }
    report_only_init = {
        "type": "operation_init",
        "operation_id": "OP_HISTORY",
        "operation_mode": "report_only",
        "budget": {"maxDurationMinutes": 90, "maxTokens": None, "maxCost": None},
    }
    report_only_metrics = {
        "type": "metrics_update",
        "metrics": {"totalTokens": 0, "duration": "0s", "budget": report_only_init["budget"]},
    }
    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text(
        "\n".join(
            [
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-01 10:00:00",
                event(execution_init),
                event({"type": "tool_start", "tool_name": "shell"}),
                event({"type": "termination_reason", "reason": "partial_failure", "message": "Phase 4 failed"}),
                event(execution_snapshot),
                "CYBER-AUTOAGENT SESSION STARTED: 2026-01-01 11:00:00",
                event(report_only_init),
                event(report_only_metrics),
                event({"type": "model_usage_snapshot", "metrics": {"modelUsage": []}}),
            ]
        )
    )

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["operation_mode"] == "execution"
    assert parsed["configured_budget"] == {"duration": 180}
    assert parsed["termination_reason"] == "partial_failure"
    assert parsed["termination_message"] == "Phase 4 failed"
    assert parsed["tools_used"] == ["shell"]
    assert parsed["reportable_tools_used"] == ["shell"]
    assert parsed["metrics"]["duration"] == "25m"
    assert parsed["metrics"]["model_usage"] == usage


def test_operation_log_parser_aggregates_execution_continuations_before_report_only(tmp_path):
    def event(value):
        return f"__CYBER_EVENT__{json.dumps(value)}__CYBER_EVENT_END__"

    def execution_session(start, tool_name, usage, duration):
        return [
            f"CYBER-AUTOAGENT SESSION STARTED: {start}",
            event({"type": "operation_init", "operation_mode": "continuation"}),
            event({"type": "tool_start", "tool_name": tool_name}),
            event(
                {
                    "type": "model_usage_snapshot",
                    "metrics": {
                        "modelUsage": [usage],
                        "inputTokens": usage["input_tokens"],
                        "outputTokens": usage["output_tokens"],
                        "totalTokens": usage["total_tokens"],
                        "duration": duration,
                    },
                }
            ),
        ]

    usage_one = {
        "provider": "ollama",
        "model": "assessment-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "inference_time_ms": 100,
        "model_calls": 1,
        "correction_loops": 0,
    }
    usage_two = {**usage_one, "input_tokens": 40, "output_tokens": 10, "total_tokens": 50, "model_calls": 1}
    lines = execution_session("2026-01-01 10:00:00", "shell", usage_one, "10m")
    lines.extend(execution_session("2026-01-01 11:00:00", "read_artifact", usage_two, "5m"))
    lines.extend(
        [
            "CYBER-AUTOAGENT SESSION STARTED: 2026-01-01 12:00:00",
            event({"type": "operation_init", "operation_mode": "report_only"}),
            event({"type": "model_usage_snapshot", "metrics": {"modelUsage": []}}),
        ]
    )
    log_path = tmp_path / "cyber_operations.log"
    log_path.write_text("\n".join(lines))

    parsed = _parse_latest_operation_log(str(log_path))

    assert parsed["tools_used"] == ["shell", "read_artifact"]
    assert parsed["reportable_tools_used"] == ["shell"]
    assert parsed["metrics"]["total_tokens"] == 170
    assert parsed["metrics"]["duration"] == "15m 0s"
    assert len(parsed["metrics"]["model_usage"]) == 1
    row = parsed["metrics"]["model_usage"][0]
    assert row["provider"] == "ollama"
    assert row["model"] == "assessment-model"
    assert row["input_tokens"] == 140
    assert row["output_tokens"] == 30
    assert row["total_tokens"] == 170
    assert row["model_calls"] == 2
    assert row["efficiency"] == 100.0


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
    normalized_lower = _validate_next_steps(lower_than_configured, configured)
    assert normalized_lower["budget_recommendations"][0] == {
        "dimension": "duration",
        "current": 60,
        "recommended": 30,
        "rationale": "Estimated from the remaining coverage.",
    }

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
    assert duration["recommended"] == 120
    assert "additional continuation budget" in duration["rationale"]


def test_next_steps_accepts_budget_recommendation_below_configured_limit():
    configured = {"duration": 60, "cost": 4.0}
    data = _next_steps_data(configured)
    data["budget_recommendations"][0]["recommended"] = 30
    data["budget_recommendations"][1]["recommended"] = 2.0

    normalized = _validate_next_steps(data, configured)

    assert [item["recommended"] for item in normalized["budget_recommendations"]] == [30, 2.0]


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
    ) == "validation_failure"


def test_summary_table_preserves_full_finding_location():
    location = "http://host.docker.internal:4280/dvwa/vulnerabilities/xss_rce/?name=proof"
    title = "User Enumeration and Lack of Rate Limiting on /vulnerabilities/brute"

    table = _format_summary_table(
        [{"severity": "HIGH", "parsed": {"vulnerability": title, "where": location}}]
    )

    assert title in table
    assert "http://host.docker.internal:4280/dvwa/vulnerabilities/xss\\_rce/?name=proof" in table
    assert _normalize_report_category(
        "finding",
        {"status": "verified", "proof_pack": "legacy"},
        "Control case without an artifact",
        {"evidence": "/tmp/proof.txt"},
    ) == "validation_failure"


@pytest.mark.parametrize(
    ("artifact_content", "expected_category"),
    [
        ('{"user":"Leaf"}', "validation_failure"),
        ('{"user":{"id":1,"details":{"role":"admin"}}}', "finding"),
    ],
)
def test_report_rechecks_nested_json_claim_shape(tmp_path, monkeypatch, artifact_content, expected_category):
    artifact = tmp_path / "response.json"
    artifact.write_text(artifact_content, encoding="utf-8")
    reference = "artifact:artifacts/response.json"
    assertion = {"artifact": reference, "type": "literal_text", "value": '"user"'}
    metadata = {
        "validation_status": "verified",
        "evidence_strategy": "direct",
        "artifacts": [reference],
        "evidence_artifacts": [reference],
        "candidate_evidence_assertions": [assertion],
        "evidence_assertions": [assertion],
        "evidence_artifact_fingerprints": {reference: hashlib.sha256(artifact.read_bytes()).hexdigest()},
        "title": "Endpoint returns nested JSON structure",
        "claim": "The endpoint returns a nested JSON object containing user details.",
        "technique": "Information Disclosure",
    }
    monkeypatch.setattr(report_generator_module, "_artifact_path_from_ref", lambda _reference: str(artifact))
    monkeypatch.setattr("modules.tools.memory._artifact_path_from_ref", lambda _reference: str(artifact))

    assert _normalize_report_category("finding", metadata, "", {}) == expected_category


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


def test_format_target_coverage_reconstructs_targets_from_task_ids():
    tasks = [
        Task(
            "task-1",
            "Check target",
            "Check target",
            make_acceptance("task-1"),
            1,
            "done",
            target_scope="subset",
            target_ids=["target-1"],
        )
    ]

    coverage = _format_target_coverage(
        None,
        tasks,
        [{"category": "finding", "metadata": {"target": "http://target.test"}}],
        {"target-1": "http://target.test"},
    )

    assert "| target-1 | network | `http://target.test` | 1 | 1 | 0 |" in coverage


def test_format_target_coverage_counts_child_endpoint_under_registered_target():
    plan = OperationPlan(
        objective="Assess target",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="done")],
        targets=[OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    tasks = [
        Task("task-1", "Check endpoint", "Check endpoint", make_acceptance("task-1"), 1, "done"),
    ]
    evidence = [
        {
            "category": "finding",
            "metadata": {"target": "http://target.test/login.php"},
            "content": "confirmed",
        },
        {
            "category": "finding",
            "metadata": {"target": "http://target.test/account.php"},
            "content": "confirmed",
        },
    ]

    coverage = _format_target_coverage(plan, tasks, evidence)

    assert "| target-1 | network | `http://target.test` | 1 | 2 | 0 |" in coverage


def test_report_consistency_reconciles_derived_counts_and_incomplete_coverage():
    sections = {
        "raw_evidence": [
            {"category": "finding"},
            {"category": "validation_failure"},
        ],
        "verified_findings_total": 9,
        "finding_count": 9,
        "validation_failure_count": 0,
        "finding_validation_failure_count": 0,
        "task_status_counts": {"done": 1, "pending": 1},
        "total_task_count": 7,
        "completed_task_count": 4,
        "phase_coverage": [
            {"phase_id": 1, "status": "done", "task_status_counts": {"done": 1}},
            {"phase_id": 2, "status": "partial_failure", "task_status_counts": {"pending": 1}},
        ],
        "evidence_integrity_errors": [{"reference": "artifact:missing.txt"}],
    }
    completion = {"assessment_complete": True, "workflow_complete": True}

    errors = _validate_report_consistency(sections, completion)

    assert sections["verified_findings_total"] == 1
    assert sections["validation_failure_count"] == 1
    assert sections["total_task_count"] == 2
    assert sections["completed_task_count"] == 1
    assert completion["assessment_complete"] is False
    assert completion["incomplete_phase_ids"] == [2]
    assert len(errors) == 6
    warning = _format_report_consistency_warnings(errors)
    assert "Report Consistency Warnings" in warning
    assert "artifact:missing.txt" in warning


def test_report_consistency_accepts_matching_canonical_state():
    sections = {
        "raw_evidence": [{"category": "finding"}],
        "verified_findings_total": 1,
        "finding_count": 1,
        "validation_failure_count": 0,
        "finding_validation_failure_count": 0,
        "task_status_counts": {"done": 1},
        "total_task_count": 1,
        "completed_task_count": 1,
        "phase_coverage": [{"phase_id": 1, "status": "done", "task_status_counts": {"done": 1}}],
        "evidence_integrity_errors": [],
    }
    completion = {"assessment_complete": True, "workflow_complete": True}

    assert _validate_report_consistency(sections, completion) == []
    assert _format_report_consistency_warnings([]) == ""


def test_report_consistency_reports_omitted_shared_memory_artifacts_as_one_warning():
    sections = {
        "raw_evidence": [],
        "verified_findings_total": 0,
        "finding_count": 0,
        "validation_failure_count": 0,
        "finding_validation_failure_count": 0,
        "task_status_counts": {},
        "total_task_count": 0,
        "completed_task_count": 0,
        "phase_coverage": [],
        "evidence_integrity_errors": [
            {
                "kind": "cross_operation_artifact_refs_omitted",
                "count": 2,
                "source_operations": ["OP_20260813_161308"],
            }
        ],
    }
    completion = {"assessment_complete": True, "workflow_complete": True}

    errors = _validate_report_consistency(sections, completion)

    assert errors == [
        "Excluded 2 artifact reference(s) from shared-memory evidence originating in prior operation(s): "
        "OP_20260813_161308."
    ]


def test_report_consistency_reports_excluded_advisory_memories():
    sections = {
        "raw_evidence": [],
        "verified_findings_total": 0,
        "finding_count": 0,
        "validation_failure_count": 0,
        "finding_validation_failure_count": 0,
        "task_status_counts": {},
        "total_task_count": 0,
        "completed_task_count": 0,
        "phase_coverage": [],
        "evidence_integrity_errors": [{
            "kind": "cross_operation_advisory_memories_excluded",
            "count": 3,
            "source_operations": ["OP_PRIOR"],
        }],
    }

    errors = _validate_report_consistency(
        sections,
        {"assessment_complete": True, "workflow_complete": True},
    )

    assert errors == [
        "Excluded 3 advisory shared-memory record(s) from current-operation report evidence: OP_PRIOR."
    ]


def test_report_consistency_counts_superseded_tasks_as_recovered_completion():
    sections = {
        "raw_evidence": [],
        "verified_findings_total": 0,
        "finding_count": 0,
        "validation_failure_count": 0,
        "finding_validation_failure_count": 0,
        "task_status_counts": {"done": 1, "superseded": 1},
        "total_task_count": 2,
        "completed_task_count": 1,
        "phase_coverage": [{
            "phase_id": 1,
            "status": "done",
            "task_status_counts": {"done": 1, "superseded": 1},
        }],
        "evidence_integrity_errors": [],
    }
    completion = {"assessment_complete": True, "workflow_complete": True}

    errors = _validate_report_consistency(sections, completion)
    canonical = _canonical_report_data(sections)

    assert errors == ["Completed task count did not match successful terminal task statuses."]
    assert sections["completed_task_count"] == 2
    assert sections["superseded_task_count"] == 1
    assert canonical["completed_task_count"] == 2
    assert canonical["superseded_task_count"] == 1


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
    proof_ref = str(tmp_path / "proof.txt")
    proof_assertion = {"artifact": proof_ref, "marker": "proof"}
    mock_client.list_memories.return_value[0]["metadata"].update(
        {
            "artifacts": [proof_ref],
            "candidate_evidence_assertions": [proof_assertion],
            "evidence_assertions": [proof_assertion],
            "evidence_artifact_fingerprints": {
                proof_ref: hashlib.sha256((tmp_path / "proof.txt").read_bytes()).hexdigest()
            },
        }
    )
    monkeypatch.setattr(report_generator_module, "_artifact_path_from_ref", lambda _reference: proof_ref)

    # Run build_report_sections
    sections = build_report_sections(op_id, "example.com", "Test Objective")

    mock_client.get_active_plan.assert_called_once_with(operation_id=op_id)
    mock_client.list_tasks.assert_called_once_with(operation_id=op_id)
    mock_client.list_finding_records.assert_called_once_with(operation_id=op_id)

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
    assert content.startswith("# SECURITY ASSESSMENT REPORT\n\n"+disclaimer)
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
    assert "- Software: cyber-autoagent v0.10.0" in content
    metrics_heading = "### Execution Metrics"
    assert content.count(metrics_heading) == 1
    assert content.index("## APPENDIX A: ASSESSMENT METHODOLOGY") < content.index(metrics_heading)
    assert content.index(metrics_heading) < content.index("Total Operation Time: N/A")
    assert content.index("Total Operation Time: N/A") < content.index("| Capture Timestamp | Provider | Model |")
    assert content.index(metrics_heading) < content.index("- Report Generated:")
    assert "| N/A | test_provider | test_model | N/A | 0 | 0 | 0 | 0 | 0 | $0.000000 | N/A | N/A |" in content
    assert content.count("*Efficiency = 100 × model inferences") == 1
    assert (
        content.index("- Report Generated:")
        < content.index("- Software:")
        < content.index("- Operation ID:")
    )
    assert content.index("Total Operation Time: N/A") < content.index("- Report Generated:")

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
    assert (output_dir / "report_execution_history.md").exists()
    assert (output_dir / "report_methodology.md").exists()
    assert (output_dir / "report_recommended_next_steps.md").exists()
    assert "## APPENDIX A: ASSESSMENT METHODOLOGY" in content
    assert "### Model & Agent Parameter Adjustments" in content
    assert content.index("### Methodology Limitations") < content.index("### Model & Agent Parameter Adjustments")
    assert "## APPENDIX C: MODEL & AGENT PARAMETER ADJUSTMENTS" not in content
    assert "appendix-c-model-agent-parameter-adjustments" not in content
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


def test_deterministic_fallback_report_renders_canonical_sections_without_narrative(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.get_provider.return_value = "ollama"
    mock_config.get_llm_config.return_value.model_id = "test-model"
    monkeypatch.setattr(report_generator_module, "get_config_manager", lambda: mock_config)
    monkeypatch.setattr(report_generator_module, "get_output_path", lambda **_kwargs: str(tmp_path))
    monkeypatch.setattr(report_generator_module, "list_persisted_operation_model_metrics", lambda _operation_id: [])
    monkeypatch.setattr(
        report_generator_module,
        "build_report_sections",
        lambda **_kwargs: {
            "operation_id": "OP_FALLBACK",
            "target": "https://example.test",
            "objective": "Assess the target",
            "module": "web",
            "raw_evidence": [
                {
                    "id": "finding-1",
                    "title": "Stored XSS",
                    "category": "finding",
                    "severity": "HIGH",
                    "content": "Verified script execution.",
                    "metadata": {"artifacts": ["artifact:artifacts/proof.txt"]},
                },
                {
                    "id": "candidate-1",
                    "title": "Possible SQL injection",
                    "category": "validation_failure",
                    "content": "Candidate was not reproduced.",
                    "metadata": {"validation_reason": "No decisive evidence."},
                },
                {
                    "id": "observation-1",
                    "title": "Server banner",
                    "category": "observation",
                    "content": "Server disclosed its version.",
                    "metadata": {"target": "https://example.test"},
                },
            ],
            "severity_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
            "verified_findings_total": 1,
            "validation_failure_count": 1,
            "task_status_counts": {"done": 1, "partial_failure": 1},
            "total_task_count": 2,
            "completed_task_count": 1,
            "phase_coverage": [{"phase_id": 1, "status": "partial_failure", "task_status_counts": {"done": 1, "partial_failure": 1}}],
            "target_coverage": "| Target | Coverage |\n|---|---|\n| example.test | partial |",
            "execution_history": "## EXECUTION HISTORY\n\nRecorded task history.",
            "summary_table": "| Severity | Count |\n|---|---:|\n| HIGH | 1 |",
            "latest_run": {"metrics": {"duration": "2m"}, "configured_budget": {"duration": 60}},
            "reportable_tools_used": ["nmap"],
        },
    )

    result = generate_deterministic_fallback_report(
        target="https://example.test",
        objective="Assess the target",
        operation_id="OP_FALLBACK",
        config_params={"completion_status": {"assessment_complete": False, "termination_reason": "budget_limit"}},
        error=RuntimeError("report agent unavailable"),
    )

    markdown = (tmp_path / "security_assessment_report.md").read_text()
    payload = json.loads((tmp_path / "security_assessment_report.json").read_text())
    assert result["status"] == "fallback"
    assert "Deterministic fallback report" in markdown
    assert "Stored XSS" in markdown
    assert "Possible SQL injection" in markdown
    assert "Server banner" in markdown
    assert "## TARGET COVERAGE" in markdown
    assert "## EXECUTION HISTORY" in markdown
    assert "## APPENDIX A: ASSESSMENT METHODOLOGY" in markdown
    assert "## APPENDIX B: RECOMMENDED NEXT STEPS" in markdown
    assert "### Execution Metrics" in markdown
    assert "### Model & Agent Parameter Adjustments" in markdown
    assert markdown.index("### Execution Metrics") < markdown.index("### Model & Agent Parameter Adjustments")
    assert "## APPENDIX C: MODEL & AGENT PARAMETER ADJUSTMENTS" not in markdown
    assert "model-authored methodology prose" in markdown
    assert "AI-Generated Content Disclaimer" not in markdown
    assert "report agent unavailable" in markdown
    assert payload["report_status"] == "fallback"
    assert payload["narrative"] == {}
    assert payload["canonical"]["verified_findings_total"] == 1


def test_fallback_report_uses_controller_snapshot_when_store_sections_fail(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.handlers.report_generator.get_output_path", lambda **_kwargs: str(tmp_path))
    monkeypatch.setattr(
        "modules.handlers.report_generator.build_report_sections",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("disk I/O error")),
    )

    result = generate_deterministic_fallback_report(
        target="https://example.test",
        objective="Assess the target",
        operation_id="OP_SNAPSHOT",
        config_params={
            "operation_state_snapshot": {
                "plan": {"phases": [{"id": 1, "title": "Recon", "status": "done"}]},
                "tasks": [{"phase": 1, "title": "Recon", "status": "done", "target_ids": ["target-1"]}],
                "findings": [],
            },
            "completion_status": {"assessment_complete": False, "termination_reason": "error"},
        },
        error=OSError("disk I/O error"),
    )

    assert result["status"] == "fallback"
    assert "| 1 | Recon | done |" in result["content"]


def test_sanitize_mermaid_diagrams_quotes_supported_node_and_edge_labels():
    source = """```mermaid
graph TD
A((double \"quote))
B(single)
C[square]
D{brace}
E>angle]
A -- edge label --> B
A -->|pipe label| B
Alice->>Bob: sequence label
subgraph group label
end
```"""

    rendered = report_generator_module._sanitize_mermaid_diagrams(source)

    assert 'A(("double &#34;quote"))' in rendered
    assert 'B("single")' in rendered
    assert 'C["square"]' in rendered
    assert 'D{"brace"}' in rendered
    assert 'E>"angle"]' in rendered
    assert '-- "edge label" -->' in rendered
    assert '|"pipe label"|' in rendered
    assert 'Alice->>Bob: "sequence label"' in rendered
    assert 'subgraph "group label"' in rendered
    assert report_generator_module._sanitize_mermaid_diagrams("No diagram") == "No diagram"


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
        config_params={"tools_used": ["nmap"]},
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
        "methodology",
        "next_steps",
    ]
    assert all(event["operation_stage"] == "final_report" for event in progress_events)
    assert progress_events[1]["report_step_label"] == "Finding: High Finding"
    assert progress_events[-1]["report_step_label"] == "Appendix B: Recommended next steps"
    callback_handler.set_report_items.assert_called_once_with(
        mock_build_sections.return_value["raw_evidence"],
        refinement_cycles=0,
    )
    assert len(created_agents) == 5
    assert len({id(agent) for agent in created_agents}) == 5
    assert all(agent.cleanup.call_count == 1 for agent in created_agents)
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
    mock_config.getenv_int.side_effect = (
        lambda name, default: 0 if name == "CYBER_REPORT_REFINEMENT_CYCLES" else default
    )
    mock_get_config.return_value = mock_config

    mock_build_sections.return_value = {
        "evidence_count": 1,
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
    mock_agent.return_value.message = {"content": [{"text": "Generated section"}]}

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
    assert "Observation content" in content
    assert "Generated section" in content
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
