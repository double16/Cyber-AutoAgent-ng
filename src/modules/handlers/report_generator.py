    #!/usr/bin/env python3
"""
Report Generation Handler Utility for Cyber-AutoAgent

This module provides report generation functionality.

This is NOT a Strands tool - it's a handler utility function.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.agents.report_agent import ReportGenerator
from modules.config import get_config_manager
from modules.config.system.logger import get_logger
from modules.handlers.utils import duration_max, get_output_path, sanitize_target_name
from modules.prompts.factory import (
    _extract_domain_lens,
    _transform_evidence_to_content,
    format_evidence_for_report,
    format_tools_summary,
    generate_findings_summary_table,
    get_report_appendix_system_prompt,
    get_report_executive_system_prompt,
    get_report_finding_system_prompt,
    get_report_observation_system_prompt,
    safe_truncate,
)
from modules.tools.memory import Task, _artifact_path_from_ref, get_memory_client, memory_is_cross_operation

logger = get_logger("Handlers.ReportGenerator")

MAX_REPORT_FINDINGS = int(os.getenv("CYBER_REPORT_MAX_FINDINGS", "200"))
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
_PAGE_BREAK = """\n<div class="page-break" style="page-break-before: always;"></div>\n\n"""
_ARTIFACT_REFERENCE = re.compile(
    r"(?:artifact:(?:artifacts/)?[^\s\"'\])]+|"
    r"(?:^|[\s\"'\[(])(?:/[^\s\"'\])]+|(?:artifacts?|outputs?)/[^\s\"'\])]+))",
    re.IGNORECASE,
)


def _normalize_completion_status(value: Any) -> Dict[str, Any]:
    """Return a stable report completion status block."""
    if not isinstance(value, dict):
        return {
            "assessment_complete": True,
            "workflow_complete": True,
            "termination_reason": "complete",
            "termination_message": None,
            "incomplete_reason": None,
            "unresolved_task_count": 0,
            "incomplete_phase_ids": [],
        }

    assessment_complete = bool(value.get("assessment_complete"))
    workflow_complete = bool(value.get("workflow_complete"))
    termination_reason = value.get("termination_reason")
    termination_message = value.get("termination_message")
    incomplete_reason = value.get("incomplete_reason")
    unresolved_task_count = value.get("unresolved_task_count")
    incomplete_phase_ids = value.get("incomplete_phase_ids")
    if assessment_complete:
        incomplete_reason = None
    elif not incomplete_reason:
        incomplete_reason = "Workflow ended before assessment completion."

    return {
        "assessment_complete": assessment_complete,
        "workflow_complete": workflow_complete,
        "termination_reason": str(termination_reason) if termination_reason is not None else None,
        "termination_message": str(termination_message) if termination_message is not None else None,
        "incomplete_reason": str(incomplete_reason) if incomplete_reason is not None else None,
        "unresolved_task_count": max(0, int(unresolved_task_count or 0)),
        "incomplete_phase_ids": [int(phase_id) for phase_id in (incomplete_phase_ids or [])],
    }


def _completion_status_guidance(completion_status: Dict[str, Any]) -> str:
    """Prompt guidance that prevents complete-run claims for partial assessments."""
    if completion_status.get("assessment_complete"):
        return (
            "Assessment status: complete. The workflow marked assessment_complete=true and terminated with "
            "reason=complete."
        )

    return (
        "Assessment status: incomplete. Treat this report as a partial assessment. Do not claim all planned tasks "
        "were completed, do not claim the target is free of vulnerabilities, and do not interpret missing verified "
        "findings as proof of absence. Explicitly state that findings, observations, coverage, and validation "
        f"counts may be partial. Completion status data: {json.dumps(completion_status, sort_keys=True)}"
    )


def _completion_status_notice(completion_status: Dict[str, Any]) -> str:
    """Deterministic report notice for incomplete assessments."""
    if completion_status.get("assessment_complete"):
        return ""

    reason = completion_status.get("termination_reason") or "unknown"
    message = completion_status.get("termination_message")
    incomplete_reason = completion_status.get("incomplete_reason") or "Workflow ended before assessment completion."
    unresolved_task_count = int(completion_status.get("unresolved_task_count") or 0)
    incomplete_phase_ids = completion_status.get("incomplete_phase_ids") or []
    message_line = f"> Termination message: {message}\n" if message else ""
    coverage_line = ""
    if unresolved_task_count:
        phase_text = ", ".join(str(phase_id) for phase_id in incomplete_phase_ids) or "unknown"
        coverage_line = (
            f"> Unresolved actionable tasks: {unresolved_task_count} across phase(s) {phase_text}.\n"
        )
    return (
        "> **Assessment Status: Incomplete**\n"
        ">\n"
        f"> {incomplete_reason}\n"
        f"> Termination reason: `{reason}`.\n"
        f"{message_line}"
        f"{coverage_line}"
        "> Findings, observations, validation counts, and coverage in this report are partial. "
        "Do not interpret the absence of verified findings as absence of vulnerabilities.\n\n"
    )


def _report_item_title(item: Dict[str, Any], default: str) -> str:
    """Return a compact title for report progress labels."""
    parsed = item.get("parsed", {}) if isinstance(item.get("parsed"), dict) else {}
    title = (
        item.get("title")
        or parsed.get("title")
        or parsed.get("vulnerability")
        or item.get("content")
        or default
    )
    return safe_truncate(str(title).strip() or default, 80)


def _has_artifact_reference(value: Any) -> bool:
    """Return whether free-form evidence text contains an artifact-like path."""

    if isinstance(value, str):
        return bool(_ARTIFACT_REFERENCE.search(value))
    if isinstance(value, dict):
        return any(_has_artifact_reference(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_artifact_reference(item) for item in value)
    return False


def _artifact_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, str):
        references.update(match.strip(".,;:") for match in _ARTIFACT_REFERENCE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            references.update(_artifact_references(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            references.update(_artifact_references(item))
    return references


def _ground_report_item(text: str, item: Dict[str, Any], *, observation: bool = False) -> str:
    """Reject report prose that cites artifact paths absent from the supplied record."""

    allowed = _artifact_references(item)
    cited = _artifact_references(text)
    if cited.issubset(allowed):
        return text
    metadata = item.get("metadata", {}) or {}
    title = _report_item_title(item, "Observation" if observation else "Finding")
    artifacts = sorted(allowed)
    artifact_text = "\n".join(f"- `{path}`" for path in artifacts) or "- No artifact supplied."
    if observation:
        return f"### {title}\n\n**Evidence:**\n{artifact_text}\n\n{item.get('content', '')}"
    return (
        f"### {title}\n\n"
        f"**Severity:** {metadata.get('severity', item.get('severity', 'Unknown'))}\n\n"
        f"**Evidence:**\n{artifact_text}\n\n"
        f"**Verified claim:** {item.get('content', '')}\n"
    )


def _normalize_report_category(
    category: Any,
    metadata: Dict[str, Any],
    content: str,
    parsed: Dict[str, str],
) -> str:
    """Enforce finding evidence requirements without mutating stored memory."""

    normalized = str(category or "").strip().lower()
    if normalized in {"signal", "observation", "discovery"}:
        return "observation"
    if normalized in {"finding_candidate", "validation_failure"}:
        return "validation_failure"
    if normalized in {"decision", "knowledge", "finding_validation"}:
        return ""
    if normalized != "finding":
        return normalized

    validation_status = str(
        metadata.get("validation_status") or metadata.get("status") or ""
    ).strip().lower()
    proof_pack = metadata.get("proof_pack") or {}
    durable_evidence = (
        _has_artifact_reference(metadata.get("artifacts"))
        or _has_artifact_reference(proof_pack.get("artifacts") if isinstance(proof_pack, dict) else None)
        or _has_artifact_reference(parsed.get("evidence", ""))
    )
    negative_control_fields = (
        metadata.get("negative_control"),
        metadata.get("negative_control_artifact"),
        metadata.get("negative_control_artifacts"),
        proof_pack.get("negative_control") if isinstance(proof_pack, dict) else None,
        proof_pack.get("negative_control_artifacts") if isinstance(proof_pack, dict) else None,
    )
    artifact_backed_control = any(
        _has_artifact_reference(value) for value in negative_control_fields
    )
    if not artifact_backed_control:
        lowered_content = content.lower()
        names_control = "negative control" in lowered_content or "control case" in lowered_content
        artifact_backed_control = names_control and _has_artifact_reference(content)

    evidence_strategy = str(metadata.get("evidence_strategy", "differential")).strip().lower()
    evidence_contract_met = durable_evidence and (
        evidence_strategy == "direct" or artifact_backed_control
    )
    if validation_status == "verified" and evidence_contract_met:
        return "finding"
    return "validation_failure"


def _emit_report_progress(
    callback_handler: Any,
    operation_id: str,
    index: int,
    total: int,
    kind: str,
    label: str,
) -> None:
    """Emit an indexed progress event for a single report agent call."""
    if not callback_handler or not hasattr(callback_handler, "emit_ui_event"):
        return

    try:
        if hasattr(callback_handler, "mark_report_step_started"):
            callback_handler.mark_report_step_started()
        callback_handler.emit_ui_event(
            {
                "type": "progress_update",
                "step": "REPORT_AGENT",
                "operation_stage": "final_report",
                "operation": operation_id,
                "report_step_index": index,
                "report_step_total": total,
                "report_step_kind": kind,
                "report_step_label": label,
            }
        )
    except Exception:
        logger.debug("Unable to emit report progress event", exc_info=True)


def _format_target_coverage(plan: Any, tasks: List[Any], evidence: List[Dict[str, Any]]) -> str:
    targets = list(getattr(plan, "targets", []) or [])
    if not targets:
        return "No executable target registry was recorded for this operation."

    lines = ["| Target ID | Type | Value | Tasks | Verified Findings | Pending/Failed Validation |"]
    lines.append("|---|---|---|---:|---:|---:|")
    for target in targets:
        target_id = target.target_id
        scoped_tasks = [
            task for task in tasks
            if getattr(task, "target_scope", "all") == "all" or target_id in getattr(task, "target_ids", [])
        ]
        verified = [
            item for item in evidence
            if item.get("category") == "finding"
            and (
                (item.get("metadata", {}) or {}).get("target") == target.value
                or target.value in str(item.get("content", ""))
            )
        ]
        validation_failures = [
            item for item in evidence
            if item.get("category") == "validation_failure"
            and (
                (item.get("metadata", {}) or {}).get("target") == target.value
                or target.value in str(item.get("content", ""))
            )
        ]
        lines.append(
            f"| {target_id} | {target.type} | `{target.value}` | {len(scoped_tasks)} | "
            f"{len(verified)} | {len(validation_failures)} |"
        )
    return "\n".join(lines)


class _ReportMetricsCallback:
    """Record report-agent metrics without streaming report-agent internals to the UI."""

    def __init__(self, callback_handler: Any) -> None:
        self.callback_handler = callback_handler

    def __call__(self, **kwargs: Any) -> None:
        handler = self.callback_handler
        if not handler or not hasattr(handler, "record_report_metrics"):
            return

        try:
            event_loop_metrics = kwargs.get("event_loop_metrics")
            agent_result = kwargs.get("result")
            if agent_result and hasattr(agent_result, "metrics"):
                event_loop_metrics = agent_result.metrics
            agent = kwargs.get("agent")
            if event_loop_metrics:
                handler.record_report_metrics(event_loop_metrics, agent=agent)
            elif agent and hasattr(agent, "event_loop_metrics"):
                usage = agent.event_loop_metrics.accumulated_usage
                if usage:
                    handler.record_report_metrics(agent.event_loop_metrics, agent=agent)
        except Exception:
            logger.debug("Unable to record report-agent metrics", exc_info=True)

def generate_security_report(
    target: str,
    objective: str,
    operation_id: str,
    config_params: Optional[Dict[str, Any]] = None,
    callback_handler = None,
    filename: Optional[str] = None,
) -> None:
    """
    Generate a comprehensive security assessment report based on the operation results.

    This function is called by handlers to create a professional penetration testing
    report by analyzing the evidence collected during the security assessment.
    It uses a specialized Report Agent with tools to generate a well-structured
    report with findings, recommendations, and risk assessments.

    Args:
        target: The target system that was assessed
        objective: The security assessment objective
        operation_id: The operation identifier
        config_params: additional config (steps_executed, tools_used, evidence, provider, model_id, module)
        callback_handler: Optional callback handler for agent events
        filename: Optional path to save the generated report. If not provided,
                  a default filename in the output directory will be used.

    Returns:
        None

    Example:
        generate_security_report(
            target="example.com",
            objective="Identify web application vulnerabilities",
            operation_id="OP_20240115_143022",
            config_data='{"steps_executed": 15, "tools_used": ["nmap", "nikto"], "provider": "bedrock"}',
            filename="/path/to/report.md"
        )
    """
    try:
        # Log the report generation request
        logger.info("Generating security report for operation: %s", operation_id)
        config_manager = get_config_manager()
        config_params = config_params or {}

        # Extract parameters with defaults
        steps_executed = config_params.get("steps_executed", 0)
        tools_used = config_params.get("tools_used", [])
        provider = config_params.get("provider", config_manager.get_provider())
        model_id = config_params.get("model_id")
        module = config_params.get("module")
        completion_status = _normalize_completion_status(config_params.get("completion_status"))

        sections = build_report_sections(
            operation_id=operation_id,
            target=target,
            objective=objective,
            module=module,
            steps_executed=steps_executed,
            tools_used=tools_used,
        )
        sections["completion_status"] = completion_status

        # these values may have been updated when building the report section
        steps_executed = max(steps_executed, sections.get("steps_executed", 0))

        # Validate evidence collection - skip report only if truly no memories
        if not sections or int(sections.get("evidence_count", 0)) == 0:
            logger.info(
                "No evidence/memories collected for operation %s - skipping report generation",
                operation_id,
            )
            return

        # Get module report prompt if available for domain guidance
        module_report_prompt = _get_module_report_prompt(module)
        try:
            from modules.prompts import get_module_loader  # Dynamic import required

            module_loader = get_module_loader()
            module_report_agent_executive_system_prompt = (
                module_loader.load_module_report_agent_executive_system_prompt(module) or ""
            )
            module_report_agent_finding_system_prompt = (
                module_loader.load_module_report_agent_finding_system_prompt(module) or ""
            )
            module_report_agent_observation_system_prompt = (
                module_loader.load_module_report_agent_observation_system_prompt(module) or ""
            )
            module_report_agent_appendix_system_prompt = (
                module_loader.load_module_report_agent_appendix_system_prompt(module) or ""
            )
        except Exception:
            module_report_agent_executive_system_prompt = ""
            module_report_agent_finding_system_prompt = ""
            module_report_agent_observation_system_prompt = ""
            module_report_agent_appendix_system_prompt = ""

        output_path = get_output_path(target_name=sanitize_target_name(target), operation_id=operation_id)
        
        # Store report data for processing by other means
        with open(os.path.join(output_path, "security_assessment_report.json"), "w") as f:
            f.write(json.dumps(sections, indent=2, sort_keys=True))

        module_str = module or "web"
        module_guidance = (
            module_report_prompt
            if module_report_prompt
            else "Apply general security assessment best practices focusing on common vulnerability patterns."
        )
        completion_guidance = _completion_status_guidance(completion_status)
        completion_notice = _completion_status_notice(completion_status)

        report_parts_files = []
        raw_findings = sections.get("raw_evidence", [])
        if callback_handler and hasattr(callback_handler, "set_report_items"):
            try:
                callback_handler.set_report_items(raw_findings)
            except Exception:
                logger.debug("Unable to set exact report item counts", exc_info=True)
        report_metrics_callback = _ReportMetricsCallback(callback_handler)
        report_findings = [
            (i, finding)
            for i, finding in enumerate(raw_findings)
            if finding.get("category") == "finding"
        ]
        report_observations = [
            (i, finding)
            for i, finding in enumerate(raw_findings)
            if finding.get("category") in ["signal", "observation", "discovery"]
        ]
        report_validation_failures = [
            (i, finding)
            for i, finding in enumerate(raw_findings)
            if finding.get("category") == "validation_failure"
        ]
        report_step_total = 2 + len(report_findings) + len(report_observations) + len(report_validation_failures)
        report_step_index = 0

        # Part 1: Executive Summary
        logger.info("Generating Executive Summary...")
        exec_agent = ReportGenerator.create_report_agent(
            provider=provider,
            model_id=model_id,
            operation_id=operation_id,
            target=target,
            callback_handler=report_metrics_callback,
            system_prompt=(
                get_report_executive_system_prompt()
                + "\n"
                + module_guidance
                + "\n"
                + completion_guidance
                + "\n"
                + module_report_agent_executive_system_prompt
            )
        )
        
        exec_prompt = f"""
