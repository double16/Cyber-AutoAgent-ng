#!/usr/bin/env python3
"""
Report Generation Handler Utility for Cyber-AutoAgent

This module provides report generation functionality that is called
directly by handlers (ReactBridgeHandler) at the
end of operations to guarantee report generation.

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
from modules.handlers.utils import sanitize_target_name, get_output_path, duration_max
from modules.prompts.factory import (
    _extract_domain_lens,
    _transform_evidence_to_content,
    format_evidence_for_report,
    format_tools_summary,
    generate_findings_summary_table,
    safe_truncate,
)
from modules.prompts.factory import get_report_agent_prompt
from modules.tools.memory import memory_sort_by_create_time
from modules.tools.memory import get_memory_client, memory_is_cross_operation
from strands.types.content import Message, ContentBlock

logger = get_logger("Handlers.ReportGenerator")

MAX_REPORT_FINDINGS = int(os.getenv("CYBER_REPORT_MAX_FINDINGS", "200"))
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def generate_security_report(
    target: str,
    objective: str,
    operation_id: str,
    config_data: Optional[str] = None,
) -> str:
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
        config_data: JSON string containing additional config (steps_executed, tools_used,
                    evidence, provider, model_id, module)

    Returns:
        The generated security assessment report as a string

    Example:
        generate_security_report(
            target="example.com",
            objective="Identify web application vulnerabilities",
            operation_id="OP_20240115_143022",
            config_data='{"steps_executed": 15, "tools_used": ["nmap", "nikto"], "provider": "bedrock"}'
        )
    """
    try:
        # Log the report generation request
        logger.info("Generating security report for operation: %s", operation_id)
        config_manager = get_config_manager()

        # Parse config data
        config_params = {}
        if config_data:
            try:
                config_params = json.loads(config_data)
            except json.JSONDecodeError:
                logger.error("Invalid JSON in config_data parameter")
                return "Report generation failed: Invalid configuration format"

        # Extract parameters with defaults
        steps_executed = config_params.get("steps_executed", 0)
        tools_used = config_params.get("tools_used", [])
        provider = config_params.get("provider", config_manager.get_provider())
        model_id = config_params.get("model_id")
        module = config_params.get("module")

        sections = build_report_sections(
            operation_id=operation_id,
            target=target,
            objective=objective,
            module=module,
            steps_executed=steps_executed,
            tools_used=tools_used,
        )

        # Validate evidence collection - skip report only if truly no memories
        if not sections or int(sections.get("evidence_count", 0)) == 0:
            logger.info(
                "No evidence/memories collected for operation %s - skipping report generation",
                operation_id,
            )
            return ""

        # Get module report prompt if available for domain guidance
        module_report_prompt = _get_module_report_prompt(module)

        # Load the report template, preferring module-specific template when available
        from modules.prompts import load_prompt_template

        # If a module-specific report prompt exists (e.g., ctf/report_prompt.md), use it as the template
        if module_report_prompt and str(module_report_prompt).strip():
            report_template = module_report_prompt
            logger.info("Using module-specific report template for module: %s", module)
        else:
            report_template = load_prompt_template("report_template.md")
            logger.info("Using default security assessment report template")

        # Create report agent with the builder tool
        report_agent = ReportGenerator.create_report_agent(
            provider=provider,
            model_id=model_id,
            operation_id=operation_id,
            target=target,
        )

        # Create comprehensive prompt with template structure and module guidance
        # Using string concatenation/format to avoid f-string issues with template placeholders
        agent_prompt = get_report_agent_prompt()
        # Safely format the agent prompt with runtime values
        # Escape braces in the report template so Python format doesn't consume them
        report_template_escaped = report_template.replace("{", "{{").replace("}", "}}")
        tools_json = json.dumps(tools_used) if tools_used else "[]"
        module_str = module or "web"
        module_guidance = (
            module_report_prompt
            if module_report_prompt
            else "Apply general security assessment best practices focusing on OWASP Top 10 and common vulnerability patterns."
        )
        agent_prompt = agent_prompt.format(
            target=target,
            objective=objective,
            operation_id=operation_id,
            module=module_str,
            steps_executed=steps_executed,
            tools_count=len(tools_used) if tools_used else 0,
            module_guidance=module_guidance,
            report_template=report_template_escaped,
            tools_used=tools_json,
        )

        # Generate the report using the agent
        report_agent.messages.append(
            Message(role="user",
                    content=[ContentBlock(text="# REPORT DATA\n"), ContentBlock(text=json.dumps(sections))])
        )

        # Store report data for processing by other means
        output_path = get_output_path(target_name=sanitize_target_name(target), operation_id=operation_id)
        with open(os.path.join(output_path, "security_assessment_report.json"), "w") as f:
            f.write(json.dumps(sections, indent=2, sort_keys=True))

        logger.info("Invoking report generation agent with structured prompt of %d characters ...", len(agent_prompt))
        logger.debug("Report prompt\n%s", agent_prompt)
        result = None
        for retry in range(1,-1,-1):  # [1,0]
            try:
                result = report_agent(agent_prompt)
                break
            except Exception as e:
                if retry == 0:
                    raise e
                e_str = str(e).lower()
                if any(n in e_str for n in ["midstreamfallbackerror", "maxtokensreached", "max_tokens"]):
                    # network errors midstream are not uncommon with litellm, they are not retried
                    # max tokens reached may be the model got into a verbose state
                    logger.info("Report agent failed, retrying ... %s", e_str)
                    continue
                raise e

        # Extract the report content
        if result and hasattr(result, "message"):
            content = result.message.get("content", [])
            if content and isinstance(content, list):
                report_text = ""
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        report_text += block["text"]

                logger.info(
                    "Report generated successfully (%d characters)", len(report_text)
                )
                return report_text

        logger.error("Failed to generate report - no content in response")
        return "Error: Failed to generate security assessment report"

    except Exception as e:
        logger.error("Error generating security report: %s", e, exc_info=True)
        # Don't expose internal error details to user
        return "Report generation failed. Please check logs for details."


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

    sorted_items = sorted(
        items,
        key=lambda entry: _SEVERITY_ORDER.get(
            str(entry.get("severity", "")).upper(), 5
        ),
    )
    trimmed = sorted_items[:limit]
    overflow = len(sorted_items) - limit
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
        operation_date = datetime.now().strftime("%Y-%m-%d")
        cross_operation = memory_is_cross_operation()
        manager = get_config_manager()

        raw_memories: List[Dict[str, Any]] = []

        try:
            memory_client = get_memory_client(silent=True)
        except Exception:
            memory_client = None

        if memory_client:
            try:
                # Use run_id scoping to get operation-specific memories
                raw_memories = memory_client.list_memories(
                    run_id=operation_id if not cross_operation else None,
                    limit=MAX_REPORT_FINDINGS * 10,
                )
            except Exception as mem_err:
                logger.warning(
                    "Failed to load memories from existing client: %s", mem_err
                )
                raw_memories = []
        else:
            error_msg = "Critical: Memory service unavailable - cannot generate comprehensive report with stored evidence"
            logger.error(error_msg)
            # Still proceed but with clear indication of missing data
            evidence.append(
                {
                    "category": "system_warning",
                    "content": "⚠️ WARNING: Memory service unavailable - report generated without stored evidence from previous assessment steps",
                    "severity": "HIGH",
                    "confidence": "SYSTEM",
                }
            )

        if raw_memories:
            # Debug logging: show what we loaded from memory
            logger.info(f"Total memories loaded from shared storage: {len(raw_memories)}")

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

            # Select the newest active plan for this operation
            try:
                plan_candidates = []
                for m in raw_memories:
                    meta = m.get("metadata", {}) or {}
                    if str(meta.get("category", "")) == "plan":
                        # Only include plans from current operation (no cross-op fallback)
                        if str(meta.get("operation_id", "")) == str(operation_id):
                            plan_candidates.append(m)

                # Sort by created_at descending
                plan_candidates.sort(key=memory_sort_by_create_time, reverse=True)
                # Pick the first active one; else first candidate
                for m in plan_candidates:
                    meta = m.get("metadata", {}) or {}
                    if meta.get("active", False) is True:
                        operation_plan = m.get("memory", "")
                        logger.info("Selected newest active operation plan from memory")
                        break
                if not operation_plan and plan_candidates:
                    operation_plan = plan_candidates[0].get("memory", "")
                    logger.info("Selected newest available plan from memory")
            except Exception as _pe:
                logger.debug(f"Plan selection fallback due to: {_pe}")

            # Process evidence entries - FILTER BY OPERATION_ID
            evidence_skipped = 0
            evidence_included = 0

            for memory_item in raw_memories:
                memory_content = memory_item.get("memory", "")
                metadata = memory_item.get("metadata", {}) or {}
                if not metadata:
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

                # Findings via metadata
                category = metadata.get("category")
                if category in ["finding", "signal", "observation", "discovery"]:
                    evidence_included += 1
                    item = base_evidence.copy()
                    sev = metadata.get("severity", "MEDIUM" if category == "finding" else "INFO")
                    conf = str(metadata.get("confidence", ""))
                    # Parse structured markers from the content so downstream sections have clean fields
                    parsed_evidence = _parse_structured_evidence(memory_content)
                    item.update(
                        {
                            "category": category,
                            "severity": sev,
                            "confidence": conf,
                            "parsed": parsed_evidence
                            if isinstance(parsed_evidence, dict)
                            else {},
                            "validation_status": str(
                                metadata.get("validation_status", "")
                            ).strip()
                                                 or None,
                        }
                    )
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
        evidence = _trim_evidence_for_report(evidence, MAX_REPORT_FINDINGS)
        evidence_text = format_evidence_for_report(evidence)

        # Count severities from actual evidence, not just text
        severity_counts = {
            "critical": sum(
                1 for e in evidence if str(e.get("severity", "")).upper() == "CRITICAL"
            ),
            "high": sum(
                1 for e in evidence if str(e.get("severity", "")).upper() == "HIGH"
            ),
            "medium": sum(
                1 for e in evidence if str(e.get("severity", "")).upper() == "MEDIUM"
            ),
            "low": sum(
                1 for e in evidence if str(e.get("severity", "")).upper() == "LOW"
            ),
            "info": sum(
                1 for e in evidence if str(e.get("severity", "")).upper() == "INFO"
            ),
        }

        # Generate findings table (structured, deterministic)
        findings_table = generate_findings_summary_table(evidence)

        # Load module report prompt for domain lens
        domain_lens = {}
        try:
            from modules.prompts.factory import get_module_loader

            module_loader = get_module_loader()
            module_prompt = module_loader.load_module_report_prompt(module)
            if module_prompt:
                domain_lens = _extract_domain_lens(module_prompt)
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

        # Separate findings for detailed vs summary treatment
        critical_findings, high_findings, summary_findings = _prioritize_findings(
            evidence
        )

        # Generate structured finding sections - include ALL findings for comprehensive report
        critical_section = _format_detailed_findings(critical_findings, "CRITICAL")
        high_section = _format_detailed_findings(
            high_findings, "HIGH"
        )  # Include ALL high findings
        summary_table = (
            _format_summary_table(summary_findings) if summary_findings else ""
        )

        # Format the operation plan
        operation_plan_formatted = _format_operation_plan(operation_plan)

        # Extract token/duration/cost metrics from the operation log (best-effort)
        metrics_input = 0
        metrics_output = 0
        metrics_total = 0
        metrics_duration = ""
        metrics_cost = 0.0
        last_step = 0
        total_steps = 0
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
                                and ('"type": "metrics_update"' in line or '"type": "step_header"' in line or '"type": "tool_start"' in line)
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
                                elif payload.get("type") == "step_header":
                                    if "step" in payload:
                                        current_step = int(payload.get("step"))
                                        if current_step < last_step:
                                            # new operation started
                                            total_steps += last_step
                                        last_step = current_step
                                        if "timestamp" in payload:
                                            operation_date = payload.get("timestamp")[0:10]
                                elif payload.get("type") == "tool_start":
                                    if "tool_name" in payload:
                                        tool_name = payload.get("tool_name")
                                        if tool_name:
                                            tools_used_from_log.append(tool_name)

                            except Exception:
                                continue
        except Exception:
            # Ignore metrics extraction failures silently
            pass
        total_steps += last_step
        if total_steps > steps_executed:
            steps_executed = total_steps
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
                e for e in evidence if str(e.get("severity", "")).upper() == sev
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
            "module_report": "",  # LLM generates from context
            "visual_summary": "",  # LLM generates mermaid diagram
            "overview": report_content.get("overview", ""),
            "operation_plan": operation_plan_formatted,
            "evidence_text": evidence_text,
            "findings_table": findings_table,
            "critical_findings": critical_section,
            "high_findings": high_section,
            "summary_table": summary_table,
            "analysis": report_content.get("analysis", ""),
            "immediate_recommendations": report_content.get("immediate", ""),
            "short_term_recommendations": report_content.get("short_term", ""),
            "long_term_recommendations": report_content.get("long_term", ""),
            "raw_evidence": evidence,  # For LLM to generate attack paths and technical content
            "tools_summary": tools_summary,
            "analysis_framework": domain_lens.get("framework", ""),
            "module": module,
            "evidence_count": len(evidence),
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
            "Report sections built: %d findings (%d critical, %d high)",
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

    return components


def _prioritize_findings(evidence: List[Dict[str, Any]]) -> tuple:
    """
    Separate findings by severity for structured presentation.

    Returns:
        Tuple of (critical_findings, high_findings, other_findings)
    """
    critical = []
    high = []
    other = []

    for finding in evidence:
        severity = str(finding.get("severity", "")).upper()
        if severity == "CRITICAL":
            critical.append(finding)
        elif severity == "HIGH":
            high.append(finding)
        else:
            other.append(finding)

    return critical, high, other


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


def _format_operation_plan(plan_content: str) -> str:
    """Format the operation plan for inclusion in the report."""
    if not plan_content:
        return ""

    # Try to parse JSON plan
    if plan_content.startswith("[PLAN]"):
        plan_content = plan_content.replace("[PLAN]", "").strip()

    try:
        plan_data = json.loads(plan_content)

        # Return raw plan data as JSON for LLM to format
        return json.dumps(plan_data, indent=2)
    except (json.JSONDecodeError, TypeError):
        # Return raw plan if not JSON
        return plan_content
