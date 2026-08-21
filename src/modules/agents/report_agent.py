#!/usr/bin/env python3
"""
Report Generation Utilities for Cyber-AutoAgent

This module provides utility functions for report generation that work
with the report generation tool to maintain clean architecture and
avoid code duplication.
"""

from typing import Optional

from strands import Agent
from strands.handlers import PrintingCallbackHandler

from modules import __version__
from modules.agents.factory import create_agent_with_stateful_retry
from modules.agents.patches import ToolUseIdHook
from modules.config.manager import get_config_manager
from modules.config.models.factory import create_strands_model
from modules.config.system.logger import get_logger
from modules.tools.artifact import create_bounded_artifact_reader

logger = get_logger("Agents.ReportAgent")


class NoOpCallbackHandler(PrintingCallbackHandler):
    """Minimal callback handler that suppresses SDK output during report generation."""

    def __call__(self, **kwargs):  # type: ignore[override]
        return


class ReportGenerator:
    """Factory for a report-generation Agent with a single builder tool.

    The agent is configured with a concise system prompt and the
    output of the build_report_sections function. Output is returned to the caller.
    """

    def create_report_agent(
        provider: str,
        system_prompt: str,
        model_id: Optional[str] = None,
        operation_id: Optional[str] = None,
        target: Optional[str] = None,
        callback_handler = None,
        agent_role: str = "report_agent",
    ) -> Agent:
        """
        Create a clean agent instance for report generation.

        This method creates a new agent with appropriate configuration
        for report generation, ensuring proper trace hierarchy when
        used within a tool context.

        Args:
            provider: Model provider (bedrock, ollama, litellm)
            model_id: Specific model to use (optional)
            operation_id: Operation ID for trace continuity
            target: Target system for trace metadata
            system_prompt: Optional custom system prompt
            agent_role: Reporting actor or critic role used for trace metadata

        Returns:
            Configured Agent instance for report generation
        """
        # Use the shared factory so report actors participate in the same profile,
        # capability fallback, and adaptation behavior as workflow agents.
        cfg = get_config_manager()
        prov = (provider or "bedrock").lower()
        is_critic = agent_role == "report_critic"
        role = "report_critic" if is_critic else "report_agent"
        mid = model_id or cfg.get_llm_config(prov).model_id
        model = create_strands_model(prov, mid, role)

        # Create agent with report-specific configuration
        trace_attrs = {
            # Core identification - CRITICAL for trace continuity
            "langfuse.session.id": operation_id,
            "langfuse.user.id": f"cyber-agent-{target}" if target else "cyber-agent",
            # Human-readable name that Langfuse will pick up
            "name": f"Security Report - {target} - {operation_id}",
            # Tags for filtering and categorization
            "langfuse.tags": [
                "Cyber-AutoAgent",
                prov,
                operation_id,
            ],
            "langfuse.environment": cfg.getenv(
                "DEPLOYMENT_ENV", "production"
            ),
            # Standard OTEL attributes
            "session.id": operation_id,
            "user.id": f"cyber-agent-{target}",
            # Agent identification
            "langfuse.agent.type": role,
            "agent.name": "Cyber-ReportCritic" if is_critic else "Cyber-ReportGenerator",
            "agent.version": __version__,
            "agent.role": role,
            "gen_ai.agent.name": "Cyber-AutoAgent",
            "gen_ai.system": "Cyber-AutoAgent",
            # Operation context
            "operation.id": operation_id,
            "operation.type": "reporting",
            "operation.phase": "reporting",
            "target.host": target or "unknown",
            # Model configuration
            "model.provider": provider,
            "model.id": mid,
        }

        # Configure trace attributes for observability
        # Only add if operation_id is provided to ensure proper parent-child relationship

        # Create a silent callback handler to prevent duplicate output
        # The report will be returned and handled by the caller
        agent_kwargs = {
            "model": model,
            "name": f"Cyber-{'ReportCritic' if is_critic else 'ReportGenerator'} {operation_id}",
            "system_prompt": system_prompt,
            "tools": [create_bounded_artifact_reader()],
            "trace_attributes": trace_attrs if operation_id else None,
            "callback_handler": callback_handler or NoOpCallbackHandler(),
            "hooks": [ToolUseIdHook()],
            "context_manager": "auto",
        }
        return create_agent_with_stateful_retry(
            agent_kwargs,
            model_id=mid,
            agent_cls=Agent,
        )