Generate all the requested sections.
Target: {target}
Objective: {objective}
Module: {module_str}

Only verified findings may be counted as confirmed risk. If there are zero verified findings, do not label the target
as "low risk"; state that no findings were verified and list validation failures separately.
Attack chains that were not demonstrated end to end may appear only in a separately titled "Hypothetical Attack
Paths" section. Label every unsupported transition as a hypothesis, cite the verified findings supporting the chain,
and keep hypothetical impact out of verified risk counts and conclusions.
{completion_guidance}

Use the following data:
{json.dumps({k: sections.get(k) for k in ['overview', 'findings_table', 'risk_assessment', 'severity_counts', 'validation_failure_count', 'target_coverage', 'phase_coverage', 'completion_status']})}
"""
        report_step_index += 1
        _emit_report_progress(
            callback_handler,
            operation_id,
            report_step_index,
            report_step_total,
            "executive",
            "Executive summary",
        )
        exec_content = None
        try:
            exec_result = exec_agent(exec_prompt)
            exec_content = _extract_text_from_result(exec_result)
        finally:
            try:
                exec_agent.cleanup()
            except Exception as error:
                logger.warning("Unable to clean up report executive summary agent: %s", error)

        if exec_content:
            # Add anchor for Table of Contents
            exec_content = "<a name=\"executive-summary\"></a>\n" + exec_content
            exec_summary_file = os.path.join(output_path, "report_executive_summary.md")
            with open(exec_summary_file, "w") as f:
                f.write(exec_content)
            report_parts_files.append(exec_summary_file)

        # Part 2: Detailed Findings
        logger.info("Generating Detailed Findings...")
        findings_header = _PAGE_BREAK + "<a name=\"detailed-vulnerability-analysis\"></a>\n## DETAILED VULNERABILITY ANALYSIS\n\n"

        # Add summary table for remaining findings
        if sections.get("summary_table"):
            findings_header += "\n### Findings Summary\n\n" + sections.get("summary_table") + "\n\n"

        findings_header_file = os.path.join(output_path, "report_findings_header.md")
        with open(findings_header_file, "w") as f:
            f.write(findings_header)
        report_parts_files.append(findings_header_file)

        finding_agent = ReportGenerator.create_report_agent(
            provider=provider,
            model_id=model_id,
            operation_id=operation_id,
            target=target,
            callback_handler=report_metrics_callback,
            system_prompt=(
                get_report_finding_system_prompt()
                + "\n"
                + module_guidance
                + "\n"
                + completion_guidance
                + "\n"
                + module_report_agent_finding_system_prompt
            )
        )
        for i, finding in report_findings:
            logger.info(f"Generating report for finding {i+1}: {finding.get('content')}")

            finding_prompt = f"""
