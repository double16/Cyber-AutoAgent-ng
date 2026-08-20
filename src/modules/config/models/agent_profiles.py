"""Per-agent model parameter registry, recommended defaults, and runtime adaptation engine.

This module provides fine-grained, per-agent-type model configuration (temperature,
reasoning level, top_k, top_p, output token limit) and tracks dynamic runtime adaptations
such as reasoning level reductions and 3-strike max token limit escalations.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


class ReasoningLevel(str, Enum):
    """Standardized reasoning levels for LLM agent roles."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"

    def to_bool(self) -> bool:
        """Evaluate reasoning level as a boolean.

        In all boolean reasoning models, 'none' and 'low' evaluate to False,
        while 'medium', 'high', and 'xhigh' evaluate to True.
        """
        return self in (ReasoningLevel.MEDIUM, ReasoningLevel.HIGH, ReasoningLevel.XHIGH)

    @classmethod
    def from_value(cls, value: Union[ReasoningLevel, str, None]) -> ReasoningLevel:
        """Parse or normalize a reasoning level value."""
        if value is None:
            return cls.NONE
        if isinstance(value, cls):
            return value
        cleaned = str(value).strip().lower()
        for member in cls:
            if member.value == cleaned:
                return member
        return cls.NONE


@dataclass
class AgentModelSettings:
    """Model execution settings tailored for a specific agent role."""

    temperature: Optional[float] = None
    reasoning_level: ReasoningLevel = ReasoningLevel.NONE
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    max_tokens: int = 4096

    def copy(self) -> AgentModelSettings:
        """Create a detached copy of this configuration."""
        return AgentModelSettings(
            temperature=self.temperature,
            reasoning_level=self.reasoning_level,
            top_k=self.top_k,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration to a dictionary."""
        return {
            "temperature": self.temperature,
            "reasoning_level": self.reasoning_level.value,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }


@dataclass
class ParameterAdjustmentRecord:
    """Audit record capturing a dynamic model parameter modification during runtime."""

    timestamp: str
    agent_type: str
    parameter_name: str
    old_value: Any
    new_value: Any
    trigger_reason: str
    permanent: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to a JSON-serializable dictionary."""
        return {
            "timestamp": self.timestamp,
            "agent_type": self.agent_type,
            "parameter_name": self.parameter_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "trigger_reason": self.trigger_reason,
            "permanent": self.permanent,
        }


ROLE_ALIASES: Dict[str, str] = {
    "plan_builder": "plan_creator",
    "phase_evaluator": "task_evaluator",
    "evaluation": "task_evaluator",
    "report": "report_agent",
    "report_executive": "report_agent",
    "report_finding": "report_agent",
    "primary": "task_executor",
    "default": "task_executor",
    "executor": "task_executor",
}


DEFAULT_AGENT_PROFILES: Dict[str, AgentModelSettings] = {
    "plan_creator": AgentModelSettings(
        temperature=0.2,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "plan_critic": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.LOW,
        top_p=0.95,
        top_k=40,
        max_tokens=4096,
    ),
    "task_creator": AgentModelSettings(
        temperature=0.2,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "task_prompt_builder": AgentModelSettings(
        temperature=0.2,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "task_prompt_critic": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.LOW,
        top_p=0.95,
        top_k=40,
        max_tokens=2048,
    ),
    "task_executor": AgentModelSettings(
        temperature=0.5,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "task_evaluator": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.NONE,
        top_p=0.95,
        top_k=40,
        max_tokens=4096,
    ),
    "task_phase_classifier": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.NONE,
        top_p=0.95,
        top_k=40,
        max_tokens=2048,
    ),
    "report_agent": AgentModelSettings(
        temperature=0.2,
        reasoning_level=ReasoningLevel.NONE,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "report_critic": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.LOW,
        top_p=0.95,
        top_k=40,
        max_tokens=2048,
    ),
    "taxonomy_annotator": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.NONE,
        top_p=0.95,
        top_k=40,
        max_tokens=4096,
    ),
    "attack_enricher": AgentModelSettings(
        temperature=0.0,
        reasoning_level=ReasoningLevel.NONE,
        top_p=0.95,
        top_k=40,
        max_tokens=4096,
    ),
    "swarm": AgentModelSettings(
        temperature=0.6,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
    "unknown": AgentModelSettings(
        temperature=0.5,
        reasoning_level=ReasoningLevel.MEDIUM,
        top_p=0.95,
        top_k=40,
        max_tokens=8192,
    ),
}


def normalize_agent_type(agent_type: Optional[str]) -> str:
    """Normalize agent type name or alias to canonical role."""
    if not agent_type:
        return "unknown"
    role = str(agent_type).strip().lower()
    return ROLE_ALIASES.get(role, role)


class AgentSettingsRegistry:
    """Centralized registry for per-agent profiles and runtime adaptation state."""

    def __init__(self, custom_defaults: Optional[Dict[str, AgentModelSettings]] = None):
        self._lock = threading.RLock()
        self._baselines: Dict[str, AgentModelSettings] = {}
        self._active: Dict[str, AgentModelSettings] = {}
        self._token_recovery_counts: Dict[str, int] = {}
        self._learned_fallbacks: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._adjustment_records: List[ParameterAdjustmentRecord] = []

        defaults = custom_defaults or DEFAULT_AGENT_PROFILES
        for role, setting in defaults.items():
            canonical = normalize_agent_type(role)
            self._baselines[canonical] = setting.copy()
            self._active[canonical] = setting.copy()

    def get_settings(
        self,
        agent_type: Optional[str] = None,
        provider: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> AgentModelSettings:
        """Retrieve active model settings for the specified agent role."""
        with self._lock:
            canonical = normalize_agent_type(agent_type)
            if canonical not in self._active:
                fallback_setting = DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
                self._baselines[canonical] = fallback_setting.copy()
                self._active[canonical] = fallback_setting.copy()

            settings = self._active[canonical].copy()

            # Apply learned provider/model parameter constraints if any
            if provider and model_id:
                key = (provider.lower(), model_id.lower())
                constraints = self._learned_fallbacks.get(key, {})
                if "temperature" in constraints and constraints["temperature"] is None:
                    settings.temperature = None
                if "top_k" in constraints and constraints["top_k"] is None:
                    settings.top_k = None
                if "top_p" in constraints and constraints["top_p"] is None:
                    settings.top_p = None

            return settings

    def apply_reasoning_repair(
        self,
        agent_type: str,
        level: Union[ReasoningLevel, str],
        reason: str,
        permanent: bool = True,
    ) -> None:
        """Update the reasoning level for an agent role following a recovery event.

        Always updates the active profile so immediate retries and subsequent calls use target_level.
        If permanent=True, additionally logs a parameter adjustment record for Appendix C.
        """
        with self._lock:
            canonical = normalize_agent_type(agent_type)
            target_level = ReasoningLevel.from_value(level)
            current_settings = self._active.get(
                canonical, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
            )
            baseline_level = self._baselines.get(
                canonical, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
            ).reasoning_level

            # Always update active reasoning level for retry and subsequent execution
            current_settings.reasoning_level = target_level
            self._active[canonical] = current_settings

            if permanent:
                record = ParameterAdjustmentRecord(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_type=canonical,
                    parameter_name="reasoning_level",
                    old_value=baseline_level.value,
                    new_value=target_level.value,
                    trigger_reason=reason,
                    permanent=True,
                )
                self._adjustment_records.append(record)

    def boost_max_tokens_for_retry(
        self,
        agent_type: str,
        boost_amount: int = 2048,
        ceiling: Optional[int] = None,
    ) -> int:
        """Temporarily boost max_tokens for an agent role during a repair attempt."""
        with self._lock:
            canonical = normalize_agent_type(agent_type)
            current_settings = self._active.get(
                canonical, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
            )
            old_tokens = current_settings.max_tokens
            new_tokens = old_tokens + boost_amount
            if ceiling is not None and ceiling > 0:
                new_tokens = min(new_tokens, ceiling)
            current_settings.max_tokens = new_tokens
            self._active[canonical] = current_settings
            return new_tokens

    def revert_token_boost(self, agent_type: str, previous_tokens: int) -> None:
        """Revert max_tokens back to previous unboosted value if retry was unpromoted."""
        with self._lock:
            canonical = normalize_agent_type(agent_type)
            if canonical in self._active:
                self._active[canonical].max_tokens = previous_tokens

    def record_token_recovery_success(
        self,
        agent_type: str,
        boost_amount: int = 2048,
        ceiling: Optional[int] = None,
    ) -> bool:
        """Track successful non-reasoning token recovery and escalate limits upon 3 strikes.

        Returns True if a permanent limit escalation was triggered, False otherwise.
        """
        with self._lock:
            canonical = normalize_agent_type(agent_type)
            count = self._token_recovery_counts.get(canonical, 0) + 1
            self._token_recovery_counts[canonical] = count

            current_settings = self._active.get(
                canonical, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
            )
            baseline_tokens = self._baselines.get(
                canonical, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings())
            ).max_tokens

            if count >= 3:
                new_tokens = baseline_tokens + boost_amount
                if ceiling is not None and ceiling > 0:
                    new_tokens = min(new_tokens, ceiling)

                current_settings.max_tokens = new_tokens
                self._active[canonical] = current_settings

                record = ParameterAdjustmentRecord(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_type=canonical,
                    parameter_name="max_tokens",
                    old_value=baseline_tokens,
                    new_value=new_tokens,
                    trigger_reason="3-strike token exhaustion escalation",
                    permanent=True,
                )
                self._adjustment_records.append(record)
                self._token_recovery_counts[canonical] = 0
                return True
            else:
                # Less than 3 strikes: revert active max_tokens to baseline
                current_settings.max_tokens = baseline_tokens
                self._active[canonical] = current_settings
                return False

    def record_parameter_fallback(
        self,
        provider: str,
        model_id: str,
        param_name: str,
        fallback_value: Any,
        trigger_reason: str = "provider parameter rejection",
    ) -> None:
        """Record a learned constraint when a provider rejects a specific parameter."""
        with self._lock:
            key = (provider.lower(), model_id.lower())
            if key not in self._learned_fallbacks:
                self._learned_fallbacks[key] = {}
            self._learned_fallbacks[key][param_name] = fallback_value

            record = ParameterAdjustmentRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_type=f"{provider}/{model_id}",
                parameter_name=param_name,
                old_value="configured",
                new_value=fallback_value,
                trigger_reason=trigger_reason,
                permanent=True,
            )
            self._adjustment_records.append(record)

    def get_learned_fallbacks(self, provider: str, model_id: str) -> Dict[str, Any]:
        """Retrieve learned parameter constraints for a provider/model pair."""
        with self._lock:
            key = (provider.lower(), model_id.lower())
            return dict(self._learned_fallbacks.get(key, {}))

    def export_adjustment_records(self) -> List[ParameterAdjustmentRecord]:
        """Export all recorded runtime parameter adjustments."""
        with self._lock:
            return list(self._adjustment_records)

    def export_profile_comparison(self) -> Dict[str, Dict[str, Any]]:
        """Compare baseline and final settings across all registered agent roles."""
        with self._lock:
            comparison: Dict[str, Dict[str, Any]] = {}
            all_roles = sorted(set(list(self._baselines.keys()) + list(self._active.keys())))
            for role in all_roles:
                base = self._baselines.get(role, DEFAULT_AGENT_PROFILES.get("unknown", AgentModelSettings()))
                final = self._active.get(role, base)
                base_dict = base.to_dict()
                final_dict = final.to_dict()
                adjusted = base_dict != final_dict
                comparison[role] = {
                    "baseline": base_dict,
                    "final": final_dict,
                    "adjusted": adjusted,
                }
            return comparison

    def reset(self) -> None:
        """Reset registry to initial baseline profiles and clear adjustments."""
        with self._lock:
            self._baselines.clear()
            self._active.clear()
            self._token_recovery_counts.clear()
            self._learned_fallbacks.clear()
            self._adjustment_records.clear()
            for role, setting in DEFAULT_AGENT_PROFILES.items():
                canonical = normalize_agent_type(role)
                self._baselines[canonical] = setting.copy()
                self._active[canonical] = setting.copy()


