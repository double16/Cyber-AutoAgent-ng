"""Agents module for Cyber-AutoAgent."""

from strands.models import BedrockModel
from strands.models.gemini import GeminiModel
from strands.models.litellm import LiteLLMModel

from modules.agents.cyber_autoagent import create_agent
from modules.agents.patches import patch_model_class_tool_use_id
from modules.agents.report_agent import ReportGenerator
from modules.config.models.ollama import OllamaModel

__all__ = ["ReportGenerator", "create_agent"]

patch_model_class_tool_use_id(BedrockModel)
patch_model_class_tool_use_id(LiteLLMModel)
patch_model_class_tool_use_id(OllamaModel)
patch_model_class_tool_use_id(GeminiModel)