Generate a detailed report for the following finding.
Target: {target}
{completion_guidance}
Finding Data:
{json.dumps(finding)}
"""
            report_step_index += 1
            _emit_report_progress(
                callback_handler,
                operation_id,
                report_step_index,
                report_step_total,
                "finding",
                f"Finding: {_report_item_title(finding, f'Finding {i + 1}')}",
            )
            finding_result = finding_agent(finding_prompt)
            finding_text = _extract_text_from_result(finding_result)

            if finding_text:
                finding_text = _ground_report_item(finding_text, finding)
                finding_filename = f"finding_{i+1}_{sanitize_target_name(finding.get('title', 'finding')[:50])}.md"
                finding_path = os.path.join(output_path, finding_filename)
                with open(finding_path, "w") as f:
                    f.write(_PAGE_BREAK + finding_text + "\n\n")
                report_parts_files.append(finding_path)

        try:
            finding_agent.cleanup()
        except Exception as error:
            logger.warning("Unable to clean up report finding agent: %s", error)

        # Part 3: Findings Requiring Validation. This section is deterministic so an
        # unverified claim cannot gain invented evidence during report generation.
        if report_validation_failures:
            validation_header_file = os.path.join(output_path, "report_validation_failures_header.md")
            with open(validation_header_file, "w") as f:
                f.write(
                    _PAGE_BREAK
                    + '<a name="findings-requiring-validation"></a>\n'
                    + "## FINDINGS REQUIRING VALIDATION\n\n"
                    + "These claims were not verified by the evidence contract. They remain investigation items, "
                    + "not confirmed vulnerabilities.\n\n"
                )
            report_parts_files.append(validation_header_file)
            for i, item in report_validation_failures:
                report_step_index += 1
                title = _report_item_title(item, f"Validation item {i + 1}")
                metadata = item.get("metadata", {}) or {}
                reason = metadata.get("validation_reason") or "Verification was incomplete or evidence requirements failed."
                artifacts = metadata.get("artifacts") or metadata.get("evidence_artifacts") or []
                if not isinstance(artifacts, list):
                    artifacts = [artifacts]
                artifact_lines = "\n".join(f"- `{path}`" for path in artifacts if path) or "- No valid artifact was recorded."
                text = (
                    f"### {title}\n\n"
                    f"- **Claimed severity:** {metadata.get('claimed_severity') or metadata.get('severity') or 'Unknown'}\n"
                    f"- **Validation status:** {item.get('validation_status') or metadata.get('validation_status') or 'failed'}\n"
                    f"- **Why validation failed:** {reason}\n\n"
                    f"**Claim:** {item.get('content', '')}\n\n"
                    f"**Available artifacts:**\n{artifact_lines}\n\n"
                    "**Required follow-up:** Reproduce this claim in a dedicated task and capture decisive direct "
                    "evidence or a test/control comparison before treating it as a vulnerability.\n"
                )
                path = os.path.join(
                    output_path,
                    f"validation_failure_{i + 1}_{sanitize_target_name(title[:50])}.md",
                )
                with open(path, "w") as f:
                    f.write(_PAGE_BREAK + text + "\n")
                report_parts_files.append(path)
                _emit_report_progress(
                    callback_handler,
                    operation_id,
                    report_step_index,
                    report_step_total,
                    "validation_failure",
                    f"Requires validation: {title}",
                )

        # Part 4: Observations and Discoveries
        logger.info("Generating Observations and Discoveries...")
        observations_header = _PAGE_BREAK + "<a name=\"observations-and-discoveries\"></a>\n## OBSERVATIONS AND DISCOVERIES\n\n"
        has_observations = False

        # Pre-create observation parts list to only add header if there are observations
        observation_parts_files = []

        obs_agent = ReportGenerator.create_report_agent(
            provider=provider,
            model_id=model_id,
            operation_id=operation_id,
            target=target,
            callback_handler=report_metrics_callback,
            system_prompt=(
                get_report_observation_system_prompt()
                + "\n"
                + module_guidance
                + "\n"
                + completion_guidance
                + "\n"
                + module_report_agent_observation_system_prompt
            )
        )
        for i, finding in report_observations:
            has_observations = True
            logger.info(f"Generating report for observation {i+1}: {finding.get('content')}")

            obs_prompt = f"""