_GLOBAL_AGENT_SETTINGS_REGISTRY: Optional[AgentSettingsRegistry] = None
_GLOBAL_REGISTRY_LOCK = threading.RLock()


def get_agent_settings_registry() -> AgentSettingsRegistry:
    """Obtain or initialize the global AgentSettingsRegistry instance."""
    global _GLOBAL_AGENT_SETTINGS_REGISTRY
    with _GLOBAL_REGISTRY_LOCK:
        if _GLOBAL_AGENT_SETTINGS_REGISTRY is None:
            _GLOBAL_AGENT_SETTINGS_REGISTRY = AgentSettingsRegistry()
        return _GLOBAL_AGENT_SETTINGS_REGISTRY


def reset_agent_settings_registry() -> AgentSettingsRegistry:
    """Reset the global registry instance (primarily used in test fixtures)."""
    global _GLOBAL_AGENT_SETTINGS_REGISTRY
    with _GLOBAL_REGISTRY_LOCK:
        _GLOBAL_AGENT_SETTINGS_REGISTRY = AgentSettingsRegistry()
        return _GLOBAL_AGENT_SETTINGS_REGISTRY


def translate_reasoning_to_provider(
    provider: str,
    model_id: str,
    reasoning_level: ReasoningLevel,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """Translate canonical ReasoningLevel into provider-specific parameter arguments."""
    provider_key = (provider or "").lower()
    result: Dict[str, Any] = {}

    if reasoning_level == ReasoningLevel.NONE:
        if provider_key == "ollama":
            result["think"] = False
        elif provider_key == "litellm":
            result["reasoning_effort"] = None
        elif provider_key == "bedrock":
            result["effort"] = None
        elif provider_key == "gemini":
            result["thinking_budget"] = 0
        return result

    # Reasoning is active (LOW, MEDIUM, HIGH, XHIGH)
    if provider_key == "ollama":
        # String level for Ollama; fallback logic will convert to bool if rejected
        result["think"] = reasoning_level.value if reasoning_level in (
            ReasoningLevel.LOW, ReasoningLevel.MEDIUM, ReasoningLevel.HIGH
        ) else "high"
    elif provider_key == "bedrock":
        effort_map = {
            ReasoningLevel.LOW: "low",
            ReasoningLevel.MEDIUM: "medium",
            ReasoningLevel.HIGH: "high",
            ReasoningLevel.XHIGH: "max",
        }
        result["effort"] = effort_map.get(reasoning_level, "medium")
        # Budget tokens for models requiring thinking budget
        budget_map = {
            ReasoningLevel.LOW: min(max_tokens, 1024),
            ReasoningLevel.MEDIUM: min(max_tokens, 2048),
            ReasoningLevel.HIGH: min(max_tokens, 4096),
            ReasoningLevel.XHIGH: min(max_tokens, int(max_tokens * 0.8)),
        }
        result["budget_tokens"] = budget_map.get(reasoning_level, min(max_tokens, 2048))
    elif provider_key == "litellm":
        result["reasoning_effort"] = reasoning_level.value
        budget_tokens = max(1024, int(max_tokens * 0.8))
        result["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
    elif provider_key == "gemini":
        gemini_budget = {
            ReasoningLevel.LOW: min(max_tokens, 1024),
            ReasoningLevel.MEDIUM: min(max_tokens, 4096),
            ReasoningLevel.HIGH: min(max_tokens, 8192),
            ReasoningLevel.XHIGH: min(max_tokens, 16384),
        }
        result["thinking_budget"] = gemini_budget.get(reasoning_level, min(max_tokens, 4096))

    return result


def mutate_agent_model_reasoning(agent: Any, level: Union[ReasoningLevel, str]) -> None:
    """Dynamically modify reasoning settings on an existing agent or model instance."""
    target_level = ReasoningLevel.from_value(level)
    model = getattr(agent, "model", agent)
    if model is None:
        return

    # LiteLLMModel
    if hasattr(model, "client_args") and isinstance(model.client_args, dict):
        if target_level == ReasoningLevel.NONE:
            model.client_args.pop("reasoning_effort", None)
            model.client_args.pop("thinking", None)
        else:
            model.client_args["reasoning_effort"] = target_level.value
            max_tokens = getattr(model, "_output_tokens", 4096)
            model.client_args["thinking"] = {"type": "enabled", "budget_tokens": max(1024, int(max_tokens * 0.8))}

    # BedrockModel
    if hasattr(model, "additional_request_fields") and isinstance(model.additional_request_fields, dict):
        if target_level == ReasoningLevel.NONE:
            model.additional_request_fields.pop("output_config", None)
            model.additional_request_fields.pop("thinking", None)
            if "anthropic_beta" in model.additional_request_fields and isinstance(
                model.additional_request_fields["anthropic_beta"], list
            ):
                model.additional_request_fields["anthropic_beta"] = [
                    b for b in model.additional_request_fields["anthropic_beta"] if "effort" not in b
                ]
        else:
            effort_map = {
                ReasoningLevel.LOW: "low",
                ReasoningLevel.MEDIUM: "medium",
                ReasoningLevel.HIGH: "high",
                ReasoningLevel.XHIGH: "max",
            }
            model.additional_request_fields.setdefault("output_config", {})
            model.additional_request_fields["output_config"]["effort"] = effort_map.get(target_level, "medium")

    # OllamaModel
    if hasattr(model, "config") and isinstance(model.config, dict):
        if "additional_args" not in model.config or not isinstance(model.config["additional_args"], dict):
            model.config["additional_args"] = {}
        if target_level == ReasoningLevel.NONE:
            model.config["additional_args"]["think"] = False
        else:
            model.config["additional_args"]["think"] = (
                target_level.value
                if target_level in (ReasoningLevel.LOW, ReasoningLevel.MEDIUM, ReasoningLevel.HIGH)
                else target_level.to_bool()
            )

    # GeminiModel
    if hasattr(model, "client_args") and isinstance(model.client_args, dict):
        if "thinking_config" in model.client_args:
            budget = 0 if target_level == ReasoningLevel.NONE else 2048
            model.client_args["thinking_config"] = {"thinking_budget": budget}


def mutate_agent_model_max_tokens(agent: Any, boost_amount: int = 2048, ceiling: Optional[int] = None) -> int:
    """Dynamically increase max_tokens on an existing agent or model instance."""
    model = getattr(agent, "model", agent)
    if model is None:
        return 0

    current = getattr(model, "_output_tokens", None)
    if current is None and hasattr(model, "config") and isinstance(model.config, dict):
        current = model.config.get("max_tokens")
    if current is None and hasattr(model, "params") and isinstance(model.params, dict):
        current = model.params.get("max_tokens") or model.params.get("max_output_tokens")
    if current is None:
        current = 4096

    new_tokens = current + boost_amount
    if ceiling is not None and ceiling > 0:
        new_tokens = min(new_tokens, ceiling)

    setattr(model, "_output_tokens", new_tokens)
    if hasattr(model, "max_tokens"):
        setattr(model, "max_tokens", new_tokens)
    if hasattr(model, "config") and isinstance(model.config, dict):
        model.config["max_tokens"] = new_tokens
    if hasattr(model, "params") and isinstance(model.params, dict):
        if "max_tokens" in model.params:
            model.params["max_tokens"] = new_tokens
        if "max_output_tokens" in model.params:
            model.params["max_output_tokens"] = new_tokens

    return new_tokens
