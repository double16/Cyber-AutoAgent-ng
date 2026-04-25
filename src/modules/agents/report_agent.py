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
from strands.models import BedrockModel
from strands.models.litellm import LiteLLMModel
from strands_tools.editor import editor
from modules.config.models.ollama import OllamaModel

from modules.config.manager import get_config_manager
from modules.config.models.factory import create_gemini_model, get_capabilities
from modules.config.system.logger import get_logger
from modules.agents.patches import ToolUseIdHook
from modules import __version__

logger = get_logger("Agents.ReportAgent")

_REPORT_TEMPERATURE = 0.3

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

        Returns:
            Configured Agent instance for report generation
        """
        # Select model via central configuration, with sensible defaults
        # do not specify max tokens, let the model use up the context
        cfg = get_config_manager()
        prov = (provider or "bedrock").lower()
        if prov == "bedrock":
            # Always use the primary bedrock model from config
            llm_cfg = cfg.get_llm_config("bedrock")
            # Only override if explicitly provided, otherwise use config
            mid = model_id or llm_cfg.model_id

            # Harden Bedrock client similar to main agent to avoid timeouts
            from botocore.config import Config as BotocoreConfig

            boto_config = BotocoreConfig(
                region_name=cfg.get_server_config("bedrock").region,
                retries={"max_attempts": 10, "mode": "adaptive"},
                read_timeout=1200,
                connect_timeout=1200,
                max_pool_connections=100,
            )

            # Ensure explicit region to avoid environment inconsistencies
            region = cfg.get_server_config("bedrock").region
            capabilities = get_capabilities(prov, mid)
            model = BedrockModel(
                model_id=mid,
                region_name=region,
                temperature=_REPORT_TEMPERATURE if capabilities.supports_temperature else None,
                boto_client_config=boto_config,
            )
        elif prov == "gemini":
            # Always use the primary model from config
            llm_cfg = cfg.get_llm_config("gemini")
            # Only override if explicitly provided, otherwise use config
            mid = model_id or llm_cfg.model_id

            model = create_gemini_model(
                mid,
                cfg.get_default_region(),
                prov,
                "report")
            capabilities = get_capabilities(prov, mid)
            if capabilities.supports_temperature:
                model.config.get("params")["temperature"] = _REPORT_TEMPERATURE
            else:
                model.config.get("params").pop("temperature", None)
        elif prov == "ollama":
            host = cfg.get_ollama_host()
            llm_cfg = cfg.get_llm_config("ollama")
            # Only override if explicitly provided, otherwise use config
            mid = model_id or llm_cfg.model_id
            capabilities = get_capabilities(prov, mid)
            model = OllamaModel(
                host=host,
                model_id=mid,
                temperature=_REPORT_TEMPERATURE if capabilities.supports_temperature else None,
                ollama_client_args={
                    "timeout": cfg.get_ollama_timeout(),
                },
                options=cfg.get_ollama_options(),
            )
        else:  # litellm
            llm_cfg = cfg.get_llm_config("litellm")
            # Only override if explicitly provided, otherwise use config
            mid = model_id or llm_cfg.model_id
            capabilities = get_capabilities(prov, mid)
            # Pass both token params - LiteLLM drop_params removes unsupported one
            params = {}
            if capabilities.supports_temperature:
                params["temperature"] = _REPORT_TEMPERATURE
            client_args = {
                "num_retries": 5,
                "timeout": 1200,
            }
            model = LiteLLMModel(model_id=mid, params=params, client_args=client_args)

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
            "langfuse.agent.type": "report_generator",
            "agent.name": "Cyber-ReportGenerator",
            "agent.version": __version__,
            "agent.role": "report_generation",
            "gen_ai.agent.name": "Cyber-AutoAgent",
            "gen_ai.system": "Cyber-AutoAgent",
            # Operation context
            "operation.id": operation_id,
            "operation.type": "reporting",
            "operation.phase": "reporting",
            "target.host": target or "unknown",
            # Model configuration
            "model.provider": provider,
            "model.id": mid if "mid" in locals() else model_id,
        }

        # Configure trace attributes for observability
        # Only add if operation_id is provided to ensure proper parent-child relationship

        # Create a silent callback handler to prevent duplicate output
        # The report will be returned and handled by the caller
        return Agent(
            model=model,
            name=f"Cyber-ReportGenerator {operation_id}",
            system_prompt=system_prompt,
            tools=[editor],
            trace_attributes=trace_attrs if operation_id else None,
            callback_handler=callback_handler or NoOpCallbackHandler(),
            hooks=[ToolUseIdHook()],
        )