Generate a brief report for the following observation/discovery.
Target: {target}
{completion_guidance}
Observation Data:
{json.dumps(finding)}
"""
            report_step_index += 1
            _emit_report_progress(
                callback_handler,
                operation_id,
                report_step_index,
                report_step_total,
                "observation",
                f"Observation: {_report_item_title(finding, f'Observation {i + 1}')}",
            )
            obs_result = obs_agent(obs_prompt)
            obs_text = _extract_text_from_result(obs_result)

            if obs_text:
                obs_text = _ground_report_item(obs_text, finding, observation=True)
                obs_filename = f"observation_{i+1}_{sanitize_target_name(finding.get('title', 'observation')[:50])}.md"
                obs_path = os.path.join(output_path, obs_filename)
                with open(obs_path, "w") as f:
                    f.write(_PAGE_BREAK + obs_text + "\n\n")
                observation_parts_files.append(obs_path)

        try:
            obs_agent.cleanup()
        except Exception as error:
            logger.warning("Unable to clean up report observation agent: %s", error)

        if has_observations:
            observations_header_file = os.path.join(output_path, "report_observations_header.md")
            with open(observations_header_file, "w") as f:
                f.write(observations_header)
            report_parts_files.append(observations_header_file)
            report_parts_files.extend(observation_parts_files)

        target_coverage_file = os.path.join(output_path, "report_target_coverage.md")
        with open(target_coverage_file, "w") as f:
            f.write(
                _PAGE_BREAK
                + "<a name=\"target-coverage\"></a>\n"
                + "## Target Coverage\n\n"
                + str(sections.get("target_coverage") or "No target coverage data was recorded.")
                + "\n\n"
            )
        report_parts_files.append(target_coverage_file)

        # Part 5: Assessment Methodology
        logger.info("Generating Assessment Methodology...")
        appendix_agent = ReportGenerator.create_report_agent(
            provider=provider,
            model_id=model_id,
            operation_id=operation_id,
            target=target,
            callback_handler=report_metrics_callback,
            system_prompt=(
                get_report_appendix_system_prompt()
                + "\n"
                + module_guidance
                + "\n"
                + completion_guidance
                + "\n"
                + module_report_agent_appendix_system_prompt
            )
        )

        appendix_prompt = f"""
Generate all requested sections.
Target: {target}
Operation ID: {operation_id}
Steps Executed: {steps_executed}
{completion_guidance}

Use the following data:
{json.dumps({k: sections.get(k) for k in ['operation_plan', 'operation_tasks', 'phase_coverage', 'tools_summary', 'completion_status']})}
"""
        report_step_index += 1
        _emit_report_progress(
            callback_handler,
            operation_id,
            report_step_index,
            report_step_total,
            "methodology",
            "Assessment methodology",
        )

        appendix_content = None
        try:
            appendix_result = appendix_agent(appendix_prompt)
            appendix_content = _extract_text_from_result(appendix_result)
        except Exception as error:
            logger.warning("Unable to clean up report appendix agent: %s", error)

        if appendix_content:
            # Add anchor for Table of Contents
            appendix_content = _PAGE_BREAK + "<a name=\"assessment-methodology\"></a>\n" + appendix_content
            methodology_file = os.path.join(output_path, "report_methodology.md")
            with open(methodology_file, "w") as f:
                f.write(appendix_content)
            report_parts_files.append(methodology_file)

        # --- Combine everything ---
        if not filename:
            filename = os.path.join(output_path, "security_assessment_report.md")

        with open(filename, "w") as final_f:
            final_f.write("# SECURITY ASSESSMENT REPORT\n\n")
            final_f.write("## TABLE OF CONTENTS\n")
            final_f.write("- [Executive Summary](#executive-summary)\n")
            final_f.write("- [Detailed Vulnerability Analysis](#detailed-vulnerability-analysis)\n")
            final_f.write("- [Findings Requiring Validation](#findings-requiring-validation)\n")
            if has_observations:
                final_f.write("- [Observations and Discoveries](#observations-and-discoveries)\n")
            final_f.write("- [Target Coverage](#target-coverage)\n")
            final_f.write("- [Assessment Methodology](#assessment-methodology)\n\n")
            final_f.write(completion_notice)

            for part_file in report_parts_files:
                with open(part_file, "r") as part_f:
                    final_f.write(part_f.read())
                    final_f.write("\n\n")

            # Add footer
            main_provider = config_manager.get_provider()
            main_models = {config_manager.get_llm_config(main_provider).model_id,
                           config_manager.get_swarm_config(main_provider).llm.model_id}

            footer = f"""
----

- Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- Operation ID: {operation_id}
- Provider: {main_provider}
- Model(s): {", ".join(main_models)}
"""
            final_f.write(footer)

        logger.info("Final combined report generated: %s", filename)
        return

    except Exception as e:
        logger.error("Error generating security report: %s", e, exc_info=True)
        # Don't expose internal error details to user
        return


_RE_MARKDOWN_INDENTED_HEADER = re.compile(r"^[ \t]+(#+ )", re.MULTILINE)
_RE_MARKDOWN_TABLE_START = re.compile(r"([^\n])\n([ \t]*\|.*\|[ \t]*\n[ \t]*\|[ \t]*:?---)", re.MULTILINE)


def _extract_text_from_result(result: Any) -> str:
    """Extract text content from an agent result object and fix leading whitespace on headings and tables."""
    text = ""
    if result and hasattr(result, "message"):
        for block in result.message.get("content", []):
            if isinstance(block, dict) and "text" in block:
                text += block["text"]
    
    if not text:
        return text

    # Post-process mermaid diagrams to ensure node names/labels are quoted and sanitize special characters
    text = _sanitize_mermaid_diagrams(text)

    # Remove leading whitespace before markdown heading markers (#, ##, ...)
    text = _RE_MARKDOWN_INDENTED_HEADER.sub(r"\1", text)

    # Ensure markdown tables have an empty line before them
    text = _RE_MARKDOWN_TABLE_START.sub(r"\1\n\n\2", text)
    
    return text


_RE_MERMAID_DOUBLE_ROUNDED = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)\(\((?!")(.*?)(?<!")\)\)(?:\s|$|[-=])')
_RE_MERAID_SINGLE_ROUNDED = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)\((?!")(.*?)(?<!")\)(?:\s|$|[-=])')
_RE_MERMAID_SQUARE = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)\[(?!")(.*?)(?<!")\](?:\s|$|[-=])')
_RE_MERMAID_BRACES = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)\{(?!")(.*?)(?<!")\}(?:\s|$|[-=])')
_RE_MERMAID_ANGLE = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)>(?!")(.*?)(?<!")\](?:\s|$|[-=])')
_RE_MERMAID_EDGE = re.compile(r'(--\s+)(?!")(.*?)(?<!")(\s*-->)')
_RE_MERMAID_SEQUENCE_LABELS = re.compile(r'(->>[^:]+:\s*)(.*)')
_RE_MERMAID_PIPE_LABELS = re.compile(r'(\|)(?!")(.*?)(?<!")(\|)')
_RE_MERMAID_SUBGRAPH_LABEL = re.compile(r'(subgraph\s+)(.*)')
_RE_MERMAID_BLOCK = re.compile(r'```mermaid\s*([\s\S]*?)\s*```')


def _sanitize_mermaid_diagrams(text: str) -> str:
    """
    Post-process mermaid diagrams to ensure node names/labels are quoted
    and replace special characters [](){}<>| with unicode equivalents.
    """
    if "```mermaid" not in text:
        return text

    replacements = {
        # disable replacing, it's noisy and not necessary
        # '[': '&#91;',
        # ']': '&#93;',
        # '(': '&#40;',
        # ')': '&#41;',
        # '{': '&#123;',
        # '}': '&#125;',
        # '<': '&#60;',
        # '>': '&#62;',
        # '|': '&#124;',
        '"': '&#34;'
    }

    def replace_special_chars(label: str) -> str:
        for char, unicode_val in replacements.items():
            label = label.replace(char, unicode_val)
        return label

    # Function to replace special characters in a label and ensure it's quoted
    def quote_and_sanitize(label):
        # Extract content if already quoted, then re-quote after sanitizing
        label = label.strip()
        while label.startswith('"') and label.endswith('"') and len(label) >= 2:
            label = label[1:-1]
        return f'"{replace_special_chars(label)}"'

    def process_mermaid_block(match):
        block_content = match.group(1)

        lines = block_content.splitlines()
        processed_lines = []

        for line in lines:
            # Skip common diagram markers
            if line.strip().lower() in ['graph td', 'graph lr', 'sequencediagram', 'flowchart td', 'flowchart lr']:
                processed_lines.append(line)
                continue

            # 1. Double Rounded: ID((label))
            if '((' in line and '))' in line:
                match_node = _RE_MERMAID_DOUBLE_ROUNDED.search(line)
                if match_node:
                    node_id = match_node.group(1)
                    label_content = match_node.group(2)
                    line = line.replace(f'{node_id}(({label_content}))',
                                      f'{node_id}(({quote_and_sanitize(label_content)}))')

            # 2. Rounded: ID(label) - only if not already matched as double rounded
            elif '(' in line and ')' in line:
                match_node = _RE_MERAID_SINGLE_ROUNDED.search(line)
                if match_node:
                    node_id = match_node.group(1)
                    label_content = match_node.group(2)
                    line = line.replace(f'{node_id}({label_content})',
                                      f'{node_id}({quote_and_sanitize(label_content)})')

            # 3. Square: ID[label]
            if '[' in line and ']' in line:
                # Find the ID and the content between the FIRST [ and LAST ] on this line
                for match_node in _RE_MERMAID_SQUARE.finditer(line):
                    if match_node:
                        node_id = match_node.group(1)
                        label_content = match_node.group(2)
                        line = line.replace(f'{node_id}[{label_content}]',
                                          f'{node_id}[{quote_and_sanitize(label_content)}]')

            # 4. Braces: ID{label}
            if '{' in line and '}' in line:
                for match_node in _RE_MERMAID_BRACES.finditer(line):
                    if match_node:
                        node_id = match_node.group(1)
                        label_content = match_node.group(2)
                        line = line.replace(f'{node_id}{{{label_content}}}',
                                          f'{node_id}{{{quote_and_sanitize(label_content)}}}')

            # 5. Angle: ID>label]
            if '>' in line and ']' in line:
                for match_node in _RE_MERMAID_ANGLE.finditer(line):
                    if match_node:
                        node_id = match_node.group(1)
                        label_content = match_node.group(2)
                        line = line.replace(f'{node_id}>{label_content}]',
                                          f'{node_id}>{quote_and_sanitize(label_content)}]')

            # 6. Edge labels: -- label -->
            if '-- ' in line and '-->' in line:
                match_edge = _RE_MERMAID_EDGE.search(line)
                if match_edge:
                    prefix = match_edge.group(1)
                    label_content = match_edge.group(2)
                    suffix = match_edge.group(3)
                    line = line.replace(f'{prefix}{label_content}{suffix}',
                                      f'{prefix}{quote_and_sanitize(label_content)}{suffix}')

            # 7. Sequence diagram labels: ID->>ID: label
            if '->>' in line and ':' in line:
                match_seq = _RE_MERMAID_SEQUENCE_LABELS.search(line)
                if match_seq:
                    prefix = match_seq.group(1)
                    label_content = match_seq.group(2)
                    line = line.replace(f'{prefix}{label_content}',
                                      f'{prefix}{quote_and_sanitize(label_content)}')

            # 8. Pipe labels: |label|
            if '|' in line:
                # Flowcharts can have |label| after edge
                # We need to find the label content between pipes. 
                # Mermaid flowcharts use |label| syntax.
                def sub_pipe(m):
                    content = m.group(2)
                    if '&#124;' in content: # Already processed or contains sanitized pipe
                        return m.group(0)
                    return f'|{quote_and_sanitize(content)}|'

                line = _RE_MERMAID_PIPE_LABELS.sub(sub_pipe, line)

            # 9. subgraph label
            if 'subgraph' in line:
                match_seq = _RE_MERMAID_SUBGRAPH_LABEL.search(line)
                if match_seq:
                    prefix = match_seq.group(1)
                    label_content = match_seq.group(2)
                    line = line.replace(f'{prefix}{label_content}',
                                        f'{prefix}{quote_and_sanitize(label_content)}')

            processed_lines.append(line)

        return "```mermaid\n" + "\n".join(processed_lines) + "\n```"

    # Match ```mermaid ... ``` blocks
    return _RE_MERMAID_BLOCK.sub(process_mermaid_block, text)


def _get_module_report_prompt(module_name: Optional[str]) -> Optional[str]:
    """Get the module-specific report prompt if available.

    Args:
        module_name: Name of the module to load report prompt for

    Returns:
        Module report prompt string or None if not available
    """
    if not module_name:
        return None

    try:
        from modules.prompts import get_module_loader  # Dynamic import required

        module_loader = get_module_loader()
        module_report_prompt = module_loader.load_module_report_prompt(module_name)

        if module_report_prompt:
            logger.info(
                "Loaded report prompt for module '%s' (%d chars)",
                module_name,
                len(module_report_prompt),
            )
        else:
            logger.debug("No report prompt found for module '%s'", module_name)

        return module_report_prompt

    except Exception as e:
        logger.warning(
            "Error loading report prompt for module '%s': %s. Using default guidance.",
            module_name,
            e,
        )
        # Return default security assessment guidance as fallback
        return (
            "DOMAIN_LENS:\n"
            "overview: Security assessment focused on identifying vulnerabilities and risks\n"
            "analysis: Analyze findings for exploitability and business impact\n"
            "immediate: Address critical security vulnerabilities immediately\n"
            "short_term: Implement security controls and monitoring\n"
            "long_term: Establish comprehensive security program\n"
        )


def _trim_evidence_for_report(
        items: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    """Keep at most `limit` evidence items, favoring higher severity."""
    if limit <= 0 or len(items) <= limit:
        return items

    trimmed = items[:limit]
    overflow = len(items) - limit
    if overflow > 0:
        trimmed.append(
            {
                "severity": "INFO",
                "parsed": {
                    "title": f"{overflow} additional finding(s) omitted",
                    "details": "Increase CYBER_REPORT_MAX_FINDINGS or review artifacts directly.",
                },
                "confidence": "",
                "validation_status": "info",
            }
        )
    return trimmed


def _clean_remediation_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if t.lower() in {"not determined", "unknown", "n/a"}:
        return "TBD — requires protocol review"
    return t


def build_report_sections(
        operation_id: str,
        target: str,
        objective: str,
        module: str = "web",
        steps_executed: int = 0,
        tools_used: List[str] = None,
) -> Dict[str, Any]:
    """
    Build structured sections for the security assessment report.

    Retrieves operation-scoped evidence and plan, summarizes findings,
    and returns preformatted sections for the final report template.

    This tool retrieves evidence from memory and transforms it into
    structured report sections that can be used to generate the final report.

    Args:
        operation_id: The operation identifier
        target: Assessment target (URL/system)
        objective: Assessment objective
        module: Operation module used (default: web)
        steps_executed: Number of steps executed in operation
        tools_used: List of tools used during assessment

    Returns:
        Dictionary containing all report sections:
        - overview: Executive summary overview
        - evidence_text: Formatted evidence collection
        - findings_table: Vulnerability findings matrix
        - severity_counts: Dictionary of severity counts
        - analysis: Detailed vulnerability analysis
        - recommendations: Immediate/short/long-term recommendations
        - tools_summary: Summary of tools used
        - metadata: Operation metadata
    """
    try:
        logger.info("Building report sections for operation: %s", operation_id)

        # Initialize memory client and retrieve evidence and plans
        evidence = []
        operation_plan = None
        operation_tasks = []
        if operation_id and len(operation_id) >= 11 and operation_id.startswith("OP_"):
            operation_date = f"{operation_id[3:7]}-{operation_id[7:9]}-{operation_id[9:11]}"
        else:
            operation_date = datetime.now().strftime("%Y-%m-%d")
        cross_operation = memory_is_cross_operation()
        manager = get_config_manager()

        memory_client = get_memory_client(silent=True)

        raw_memories: List[Dict[str, Any]] = memory_client.list_memories(
            run_id=operation_id if not cross_operation else None,
            limit=MAX_REPORT_FINDINGS * 10,
        )
        logger.info(f"Total memories loaded: {len(raw_memories)}")

        # Count by operation_id and category for debugging
        try:
            op_ids = Counter()
            categories = Counter()
            for m in raw_memories:
                meta = m.get("metadata", {}) or {}
                op_ids[meta.get("operation_id", "unknown")] += 1
                categories[meta.get("category", "unknown")] += 1
            logger.info(f"Memories by operation_id: {dict(op_ids)}")
            logger.info(f"Memories by category: {dict(categories)}")
        except Exception as debug_err:
            logger.debug(f"Debug counter failed: {debug_err}")

        if not cross_operation:
            logger.info(f"Filtering evidence for current operation_id: {operation_id}")

        operation_plan = memory_client.get_active_plan()
        task_records = memory_client.list_tasks()
        operation_tasks = []
        phase_coverage_state: Dict[int, Dict[str, Any]] = {}
        for task in task_records:
            acceptance_results = memory_client.list_task_acceptance_results(task.task_uid)
            acceptance_results = acceptance_results if isinstance(acceptance_results, list) else []
            completed_ids = {result.criterion_id for result in acceptance_results}
            completed_count = sum(
                1 for criterion in task.acceptance.criteria if criterion.id in completed_ids
            )
            operation_tasks.append(
                f"{task.to_toon(include_format=False)},acceptance={completed_count}/"
                f"{len(task.acceptance.criteria)},manifest={task.acceptance.manifest_hash}"
            )
            phase_state = phase_coverage_state.setdefault(
                task.phase,
                {"task_status_counts": Counter(), "expected_items": set(), "assessed_items": set()},
            )
            phase_state["task_status_counts"][task.status] += 1
            phase_state["expected_items"].update(str(item_id) for item_id in task.acceptance.basis.item_ids)
            for result in acceptance_results:
                phase_state["assessed_items"].update(str(item.item_id) for item in result.coverage)

        phase_coverage = []
        for phase in operation_plan.phases if operation_plan else []:
            phase_state = phase_coverage_state.get(
                phase.id,
                {"task_status_counts": Counter(), "expected_items": set(), "assessed_items": set()},
            )
            expected_items = phase_state["expected_items"]
            assessed_items = phase_state["assessed_items"]
            phase_row = {
                "phase_id": phase.id,
                "title": phase.title,
                "status": phase.status,
                "task_status_counts": dict(sorted(phase_state["task_status_counts"].items())),
                "inventory_item_count": len(expected_items),
                "assessed_item_count": len(assessed_items),
                "omitted_item_count": len(expected_items - assessed_items),
            }
            if phase.status == "not_applicable":
                phase_row["status_reason"] = "No finding candidates required validation."
            phase_coverage.append(phase_row)

        # Process evidence entries - FILTER BY OPERATION_ID
        evidence_skipped = 0
        evidence_included = 0

        logger.info(f"Processing {len(raw_memories)} memories for evidence")

        resolved_finding_uids = {
            str((item.get("metadata", {}) or {}).get("finding_uid"))
            for item in raw_memories
            if (item.get("metadata", {}) or {}).get("category") in {"finding", "validation_failure"}
            and (item.get("metadata", {}) or {}).get("finding_uid")
        }

        for memory_item in raw_memories:
            memory_content = memory_item.get("memory", "")
            metadata = memory_item.get("metadata", {}) or {}
            logger.info(
                f"Checking memory item: id={memory_item.get('id')}, category={metadata.get('category')}, op_id={metadata.get('operation_id')}")
            if not metadata:
                continue
            if (
                metadata.get("category") == "finding_candidate"
                and str(metadata.get("finding_uid", "")) in resolved_finding_uids
            ):
                continue

            if not cross_operation:
                item_op_id = str(metadata.get("operation_id", ""))
                if item_op_id and item_op_id != str(operation_id):
                    # Skip evidence from other operations
                    logger.debug(
                        f"Skipping evidence from different operation: {item_op_id} (current: {operation_id})")
                    evidence_skipped += 1
                    continue

            # Build base evidence structure
            base_evidence = {
                "content": memory_content,
                "id": memory_item.get("id", ""),
                "anchor_id": ("finding-" + str(memory_item.get("id", "")))
                if memory_item.get("id")
                else "",
                "anchor": ("#finding-" + str(memory_item.get("id", "")))
                if memory_item.get("id")
                else "",
                "metadata": metadata,  # Include metadata for traceability
            }

            parsed_evidence = _parse_structured_evidence(memory_content)

            # Normalize report categories without modifying the stored memory.
            stored_category = metadata.get("category")
            category = _normalize_report_category(
                stored_category,
                metadata,
                memory_content,
                parsed_evidence,
            )
            if category in ["finding", "observation", "validation_failure"]:
                if stored_category == "finding" and category == "validation_failure":
                    logger.info(
                        "Classifying report item '%s' (id: %s) as requiring validation",
                        metadata.get("vulnerability") or memory_content[:30],
                        memory_item.get("id"),
                    )

                evidence_included += 1
                item = base_evidence.copy()
                sev = (
                    metadata.get("severity", "MEDIUM")
                    if category == "finding"
                    else metadata.get("claimed_severity", metadata.get("severity", "INFO"))
                    if category == "validation_failure"
                    else "INFO"
                )
                conf = str(metadata.get("confidence", ""))
                item.update(
                    {
                        "category": category,
                        "severity": sev,
                        "confidence": conf,
                        "validation_status": str(
                            metadata.get("validation_status", "")
                        ).strip()
                                             or None,
                    }
                )

                # Parse structured markers from the content so downstream sections have clean fields
                if parsed_evidence and isinstance(parsed_evidence, dict):
                    item["parsed"] = parsed_evidence

                evidence.append(item)

        logger.info(
            "Retrieved %d pieces of evidence from memory (skipped %d from other ops)",
            len(evidence),
            evidence_skipped
        )

        # If no evidence, let LLM handle empty evidence
        if not evidence:
            evidence = []

        # Format evidence for report (cap to avoid context explosions)
        evidence.sort(key=lambda entry: _SEVERITY_ORDER.get(str(entry.get("severity", "")).upper(), 5))
        evidence = _trim_evidence_for_report(evidence, MAX_REPORT_FINDINGS)
        evidence_text = format_evidence_for_report(evidence)

        # Count severities from actual evidence, not just text
        severity_counts = {
            "critical": sum(
                1 for e in evidence if e.get("category") == "finding" and str(e.get("severity", "")).upper() == "CRITICAL"
            ),
            "high": sum(
                1 for e in evidence if e.get("category") == "finding" and str(e.get("severity", "")).upper() == "HIGH"
            ),
            "medium": sum(
                1 for e in evidence if e.get("category") == "finding" and str(e.get("severity", "")).upper() == "MEDIUM"
            ),
            "low": sum(
                1 for e in evidence if e.get("category") == "finding" and str(e.get("severity", "")).upper() == "LOW"
            ),
            "info": sum(
                1
                for e in evidence
                if e.get("category") == "finding"
                and str(e.get("severity", "")).upper() == "INFO"
            ),
        }

        # Generate findings table (structured, deterministic)
        findings_table = generate_findings_summary_table(evidence)

        # Load module report prompt for domain lens
        domain_lens = {}
        try:
            domain_lens = _extract_domain_lens(_get_module_report_prompt(module))
            logger.info("Loaded domain lens for module '%s'", module)
        except Exception as e:
            logger.warning("Could not load module prompt: %s", e)

        # Transform evidence to content using domain lens
        report_content = _transform_evidence_to_content(
            evidence=evidence,
            domain_lens=domain_lens,
            target=target,
            objective=objective,
        )

        # Generate structured finding sections - include ALL findings for comprehensive report
        summary_table = (
            _format_summary_table([item for item in evidence if item.get("category") == "finding"])
            if evidence
            else ""
        )

        # Extract token/duration/cost metrics from the operation log (best-effort)
        metrics_input = 0
        metrics_output = 0
        metrics_total = 0
        metrics_duration = ""
        metrics_cost = 0.0
        tools_used_from_log = []
        try:
            safe_target_name = sanitize_target_name(target)
            log_path = os.path.join(get_output_path(target_name=safe_target_name, operation_id=operation_id),
                                    "cyber_operations.log")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if (
                                "__CYBER_EVENT__" in line
                                and ('"type": "metrics_update"' in line or '"type": "tool_start"' in line)
                        ):
                            # Extract JSON between markers
                            try:
                                start = line.index("__CYBER_EVENT__") + len(
                                    "__CYBER_EVENT__"
                                )
                                end = line.index("__CYBER_EVENT_END__")
                                payload = json.loads(line[start:end])
                                if payload.get("type") == "metrics_update":
                                    m = (
                                        payload.get("metrics", {})
                                        if isinstance(payload, dict)
                                        else {}
                                    )
                                    # Prefer the most recent values (overwrite as we go)
                                    metrics_input = max(metrics_input, int(m.get("inputTokens", metrics_input) or 0))
                                    metrics_output = max(metrics_output, int(m.get("outputTokens", metrics_output) or 0))
                                    metrics_total = max(metrics_total,
                                                        int(m.get("totalTokens", m.get("tokens", metrics_total) or 0)))
                                    metrics_duration = duration_max(metrics_duration,
                                                                    str(m.get("duration", metrics_duration)))
                                    if "cost" in m:
                                        try:
                                            metrics_cost = max(metrics_cost, float(m.get("cost")))
                                        except Exception:
                                            pass
                                elif payload.get("type") == "progress_update":
                                    if "timestamp" in payload:
                                        operation_date = payload.get("timestamp")[0:10]
                                elif payload.get("type") == "tool_start":
                                    if "tool_name" in payload:
                                        tool_name = payload.get("tool_name")
                                        if tool_name:
                                            if tool_name == "shell" and "tool_input" in payload:
                                                tool_input = payload.get("tool_input")
                                                tool_command = tool_input.get("command", "")
                                                if tool_command:
                                                    if isinstance(tool_command, list):
                                                        tools_used_from_log.append(tool_command[0])
                                                    else:
                                                        tools_used_from_log.append(str(tool_command).split()[0])
                                            else:
                                                tools_used_from_log.append(tool_name)

                            except Exception:
                                continue
        except Exception:
            # Ignore metrics extraction failures silently
            pass
        if not tools_used:
            tools_used = tools_used_from_log

        # Format tools summary (accepts dict or list); prefer accurate counts if provided
        try:
            # If caller passed repeated names, we’ll get counts automatically
            # If caller passed a unique set, counts will be 1 each
            tools_summary = format_tools_summary(tools_used or [])
        except Exception:
            tools_summary = format_tools_summary([])

        # Build canonical findings (first per severity) with stable anchors
        canonical_findings: Dict[str, Dict[str, Any]] = {}
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
            sev_items = [
                e
                for e in evidence
                if e.get("category") == "finding" and str(e.get("severity", "")).upper() == sev
            ]
            if not sev_items:
                continue
            top = sev_items[0]
            p = top.get("parsed", {}) if isinstance(top.get("parsed"), dict) else {}
            anchor_link = str(top.get("anchor") or "").strip()
            if not anchor_link and str(top.get("id") or "").strip():
                anchor_link = f"#finding-{top['id']}"
            canonical_findings[sev] = {
                "id": top.get("id", ""),
                "title": (
                        p.get("vulnerability")
                        or safe_truncate(str(top.get("content", "")), 60)
                ).strip(),
                "where": (p.get("where") or "").strip(),
                "anchor": anchor_link,
            }

        # Build complete sections dictionary
        target_coverage = _format_target_coverage(operation_plan, task_records, evidence)
        evidence_integrity_errors = []
        for item in evidence:
            for reference in sorted(_artifact_references(item)):
                if not reference.startswith("artifact:"):
                    continue
                try:
                    _artifact_path_from_ref(reference)
                except ValueError as error:
                    evidence_integrity_errors.append(
                        {"evidence_id": item.get("id", ""), "reference": reference, "error": str(error)}
                    )
        finding_count = sum(1 for item in evidence if item.get("category") == "finding")
        observation_count = sum(
            1 for item in evidence if item.get("category") in {"signal", "observation", "discovery"}
        )
        validation_failure_count = sum(
            1 for item in evidence if item.get("category") == "validation_failure"
        )
        sections = {
            "operation_id": operation_id,
            "target": target,
            "objective": objective,
            "date": operation_date,
            "steps_executed": steps_executed,
            "severity_counts": severity_counts,
            "critical_count": severity_counts["critical"],
            "high_count": severity_counts["high"],
            "medium_count": severity_counts["medium"],
            "low_count": severity_counts["low"],
            "info_count": severity_counts["info"],
            "overview": report_content.get("overview", ""),
            "operation_plan": operation_plan.to_dict() if operation_plan else "",
            "operation_tasks": {
                "columns": Task.csv_format(),
                "items": operation_tasks,
            },
            "evidence_text": evidence_text,
            "findings_table": findings_table,
            "summary_table": summary_table,
            "target_coverage": target_coverage,
            "phase_coverage": phase_coverage,
            "analysis": report_content.get("analysis", ""),
            "immediate_recommendations": report_content.get("immediate", ""),
            "short_term_recommendations": report_content.get("short_term", ""),
            "long_term_recommendations": report_content.get("long_term", ""),
            "raw_evidence": evidence,
            "finding_count": finding_count,
            "observation_count": observation_count,
            "validation_failure_count": validation_failure_count,
            "tools_summary": tools_summary,
            "analysis_framework": domain_lens.get("framework", ""),
            "module": module,
            "evidence_count": len(evidence),
            "evidence_integrity_errors": evidence_integrity_errors,
            "canonical_findings": canonical_findings,
            # Execution metrics for direct insertion into the template
            "main_model": f"{manager.get_provider()}/{manager.get_llm_config(manager.get_provider()).model_id}",
            "input_tokens": metrics_input,
            "output_tokens": metrics_output,
            "total_tokens": metrics_total or (metrics_input + metrics_output),
            "total_duration": metrics_duration,
            "estimated_cost": (
                f"{metrics_cost:.4f}"
                if isinstance(metrics_cost, (int, float)) and metrics_cost > 0
                else "N/A"
            ),
        }

        logger.info(
            "Report sections built: %d findings, %d observations, %d validation failures "
            "(%d evidence items total; %d critical, %d high)",
            finding_count,
            observation_count,
            validation_failure_count,
            len(evidence),
            severity_counts["critical"],
            severity_counts["high"],
        )

        return sections

    except Exception as e:
        logger.error("Error building report sections: %s", e, exc_info=True)
        return {
            "error": str(e),
            "operation_id": operation_id,
            "target": target,
            "objective": objective,
        }


def _parse_structured_evidence(content: str) -> Dict[str, str]:
    """
    Parse structured evidence from memory content.

    Extracts components like [VULNERABILITY], [WHERE], [IMPACT], [EVIDENCE], [STEPS]
    from the stored finding content.

    Args:
        content: Raw memory content with structured markers

    Returns:
        Dictionary with parsed evidence components
    """
    components = {
        "vulnerability": "",
        "where": "",
        "impact": "",
        "evidence": "",
        "steps": "",
        "remediation": "",
        "confidence": "",
    }

    # Define markers to extract
    markers = {
        "VULNERABILITY": "vulnerability",
        "FINDING": "vulnerability",  # Alternative marker
        "WHERE": "where",
        "IMPACT": "impact",
        "EVIDENCE": "evidence",
        "STEPS": "steps",
        "REMEDIATION": "remediation",
        "CONFIDENCE": "confidence",
        "DISCOVERY": "vulnerability",  # Alternative marker
        "SIGNAL": "vulnerability",  # Alternative marker
    }

    for marker, key in markers.items():
        # Extract content between markers using regex
        # Updated pattern to better handle multi-line content
        pattern = rf"\[{marker}\]\s*(.*?)(?=\[(?:VULNERABILITY|FINDING|WHERE|IMPACT|EVIDENCE|STEPS|REMEDIATION|CONFIDENCE|DISCOVERY|SIGNAL)|$)"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match and not components[key]:  # Don't override if already found
            extracted = match.group(1).strip()
            # Clean up the extracted content
            if extracted:
                components[key] = extracted

    # Remove all entries from components where the value is falsey, including strings with only whitespace
    components = {k: v for k, v in components.items() if v and v.strip()}

    return components


def _format_detailed_findings(findings: List[Dict[str, Any]], severity: str) -> str:
    """
    Format findings with evidence-first structure.

    Provides concise, professional presentation with full evidence.
    """
    if not findings:
        return ""

    output = []
    for i, finding in enumerate(findings, 1):
        title = ""
        evidence = ""
        impact = ""
        remediation = ""
        confidence = ""
        status = str(finding.get("validation_status") or "").strip()

        # Extract from parsed structure if available
        if "parsed" in finding and any(finding["parsed"].values()):
            parsed = finding["parsed"]
            title = parsed.get("vulnerability", "")
            location = parsed.get("where", "")
            if location:
                title += f" - {location}"
            evidence = parsed.get("evidence", "")
            impact = parsed.get("impact", "")
            remediation = parsed.get("remediation", "")
            confidence = parsed.get("confidence", "")
        else:
            # Use raw content if no parsed structure
            content = finding.get("content", "")
            title = ""
            evidence = content
            impact = ""
            remediation = ""
            confidence = finding.get("confidence", "")

        # Normalize fields (only remediation cleanup; display confidence as-is)
        confidence = confidence or finding.get("confidence", "")
        remediation = _clean_remediation_text(remediation)

        # If impact missing, attempt to parse from original content
        if not impact:
            parsed_fallback = _parse_structured_evidence(
                finding.get("content", "") or ""
            )
            impact = (
                parsed_fallback.get("impact", "")
                if isinstance(parsed_fallback, dict)
                else ""
            )

        # Build structured finding
        anchor_id = str(finding.get("anchor_id") or "").strip()
        if anchor_id:
            output.append(f'<a id="{anchor_id}"></a>')
        output.append(f"#### {i}. {title}")

        # Status badge and confidence
        if status:
            status_norm = (
                "Verified"
                if status.lower() == "verified"
                else ("Unverified" if status else "")
            )
            if status_norm:
                output.append(f"**Status:** {status_norm}")
        if confidence:
            output.append(f"**Confidence:** {confidence}")

        # Evidence first (full for critical/high)
        if evidence:
            # For critical/high, show full evidence
            if severity in ["CRITICAL", "HIGH"]:
                # If evidence is the full content with markers, format it better
                if "[VULNERABILITY]" in evidence and "[WHERE]" in evidence:
                    # Parse inline for display
                    formatted_evidence = evidence
                    for marker in [
                        "[VULNERABILITY]",
                        "[WHERE]",
                        "[IMPACT]",
                        "[EVIDENCE]",
                        "[STEPS]",
                        "[REMEDIATION]",
                        "[CONFIDENCE]",
                    ]:
                        formatted_evidence = formatted_evidence.replace(
                            marker, f"\n{marker}"
                        )
                    output.append(
                        f"**Evidence:**\n```\n{formatted_evidence.strip()}\n```"
                    )
                else:
                    output.append(f"**Evidence:**\n```\n{evidence}\n```")
            else:
                if len(evidence) > 500:
                    evidence = evidence[:500] + "\n[Truncated - see appendix]"
                output.append(f"**Evidence:**\n```\n{evidence}\n```")

        # Impact and remediation - always show them
        impact_text = impact if impact else "N/A"
        output.append(f"**Impact:** {impact_text}")
        output.append(
            f"**Remediation:** {remediation if remediation else 'TBD — requires protocol review'}"
        )

        output.append("")  # Blank line between findings

    return "\n".join(output)


def _format_summary_table(findings: List[Dict[str, Any]]) -> str:
    """
    Create a summary table for remaining findings.

    Token-efficient presentation for lower priority findings.
    """
    if not findings:
        return ""

    table = [
        "| # | Severity | Finding | Location | Confidence |",
        "|---|----------|---------|----------|------------|",
    ]

    for i, finding in enumerate(
            findings[:MAX_REPORT_FINDINGS], 1
    ):  # Include up to 50 findings in summary
        severity = finding.get("severity", "MEDIUM")
        confidence = finding.get("confidence", "N/A")

        # Extract title and location
        if "parsed" in finding and any(finding["parsed"].values()):
            parsed = finding["parsed"]
            title = parsed.get("vulnerability", "Finding")[:50]
            location = parsed.get("where", "N/A")[:30]
        else:
            content = finding.get("content", "")[:50]
            title = content.split("[WHERE]")[0] if "[WHERE]" in content else content
            location = "See appendix"

        table.append(f"| {i} | {severity} | {title} | {location} | {confidence} |")

    # Include all findings count if more than shown
    if len(findings) > MAX_REPORT_FINDINGS:
        table.append(f"\n*Total findings: {len(findings)}*")

    return "\n".join(table)
