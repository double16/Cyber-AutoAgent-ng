"""Agent event handler for structured SDK callback emission.

The handler converts Strands SDK callbacks into Cyber-AutoAgent stream events.
It is intentionally UI-agnostic: the React terminal is one consumer, but the
event protocol is shared by CLI/logging/automation surfaces.
"""

import json
import math
import os
import re
import threading
import time
import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from pathlib import Path

from strands.handlers import PrintingCallbackHandler

from ..base import BudgetLimitReached
from ..events import EventEmitter, get_emitter
from ..output_interceptor import (
    get_buffered_output,
    get_buffered_error_output,
    set_tool_execution_state,
)
from .tool_emitters import ToolEventEmitter
from modules.config.system.logger import get_logger
from ...config import get_config_manager
from ...config.models import get_models_client
from ...config.models.factory import get_model_id_from_agent, get_provider_from_agent
from ...config.system import EnvironmentReader
from ...config.types import DEFAULT_MAX_DURATION
from ..conversation_budget import token_calc
from ...utils.text_reducer import collapse_first_repeated_sequence

from modules.handlers.utils import (
    get_output_path,
    sanitize_target_name,
)

logger = get_logger("Handlers.AgentEvent")

_DEFAULT_REASONING_DEDUPE_TTL_S = 20.0
_AGENT_USAGE_CACHE_SIZE = 128
_AGENT_USAGE_UUID_ATTR = "_caa_agent_event_usage_uuid"

# Do not increment action count for planning tools
_PLANNING_TOOL_NAMES = {
    "create_tasks",
}


@dataclass
class _ReasoningSeenHolder:
    """Mutable per-callback holder to dedupe reasoning extraction without shared instance state."""
    seen: bool = False


@dataclass
class _AgentUsageEntry:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0


@dataclass
class ReportBudgetEstimate:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    findings: int = 0
    observations: int = 0
    remaining_steps: int = 0


class OperationEventCoordinator:
    """Shared operation-level state for multiple agent event handlers."""

    def __init__(
            self,
            operation_id: str,
            emitter: EventEmitter,
            budget_max_duration: int = 0,
            budget_max_tokens: Optional[int] = None,
            budget_max_cost: Optional[float] = None,
    ) -> None:
        self.operation_id = operation_id
        self.emitter = emitter
        self.budget_max_duration = budget_max_duration
        self.budget_max_tokens = budget_max_tokens
        self.budget_max_cost = budget_max_cost
        self.start_time = time.time()
        self._lock = threading.RLock()
        self._agent_sequence = 0
        self._termination_emitted = False
        self._termination_reason: Optional[str] = None
        self._report_generated = False
        self.memory_ops = 0
        self.evidence_count = 0
        self.tool_counts: Dict[str, int] = {}
        self._handler_usage: Dict[str, _AgentUsageEntry] = {}
        self.report_findings = 0
        self.report_observations = 0
        self.report_finding_content_tokens = 0
        self.report_observation_content_tokens = 0
        self._report_finding_content_token_items: List[int] = []
        self._report_observation_content_token_items: List[int] = []
        self._report_exact_counts = False
        self._report_steps_started = 0

    def next_agent_run_id(self, agent_name: str) -> str:
        with self._lock:
            self._agent_sequence += 1
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_name or "agent").strip("_") or "agent"
            return f"{safe_name}-{self._agent_sequence}"

    def emit(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self.emitter.emit(event)

    def update_usage(self, handler_id: str, entry: _AgentUsageEntry) -> None:
        with self._lock:
            self._handler_usage[handler_id] = entry

    def current_usage(self) -> _AgentUsageEntry:
        with self._lock:
            total = _AgentUsageEntry()
            for entry in self._handler_usage.values():
                total.input_tokens += int(entry.input_tokens)
                total.output_tokens += int(entry.output_tokens)
                total.cache_read_tokens += int(entry.cache_read_tokens)
                total.cache_write_tokens += int(entry.cache_write_tokens)
                total.cost += float(entry.cost)
            return total

    def elapsed_seconds(self) -> float:
        with self._lock:
            return max(0.0, time.time() - self.start_time)

    def record_tool(self, tool_name: str) -> None:
        if not tool_name:
            return
        with self._lock:
            self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1

    def record_memory(
            self,
            evidence: bool = False,
            category: Optional[str] = None,
            severity: Optional[str] = None,
            content_length: int = 0,
            model_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.memory_ops += 1
            if evidence:
                self.evidence_count += 1
            if self._report_exact_counts:
                return

            normalized_category = str(category or "").strip().lower()
            normalized_severity = str(severity or "").strip().upper()
            content_tokens = token_calc(max(0, int(content_length or 0)), model_id=model_id)
            if normalized_category == "finding" or normalized_severity in {"CRITICAL", "HIGH"}:
                self.report_findings += 1
                self.report_finding_content_tokens += content_tokens
                self._report_finding_content_token_items.append(content_tokens)
            elif normalized_category in {"signal", "observation", "discovery"}:
                self.report_observations += 1
                self.report_observation_content_tokens += content_tokens
                self._report_observation_content_token_items.append(content_tokens)

    def set_report_items(self, items: List[Dict[str, Any]], model_id: Optional[str] = None) -> None:
        findings = 0
        observations = 0
        finding_content_tokens = 0
        observation_content_tokens = 0
        finding_items: List[int] = []
        observation_items: List[int] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip().lower()
            severity = str(item.get("severity") or "").strip().upper()
            content = item.get("content") or item.get("memory") or ""
            content_tokens = token_calc(len(str(content)), model_id=model_id)
            if category == "finding" or severity in {"CRITICAL", "HIGH"}:
                findings += 1
                finding_content_tokens += content_tokens
                finding_items.append(content_tokens)
            elif category in {"signal", "observation", "discovery"}:
                observations += 1
                observation_content_tokens += content_tokens
                observation_items.append(content_tokens)
        with self._lock:
            self.report_findings = findings
            self.report_observations = observations
            self.report_finding_content_tokens = finding_content_tokens
            self.report_observation_content_tokens = observation_content_tokens
            self._report_finding_content_token_items = finding_items
            self._report_observation_content_token_items = observation_items
            self._report_exact_counts = True
            self._report_steps_started = 0

    def mark_report_step_started(self) -> None:
        with self._lock:
            total_steps = 2 + self.report_findings + self.report_observations
            self._report_steps_started = min(total_steps, self._report_steps_started + 1)

    def report_budget_estimate(
            self,
            provider_id: Optional[str],
            model_id: Optional[str],
            models_client: Any = None,
            pricing_fallback: Optional[Dict[str, float]] = None,
            pricing_override: bool = False,
    ) -> ReportBudgetEstimate:
        with self._lock:
            findings = int(self.report_findings)
            observations = int(self.report_observations)
            finding_content_tokens = int(self.report_finding_content_tokens)
            observation_content_tokens = int(self.report_observation_content_tokens)
            finding_items = list(self._report_finding_content_token_items)
            observation_items = list(self._report_observation_content_token_items)
            steps_started = int(self._report_steps_started)

        remaining_steps = max(0, 2 + findings + observations - steps_started)
        if remaining_steps <= 0:
            return ReportBudgetEstimate(findings=findings, observations=observations, remaining_steps=0)

        if len(finding_items) != findings:
            finding_items = [0] * findings
            if findings > 0:
                finding_items[-1] = finding_content_tokens
        if len(observation_items) != observations:
            observation_items = [0] * observations
            if observations > 0:
                observation_items[-1] = observation_content_tokens

        step_costs: List[tuple[int, int]] = [(2500, 1500)]
        step_costs.extend((1800 + content_tokens, 1800) for content_tokens in finding_items)
        step_costs.extend((1400 + content_tokens, 900) for content_tokens in observation_items)
        step_costs.append((2200, 1200))
        remaining_costs = step_costs[steps_started:]
        input_tokens = math.ceil(sum(item[0] for item in remaining_costs) * 1.15)
        output_tokens = math.ceil(sum(item[1] for item in remaining_costs) * 1.15)
        cost = self._estimate_report_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_id=provider_id,
            model_id=model_id,
            models_client=models_client,
            pricing_fallback=pricing_fallback,
            pricing_override=pricing_override,
        )
        return ReportBudgetEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost=cost,
            findings=findings,
            observations=observations,
            remaining_steps=remaining_steps,
        )

    def _estimate_report_cost(
            self,
            input_tokens: int,
            output_tokens: int,
            provider_id: Optional[str],
            model_id: Optional[str],
            models_client: Any = None,
            pricing_fallback: Optional[Dict[str, float]] = None,
            pricing_override: bool = False,
    ) -> float:
        provider = str(provider_id or "").lower()
        resolved_model = model_id
        fallback = pricing_fallback or {}
        fallback_cost = (
            float(fallback.get("input", 0.0) or 0.0) * (input_tokens / 1_000_000)
            + float(fallback.get("output", 0.0) or 0.0) * (output_tokens / 1_000_000)
        )
        if pricing_override:
            return fallback_cost

        try:
            if not resolved_model:
                config_manager = get_config_manager()
                resolved_model = config_manager.get_llm_config(provider or config_manager.get_provider()).model_id
        except Exception:
            resolved_model = model_id

        if models_client is not None and provider != "ollama":
            try:
                pricing = None
                if provider and provider != "litellm" and resolved_model:
                    try:
                        pricing = models_client.get_pricing(f"{provider}/{resolved_model}")
                    except Exception:
                        pricing = None
                if pricing is None and resolved_model:
                    pricing = models_client.get_pricing(resolved_model)
                if pricing is not None:
                    return (
                        float(pricing.input or 0.0) * (input_tokens / 1_000_000)
                        + float(pricing.output or 0.0) * (output_tokens / 1_000_000)
                    )
            except Exception:
                logger.debug("Unable to price report reservation for model %s", resolved_model, exc_info=True)

        return fallback_cost

    def mark_termination(self, reason: str) -> bool:
        with self._lock:
            if self._termination_emitted:
                return False
            self._termination_emitted = True
            self._termination_reason = reason
            return True

    @property
    def termination_emitted(self) -> bool:
        with self._lock:
            return self._termination_emitted

    @property
    def termination_reason(self) -> Optional[str]:
        with self._lock:
            return self._termination_reason


class AgentEventHandler(PrintingCallbackHandler):
    """
    Handler that bridges SDK callbacks to Cyber-AutoAgent stream events.

    This handler processes SDK callbacks and emits structured events that
    downstream interfaces can display. It handles tool execution, reasoning
    text, metrics tracking, and per-agent state management.
    """

    def __init__(
        self,
        operation_id: str = None,
        provider_id: str = None,
        model_id: str = None,
            specialist_model_id: str = None,
        emitter: EventEmitter = None,
        init_context: Dict[str, Any] = None,
            coordinator: OperationEventCoordinator = None,
            agent_name: str = "Cyber-AutoAgent",
            agent_type: str = "operation_controller",
            agent_run_id: str = None,
            parent_agent_run_id: str = None,
            emit_operation_init: bool = True,
            start_metrics_thread: bool = True,
    ):
        """
        Initialize the React bridge handler.

        Args:
            operation_id: Unique operation identifier
            model_id: Model ID for accurate pricing calculations
            specialist_model_id: Optional model ID used by specialist/sub-agent runs
            emitter: Event emitter to use (defaults to stdout)
            init_context: Optional initialization context with rich operation details
            coordinator: Shared operation coordinator for multi-agent runs
            agent_name: Human-readable agent name for event metadata
            agent_type: Stable agent role/type for event metadata
            agent_run_id: Unique run ID for this agent instance
            parent_agent_run_id: Optional parent agent run ID
            emit_operation_init: Emit operation initialization events from this handler
            start_metrics_thread: Start this handler's periodic metrics thread
        """
        super().__init__()

        env_reader = EnvironmentReader()
        self._state_lock = threading.RLock()

        # Operation configuration
        self.action_count = 0
        self.operation_id = (
            operation_id or f"OP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        # Initialize emitter with operation context
        self.emitter = emitter or get_emitter(operation_id=self.operation_id)
        self.agent_name = agent_name or "Cyber-AutoAgent"
        self.agent_type = agent_type or "agent"
        self.parent_agent_run_id = parent_agent_run_id
        self._active_agent_metadata: Optional[Dict[str, str]] = None
        self.emit_operation_init = emit_operation_init
        self.start_metrics_thread = start_metrics_thread
        self.start_time = time.time()
        self.provider_id = provider_id
        self.model_id = model_id
        self.specialist_model_id = specialist_model_id or model_id
        self.init_context = init_context or {}

        # Unified budget caps
        budget_ctx = {}
        try:
            if isinstance(self.init_context, dict):
                budget_ctx = self.init_context.get("budget", {}) or {}
        except Exception:
            budget_ctx = {}
        try:
            self.budget_max_duration = int(budget_ctx.get("maxDurationMinutes") or DEFAULT_MAX_DURATION)
        except Exception:
            self.budget_max_duration = 0
        try:
            self.budget_max_tokens = (
                int(budget_ctx.get("maxTokens")) if budget_ctx.get("maxTokens") is not None else None
            )
        except Exception:
            self.budget_max_tokens = None
        try:
            self.budget_max_cost = (
                float(budget_ctx.get("maxCost")) if budget_ctx.get("maxCost") is not None else None
            )
        except Exception:
            self.budget_max_cost = None

        self.coordinator = coordinator or OperationEventCoordinator(
            operation_id=self.operation_id,
            emitter=self.emitter,
            budget_max_duration=self.budget_max_duration,
            budget_max_tokens=self.budget_max_tokens,
            budget_max_cost=self.budget_max_cost,
        )
        self.agent_run_id = agent_run_id or self.coordinator.next_agent_run_id(self.agent_name)

        # Track budget termination once
        self._budget_limit_reached = False
        self._budget_limit_reason = None

        # Metrics tracking
        self.memory_ops = 0
        self.evidence_count = 0
        # Track SDK metrics as authoritative source
        # THREAD SAFETY: Use the state lock to protect counters accessed by the metrics thread
        self._metrics_lock = self._state_lock
        self._sdk_input_tokens = 0
        self._sdk_output_tokens = 0
        # Cache token metrics for prompt caching (Bedrock/Anthropic)
        self._sdk_cache_read_tokens = 0
        self._sdk_cache_write_tokens = 0
        self._aggregate_input_tokens = 0
        self._aggregate_output_tokens = 0
        self._aggregate_cache_read_tokens = 0
        self._aggregate_cache_write_tokens = 0
        self._aggregate_cost = 0.0
        self._agent_usage_cache: OrderedDict[str, _AgentUsageEntry] = OrderedDict()
        self._agent_usage_cache_size = _AGENT_USAGE_CACHE_SIZE
        self._report_metrics_input_baseline = 0
        self._report_metrics_output_baseline = 0
        self._report_metrics_cache_read_baseline = 0
        self._report_metrics_cache_write_baseline = 0
        # Metrics emission handled by background thread

        try:
            self.models_client = get_models_client()
            logger.debug("models.dev client initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize models.dev client, model cost will not be reported", exc_info=e)
            self.models_client = None
        pricing_env_names = [
            "CYBER_AGENT_PRICING_INPUT",
            "CYBER_AGENT_PRICING_OUTPUT",
            "CYBER_AGENT_PRICING_CACHE_READ",
            "CYBER_AGENT_PRICING_CACHE_WRITE",
        ]
        self._pricing_override_configured = any(os.environ.get(name) not in [None, ""] for name in pricing_env_names)
        self.pricing_input = env_reader.get_float("CYBER_AGENT_PRICING_INPUT", 0.0)
        self.pricing_output = env_reader.get_float("CYBER_AGENT_PRICING_OUTPUT", 0.0)
        self.pricing_cache_read = env_reader.get_float("CYBER_AGENT_PRICING_CACHE_READ", 0.0)
        self.pricing_cache_write = env_reader.get_float("CYBER_AGENT_PRICING_CACHE_WRITE", 0.0)

        # Tool tracking
        self.tool_start_times = {}  # Track start times for duration calculation
        self.announced_tools = set()
        self.tool_input_buffer = {}
        self.tool_name_buffer = {}  # Map tool_id -> tool_name for correct attribution
        self.tools_used = set()
        # Track per-tool usage counts for accurate reporting
        self.tool_counts = {}
        # Track whether a tool invocation already emitted meaningful output to suppress redundant generic completions
        self.tool_use_output_emitted = {}
        # Track tool IDs that have complete input to avoid duplicate updates
        self.tools_with_complete_input = set()

        # Reasoning buffer to prevent fragmentation
        self.reasoning_buffer = []
        # Track times for reasoning streaming control (append vs. flush)
        self.last_reasoning_time = 0
        self._last_reasoning_flush = 0
        # Track whether any reasoning has ever been emitted (for CLI orchestration heuristics)
        self._emitted_any_reasoning = False

        # Recent reasoning dedupe per agent (TTL-based to prevent repeated summaries)
        self._recent_reasoning_by_agent = {}
        reasoning_dedupe_ttl_s = _DEFAULT_REASONING_DEDUPE_TTL_S
        provider_text = str(self.provider_id or "")
        model_text = str(self.model_id or "")
        if "ollama" in provider_text or "ollama/" in model_text:
            reasoning_dedupe_ttl_s = 90.0
        try:
            self._recent_reasoning_ttl = float(
                os.getenv("REASONING_DEDUPE_TTL_S", str(reasoning_dedupe_ttl_s))
            )
        except Exception:
            self._recent_reasoning_ttl = reasoning_dedupe_ttl_s

        # Ensure each action has exactly one reasoning block (after initial pre-action reasoning)
        self._reasoning_required_for_current_action = False
        self.pending_action_header = False
        # Track whether we already emitted a header for the current reasoning-only cycle
        self._reasoning_action_header_emitted = False
        # Reasoning gating to avoid duplicate reasoning per action
        self._any_action_header_emitted = False
        self._reasoning_emitted_since_last_action_header = False

        # Operation state
        self._report_generated = False
        self._report_generation_active = False
        self._evaluation_report_path: Optional[str] = None
        self._completed_report_path: Optional[str] = None
        self._assessment_completion_emitted = False

        # Termination tracking (workflow completion, user abort, or budget limit)
        self._termination_emitted = False
        self._termination_reason: Optional[str] = None
        # Track python_repl preview emission per tool id to suppress generic completion
        self._python_preview_emitted = set()

        # Initialize tool emitter
        self.tool_emitter = ToolEventEmitter(self.emit_ui_event)

        # Metrics update thread
        self._metrics_thread = None
        self._stop_metrics = False
        self._stop_metrics_event = threading.Event()
        self._last_agent = None  # Store agent reference for metrics

        # Emit initial metrics
        if self.emit_operation_init:
            self._emit_initial_metrics()

        # Start periodic metrics updates
        if self.start_metrics_thread:
            self._start_metrics_thread()

        # Emit operation initialization details if provided
        try:
            if not self.emit_operation_init:
                return
            op_event = {
                "type": "operation_init",
                "operation_id": self.operation_id,
                "model_id": self.model_id,
            }

            # Merge provided context
            if isinstance(self.init_context, dict):
                op_event.update(self.init_context)

            # Best-effort defaults for memory backend if not supplied
            memory_info = op_event.get("memory", {}) or {}
            if "backend" not in memory_info:
                if os.getenv("MEM0_API_KEY"):
                    memory_info["backend"] = "mem0_cloud"
                elif os.getenv("OPENSEARCH_HOST"):
                    memory_info["backend"] = "opensearch"
                else:
                    memory_info["backend"] = "faiss"
            op_event["memory"] = memory_info

            # UI mode hint
            if "ui_mode" not in op_event:
                op_event["ui_mode"] = os.getenv("CYBER_UI_MODE", "cli").lower()

            self.emit_ui_event(op_event)

            # Emit startup spinner immediately after initialization
            # This provides visual feedback during model loading and first reasoning
            self.emit_ui_event(
                {"type": "thinking", "context": "startup", "urgent": True}
            )
        except Exception as e:
            logger.warning("Failed to emit operation_init event: %s", e)

    def __call__(self, **kwargs):
        """
        Process SDK callbacks and emit appropriate UI events.

        This is the main entry point for all SDK callbacks. It routes
        different callback types to appropriate handlers.

        Events are attributed to this handler's agent metadata. Multiple
        handlers can safely share one operation coordinator.
        """
        # Minimal logging for production

        # Transform SDK events to stream events. Strands can invoke callbacks
        # from multiple worker threads, so serialize mutable handler state.
        with self._state_lock:
            previous_metadata = self._active_agent_metadata
            self._active_agent_metadata = self._metadata_from_agent(kwargs.get("agent"))
            try:
                self._transform_sdk_event(kwargs)
            finally:
                self._active_agent_metadata = previous_metadata

    @property
    def action_count(self) -> int:
        with self._state_lock:
            return self._action_count

    @action_count.setter
    def action_count(self, value: int) -> None:
        with self._state_lock:
            self._action_count = value

    def emit_ui_event(self, event: Dict[str, Any]) -> None:
        """
        Emit a structured event for downstream interfaces.

        Agent metadata is attached centrally so all consumers can correlate
        interleaved multi-agent streams without tool-specific state.
        """
        try:
            event = dict(event)
            active_metadata = getattr(self, "_active_agent_metadata", None) or {}
            event.setdefault("operation_id", self.operation_id)
            event.setdefault("agent_run_id", active_metadata.get("agent_run_id") or self.agent_run_id)
            event.setdefault("agent_name", active_metadata.get("agent_name") or self.agent_name)
            event.setdefault("agent_type", active_metadata.get("agent_type") or self.agent_type)
            parent_agent_run_id = active_metadata.get("parent_agent_run_id") or self.parent_agent_run_id
            if parent_agent_run_id:
                event.setdefault("parent_agent_run_id", parent_agent_run_id)
            self.coordinator.emit(event)
        except BrokenPipeError:
            logger.debug("Frontend disconnected, skipping event %s", event.get("type"))
        except Exception as e:
            logger.error(
                f"Failed to emit event {event.get('type')}: {e}", exc_info=True
            )

    def _metadata_from_agent(self, agent: Any) -> Dict[str, str]:
        """Return Cyber-AutoAgent event metadata attached to a Strands agent."""
        if not agent:
            return {}

        metadata = {}
        agent_type = getattr(agent, "_cyber_agent_type", None)
        agent_name = getattr(agent, "_cyber_agent_name", None) or getattr(agent, "name", None)
        agent_run_id = getattr(agent, "_cyber_agent_run_id", None)
        parent_agent_run_id = getattr(agent, "_cyber_parent_agent_run_id", None)

        if agent_type:
            metadata["agent_type"] = str(agent_type)
        if agent_name:
            metadata["agent_name"] = str(agent_name)
        if agent_run_id:
            metadata["agent_run_id"] = str(agent_run_id)
        if parent_agent_run_id:
            metadata["parent_agent_run_id"] = str(parent_agent_run_id)
        return metadata

    def emit_termination(self, reason: str, message: str) -> None:
        """Emit a single termination_reason event (idempotent) with a clear final action.

        Ensures the UI sees a clean end-of-operation sequence:
        - Flush any pending reasoning
        - End any active thinking indicator
        - Emit a final action header (TERMINATED)
        - Emit the termination_reason payload
        """
        try:
            with self._state_lock:
                coordinator = self.coordinator
                if coordinator is not None and not coordinator.mark_termination(reason):
                    self._termination_emitted = True
                    self._termination_reason = coordinator.termination_reason
                    return
                if coordinator is None and self._termination_emitted:
                    return
                self._termination_emitted = True
                self._termination_reason = reason

                # Flush any accumulated reasoning so it doesn't appear after termination
                try:
                    self._emit_accumulated_reasoning(force=True)
                except Exception:
                    pass

                # End any active thinking indicator in the UI
                try:
                    self.emit_ui_event({"type": "thinking_end"})
                except Exception:
                    pass

                self.emit_ui_event(
                    {
                        "type": "progress_update",
                        "step": "TERMINATED",
                        "progressPercent": self.get_budget_progress(),
                        "operation": self.operation_id,
                        "duration": self._format_duration(self._operation_elapsed_seconds()),
                    }
                )

                # Emit termination details
                self.emit_ui_event(
                    {
                        "type": "termination_reason",
                        "reason": reason,
                        "message": message,
                        "budget": {
                            "maxDurationMinutes": self._budget_max_duration(),
                            "maxTokens": self._budget_max_tokens(),
                            "maxCost": self._budget_max_cost(),
                        },
                    }
                )
        except Exception as e:
            logger.debug("Failed to emit termination event: %s", e)

    def _transform_sdk_event(self, kwargs: Dict[str, Any]) -> None:
        """Adapt SDK callbacks to UI events.

        Delegates to small helpers to keep this method readable and testable.
        """
        # Extract common fields
        message = kwargs.get("message")
        reasoning_text = kwargs.get("reasoningText")
        data = kwargs.get("data", "")
        complete = kwargs.get("complete", False)
        current_tool_use = kwargs.get("current_tool_use")
        tool_result = kwargs.get("toolResult")

        # Track whether we saw explicit reasoning in this callback to avoid duplicate extraction
        recent_reasoning_seen = _ReasoningSeenHolder()

        # Metrics from AgentResult
        agent_result = kwargs.get("result")
        event_loop_metrics = kwargs.get("event_loop_metrics")
        if agent_result and hasattr(agent_result, "metrics"):
            event_loop_metrics = agent_result.metrics

        # 1) Reasoning first (prefer explicit reasoningText over message extraction to avoid duplicates)
        skip_message_reasoning = False
        if reasoning_text:
            self._handle_reasoning(reasoning_text)
            recent_reasoning_seen.seen = True
            skip_message_reasoning = True
        elif data and not complete:
            self._handle_streaming_reasoning(data)
            recent_reasoning_seen.seen = True

        # 2) Message (tool blocks and result blocks)
        if message and isinstance(message, dict):
            self._process_message(
                message,
                skip_reasoning_extraction=skip_message_reasoning,
                recent_reasoning_seen=recent_reasoning_seen,
            )

        # 3) Tool lifecycle
        if current_tool_use:
            self._handle_tool_announcement(current_tool_use)

        if tool_result:
            self._handle_tool_result(tool_result)

        # 3b) Alternate result keys
        self._handle_alternate_results(kwargs, tool_result_already=bool(tool_result))

        # 4) Completion and errors
        if complete or kwargs.get("is_final"):
            self._handle_completion()

        if kwargs.get("error") and "MaxTokensReached" in str(kwargs.get("error")):
            self.emit_ui_event(
                {
                    "type": "error",
                    "content": "⚠️ Token limit reached - agent cannot continue due to context size.",
                    "metadata": {"error_type": "max_tokens"},
                }
            )

        # 5) Metrics
        agent = kwargs.get("agent")
        if event_loop_metrics:
            self.process_metrics(event_loop_metrics, agent=agent)

        if agent and hasattr(agent, "event_loop_metrics"):
            setattr(self, "_last_agent", agent)
            usage = agent.event_loop_metrics.accumulated_usage
            if usage:
                self._capture_agent_usage(agent, usage)

    # -- Thread-safe token counter properties --------------------------------

    @property
    def sdk_input_tokens(self) -> int:
        """Thread-safe getter for input token count."""
        with self._metrics_lock:
            return self._current_usage_totals()["input_tokens"]

    @sdk_input_tokens.setter
    def sdk_input_tokens(self, value: int) -> None:
        """Thread-safe setter for input token count."""
        with self._metrics_lock:
            self._sdk_input_tokens = value

    @property
    def sdk_output_tokens(self) -> int:
        """Thread-safe getter for output token count."""
        with self._metrics_lock:
            return self._current_usage_totals()["output_tokens"]

    @sdk_output_tokens.setter
    def sdk_output_tokens(self, value: int) -> None:
        """Thread-safe setter for output token count."""
        with self._metrics_lock:
            self._sdk_output_tokens = value

    @property
    def sdk_cache_read_tokens(self) -> int:
        """Thread-safe getter for cache read token count."""
        with self._metrics_lock:
            return self._current_usage_totals()["cache_read_tokens"]

    @sdk_cache_read_tokens.setter
    def sdk_cache_read_tokens(self, value: int) -> None:
        """Thread-safe setter for cache read token count."""
        with self._metrics_lock:
            self._sdk_cache_read_tokens = value

    @property
    def sdk_cache_write_tokens(self) -> int:
        """Thread-safe getter for cache write token count."""
        with self._metrics_lock:
            return self._current_usage_totals()["cache_write_tokens"]

    @sdk_cache_write_tokens.setter
    def sdk_cache_write_tokens(self, value: int) -> None:
        """Thread-safe setter for cache write token count."""
        with self._metrics_lock:
            self._sdk_cache_write_tokens = value

    def _current_usage_totals(self) -> Dict[str, Any]:
        with self._metrics_lock:
            input_tokens = self._aggregate_input_tokens + self._sdk_input_tokens
            output_tokens = self._aggregate_output_tokens + self._sdk_output_tokens
            cache_read_tokens = self._aggregate_cache_read_tokens + self._sdk_cache_read_tokens
            cache_write_tokens = self._aggregate_cache_write_tokens + self._sdk_cache_write_tokens
            cost = self._aggregate_cost

            cache = self._agent_usage_cache
            for entry in list(cache.values()):
                input_tokens += int(entry.input_tokens)
                output_tokens += int(entry.output_tokens)
                cache_read_tokens += int(entry.cache_read_tokens)
                cache_write_tokens += int(entry.cache_write_tokens)
                cost += float(entry.cost)

            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "cost": cost,
            }

    def _publish_usage_to_coordinator(self) -> None:
        try:
            totals = self._current_usage_totals()
            coordinator = self.coordinator
            if coordinator is not None:
                coordinator.update_usage(
                    self.agent_run_id,
                    _AgentUsageEntry(
                        input_tokens=int(totals["input_tokens"]),
                        output_tokens=int(totals["output_tokens"]),
                        cache_read_tokens=int(totals["cache_read_tokens"]),
                        cache_write_tokens=int(totals["cache_write_tokens"]),
                        cost=float(self._compute_total_cost_from_usage()),
                    ),
                )
        except Exception:
            pass

    def _operation_usage_totals(self) -> Dict[str, Any]:
        self._publish_usage_to_coordinator()
        coordinator = self.coordinator
        if coordinator is None:
            return self._current_usage_totals()
        usage = coordinator.current_usage()
        return {
            "input_tokens": int(usage.input_tokens),
            "output_tokens": int(usage.output_tokens),
            "cache_read_tokens": int(usage.cache_read_tokens),
            "cache_write_tokens": int(usage.cache_write_tokens),
            "cost": float(usage.cost),
        }

    def _report_budget_estimate(self) -> ReportBudgetEstimate:
        coordinator = self.coordinator
        if coordinator is None:
            return ReportBudgetEstimate()
        return coordinator.report_budget_estimate(
            provider_id=self.provider_id,
            model_id=self.model_id,
            models_client=self.models_client,
            pricing_fallback={
                "input": self.pricing_input,
                "output": self.pricing_output,
            },
            pricing_override=self._pricing_override_configured,
        )

    def _report_budget_estimate_payload(self) -> Dict[str, Any]:
        estimate = self._report_budget_estimate()
        return {
            "inputTokens": estimate.input_tokens,
            "outputTokens": estimate.output_tokens,
            "totalTokens": estimate.total_tokens,
            "cost": estimate.cost,
            "findings": estimate.findings,
            "observations": estimate.observations,
            "remainingSteps": estimate.remaining_steps,
        }

    def _budgeted_usage_totals(self) -> Dict[str, Any]:
        totals = self._operation_usage_totals()
        estimate = self._report_budget_estimate()
        input_tokens = int(totals["input_tokens"]) + int(estimate.input_tokens)
        output_tokens = int(totals["output_tokens"]) + int(estimate.output_tokens)
        return {
            **totals,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": float(totals.get("cost", self._compute_total_cost_from_usage())) + float(estimate.cost),
            "report_estimate": estimate,
        }

    def set_report_items(self, items: List[Dict[str, Any]]) -> None:
        if self.coordinator is not None:
            self.coordinator.set_report_items(items, model_id=self.model_id)

    def mark_report_step_started(self) -> None:
        if self.coordinator is not None:
            self.coordinator.mark_report_step_started()
        with self._metrics_lock:
            self._report_metrics_input_baseline = 0
            self._report_metrics_output_baseline = 0
            self._report_metrics_cache_read_baseline = 0
            self._report_metrics_cache_write_baseline = 0

    def record_report_metrics(self, event_loop_metrics: Any, agent: Any = None) -> None:
        self.process_metrics(event_loop_metrics, agent=agent)

    def _get_or_assign_agent_usage_uuid(self, agent: Any) -> str:
        agent_uuid = getattr(agent, _AGENT_USAGE_UUID_ATTR, None)
        if agent_uuid:
            return str(agent_uuid)
        agent_uuid = str(uuid.uuid4())
        try:
            setattr(agent, _AGENT_USAGE_UUID_ATTR, agent_uuid)
        except Exception:
            agent_uuid = f"unassignable:{id(agent)}"
        return agent_uuid

    def _aggregate_usage_entry(self, entry: _AgentUsageEntry) -> None:
        with self._metrics_lock:
            self._aggregate_input_tokens += int(entry.input_tokens)
            self._aggregate_output_tokens += int(entry.output_tokens)
            self._aggregate_cache_read_tokens += int(entry.cache_read_tokens)
            self._aggregate_cache_write_tokens += int(entry.cache_write_tokens)
            self._aggregate_cost += float(entry.cost)

    def _capture_agent_usage(self, agent: Any, usage: Dict[str, Any]) -> None:
        if not agent or not usage:
            return
        with self._metrics_lock:
            agent_uuid = self._get_or_assign_agent_usage_uuid(agent)
            input_tokens = int(usage.get("inputTokens", 0) or 0)
            output_tokens = int(usage.get("outputTokens", 0) or 0)
            cache_read_tokens = int(usage.get("cacheReadInputTokens", 0) or 0)
            cache_write_tokens = int(usage.get("cacheWriteInputTokens", 0) or 0)
            cost = self._compute_cost_from_metrics(
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                agent=agent,
            )

            self._agent_usage_cache[agent_uuid] = _AgentUsageEntry(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                cost=cost,
            )
            self._agent_usage_cache.move_to_end(agent_uuid)

            while len(self._agent_usage_cache) > self._agent_usage_cache_size:
                _evicted_uuid, evicted = self._agent_usage_cache.popitem(last=False)
                self._aggregate_usage_entry(evicted)

    def _capture_report_usage(self, usage: Dict[str, Any]) -> None:
        """Accumulate no-agent report metrics as per-step deltas.

        Report generation creates short-lived agents. Some callbacks only expose
        result metrics and not the agent object, so their accumulated usage resets
        for each report step. Treating those values as replacement SDK totals can
        make operation totals decrease between steps.
        """
        input_tokens = int(usage.get("inputTokens", 0) or 0)
        output_tokens = int(usage.get("outputTokens", 0) or 0)
        cache_read_tokens = int(usage.get("cacheReadInputTokens", 0) or 0)
        cache_write_tokens = int(usage.get("cacheWriteInputTokens", 0) or 0)

        with self._metrics_lock:
            input_delta = max(0, input_tokens - self._report_metrics_input_baseline)
            output_delta = max(0, output_tokens - self._report_metrics_output_baseline)
            cache_read_delta = max(0, cache_read_tokens - self._report_metrics_cache_read_baseline)
            cache_write_delta = max(0, cache_write_tokens - self._report_metrics_cache_write_baseline)

            self._aggregate_input_tokens += input_delta
            self._aggregate_output_tokens += output_delta
            self._aggregate_cache_read_tokens += cache_read_delta
            self._aggregate_cache_write_tokens += cache_write_delta
            self._aggregate_cost += self._compute_cost_from_metrics(
                input_delta,
                output_delta,
                cache_read_delta,
                cache_write_delta,
            )

            self._report_metrics_input_baseline = max(self._report_metrics_input_baseline, input_tokens)
            self._report_metrics_output_baseline = max(self._report_metrics_output_baseline, output_tokens)
            self._report_metrics_cache_read_baseline = max(
                self._report_metrics_cache_read_baseline,
                cache_read_tokens,
            )
            self._report_metrics_cache_write_baseline = max(
                self._report_metrics_cache_write_baseline,
                cache_write_tokens,
            )

    # -- Helper methods ----------------------------------------------------

    def _handle_reasoning(self, text: str) -> None:
        """Handle reasoning text with per-agent TTL dedupe, then accumulate.

        We avoid emitting identical reasoning fragments repeatedly within a short window.
        """
        if not text:
            return
        try:
            agent_key = self.agent_run_id or "main"
            # Normalize whitespace to compare fragments robustly
            norm = re.sub(r"\s+", " ", str(text))
            if not norm.strip():
                self._accumulate_reasoning_text(text)
                return
            now = time.time()
            recent = self._recent_reasoning_by_agent.get(agent_key, {})
            # Prune expired entries
            if recent:
                for k, ts in list(recent.items()):
                    if now - ts > self._recent_reasoning_ttl:
                        del recent[k]
            # Skip if we've seen this fragment very recently for this agent
            if ' ' in norm and len(norm) > 10 and norm in recent:
                return
            recent[norm] = now
            self._recent_reasoning_by_agent[agent_key] = recent
        except Exception:
            # Never break reasoning on dedupe errors
            pass
        # Accumulate for later flush
        # Do not advance or pre-emit action headers for reasoning-only turns; actions are driven by tool usage
        self._accumulate_reasoning_text(text)

    def _handle_streaming_reasoning(self, data: str) -> None:
        # Do not advance or pre-emit action headers for reasoning streaming; actions are driven by tools
        # Accumulate only; avoid emitting incremental deltas to prevent duplicate fragments
        if data and not data.startswith("[") and not data.startswith("{"):
            self._accumulate_reasoning_text(data)

    def _tool_use_id(self, tool_use: Dict[str, Any]) -> str:
        return tool_use.get("_toolUseId") or tool_use.get("id") or tool_use.get("toolUseId")

    def _handle_tool_announcement(self, tool_use: Dict[str, Any]) -> None:
        self._process_tool_announcement(tool_use)

    def _handle_tool_result(self, tool_result: Any) -> None:
        self._process_tool_result_from_message(tool_result)

    def _handle_alternate_results(
        self, kwargs: Dict[str, Any], tool_result_already: bool
    ) -> None:
        for alt_key in [
            "result",
            "tool_result",
            "execution_result",
            "response",
            "output",
        ]:
            result_data = kwargs.get(alt_key)
            if result_data is None:
                continue
            if alt_key == "result" and hasattr(result_data, "metrics"):
                continue
            if alt_key == "tool_result" and tool_result_already:
                continue
            if isinstance(result_data, str):
                result_data = {"content": [{"text": result_data}], "status": "success"}
            self._process_tool_result_from_message(result_data)

    def _process_message(
            self,
            message: Dict[str, Any],
            skip_reasoning_extraction: bool = False,
            recent_reasoning_seen: Optional[_ReasoningSeenHolder] = None,
    ) -> None:
        """Process message objects to track actions and extract content.

        Args:
            message: The SDK message dict
            skip_reasoning_extraction: When True, do not extract reasoning text from message content
                                      (used to avoid duplication when reasoningText is provided)
            recent_reasoning_seen: Mutable per-callback holder used to mark that reasoning was already handled
                                   earlier in this callback (dataclass holder pattern to avoid shared state).
        """
        content = message.get("content", [])
        if recent_reasoning_seen is None:
            recent_reasoning_seen = _ReasoningSeenHolder()

        # Check if message contains tool usage
        has_tool_use = any(
            isinstance(block, dict)
            and (block.get("type") == "tool_use" or "toolUse" in block)
            for block in content
        )

        # Handle action progression
        if message.get("role") == "assistant":
            # Identify the very first assistant turn (before any actions are counted)
            initial_assistant = self.action_count == 0

            if has_tool_use:
                # Reset batch tracking for new assistant response with tools
                # This allows multiple tools in the same response to share one action header
                if hasattr(self, "_action_header_emitted_for_batch"):
                    delattr(self, "_action_header_emitted_for_batch")
                if hasattr(self, "_tools_in_action_count"):
                    # Clear the list but keep the attribute to signal we're in a action
                    self._tools_in_action_count = []

                if initial_assistant:
                    # Do not emit header yet; let the first tool announcement emit action 1
                    # Ensure a header will be emitted on tool announcement
                    self.pending_action_header = True
                else:
                    # Defer action header to tool announcement to keep a single emission path
                    # Ensure a header will be emitted on tool announcement
                    self.pending_action_header = True
            else:
                # Pure reasoning turn without tools: do not advance actions or emit headers
                if initial_assistant:
                    # Keep initial reasoning above the first action header
                    pass
                elif self._reasoning_action_header_emitted:
                    # Reset any prior flag
                    self._reasoning_action_header_emitted = False
                else:
                    # No-op: keep reasoning adjacent to upcoming tool action
                    pass

            # Count output tokens
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    pass  # Token counting via SDK metrics

        elif message.get("role") == "user":
            # Count input tokens
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    pass  # Token counting via SDK metrics

        # Process tool uses in message content
        for block in content:
            if isinstance(block, dict):
                # Handle tool use blocks
                if "toolUse" in block:
                    tool_use = block["toolUse"]
                    # Always process tool announcements - handler will determine if events needed
                    self._process_tool_announcement(tool_use)
                elif block.get("type") == "tool_use":
                    # Alternative format
                    self._process_tool_announcement(block)

        # Process tool results in message content
        for block in content:
            if isinstance(block, dict):
                if "toolResult" in block:
                    self._process_tool_result_from_message(block["toolResult"])
                elif "toolResponse" in block:
                    self._process_tool_result_from_message(block["toolResponse"])
                elif block.get("type") == "tool_result":
                    self._process_tool_result_from_message(block)

    def _handoff_input_complete(self, tool_input: Any) -> bool:
        try:
            return (
                isinstance(tool_input, dict)
                and bool(tool_input.get("handoff_to"))
                and bool(tool_input.get("message"))
            )
        except Exception:
            return False

    def _process_tool_announcement(self, tool_use: Dict[str, Any]) -> None:
        """Process tool usage announcements.

        Emits generic tool lifecycle events from SDK callbacks. Consumers dedupe
        duplicate hook/callback emissions by tool id.
        """
        tool_name = tool_use.get("name", "")
        tool_id = self._tool_use_id(tool_use)
        raw_input = tool_use.get("input", {})
        tool_input = self._parse_tool_input_from_stream(raw_input)

        # Only process new tools
        if tool_id and tool_id not in self.announced_tools:
            # Ensure a action header will be emitted for each new tool.
            # IMPORTANT: Only emit header for the FIRST tool in a multi-tool response
            # Models can invoke multiple tools in parallel within the same response
            if self.action_count == 0 or not hasattr(self, "_tools_in_action_count"):
                self._tools_in_action_count = []
                self.pending_action_header = True
                self._emit_accumulated_reasoning()

            # Track this tool as part of current action
            self._tools_in_action_count.append(tool_id)

            # Emit progress update ONLY if pending (i.e., this is the first tool in the response)
            if (self.action_count == 0 or self.pending_action_header) and tool_name not in _PLANNING_TOOL_NAMES:
                if self.action_count == 0:
                    # First tool ever - increment action
                    self.action_count += 1
                elif self.pending_action_header and not hasattr(
                    self, "_action_header_emitted_for_batch"
                ):
                    # First tool in this batch - increment action
                    self.action_count += 1
                    self._action_header_emitted_for_batch = True

                if self.should_stop():
                    raise BudgetLimitReached("Budget limit reached")

                self._record_action_boundary()
                self.pending_action_header = False

            # Track tool
            self.announced_tools.add(tool_id)
            self.tools_used.add(tool_name)
            # Increment per-tool usage count once per announced tool id
            try:
                self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
                if self.coordinator is not None:
                    self.coordinator.record_tool(tool_name)
            except Exception as e:
                # Defensive: never allow metrics to break streaming
                logger.debug("Incrementing tool_counts", exc_info=e)
            self.tool_input_buffer[tool_id] = tool_input
            # Track tool name for correct attribution
            self.tool_name_buffer[tool_id] = tool_name

            # Emit tool_start once meaningful input is available.
            has_meaningful_input = (
                bool(tool_input)
                and tool_input != {}
                and self._is_valid_input(tool_input)
            )

            # For handoff_to_agent, require complete input (handoff_to and message) to avoid duplicate args
            if tool_name == "handoff_to_agent" and not self._handoff_input_complete(
                tool_input
            ):
                has_meaningful_input = False

            # Emit tool headers generically from the callback handler. Legacy
            # ReactHooks may also emit them; consumers dedupe by tool_id.
            should_emit = has_meaningful_input

            if should_emit:
                # Suppress OutputInterceptor during tool execution
                set_tool_execution_state(True)

                # Record start time for duration calculation
                if tool_id:
                    self.tool_start_times[tool_id] = time.time()

                # Build tool_start event with all necessary information
                tool_event = {
                    "type": "tool_start",
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "tool_input": tool_input,
                }

                # Mark as having complete input if it's meaningful
                if has_meaningful_input:
                    self.tools_with_complete_input.add(tool_id)

                self.emit_ui_event(tool_event)

                # Also emit tool_invocation_start for compatibility
                invocation_event = {
                    "type": "tool_invocation_start",
                    "tool_name": tool_name,
                }
                self.emit_ui_event(invocation_event)

            # Emit tool-specific events for all tools.
            if tool_input and self._is_valid_input(tool_input):
                self.tool_emitter.emit_tool_specific_events(tool_name, tool_input)

            # Emit thinking animation ONLY after a tool_start has been emitted
            # This prevents the UI from showing an 'Executing' spinner without a corresponding tool header
            if should_emit:
                current_time_ms = int(time.time() * 1000)
                self.emit_ui_event(
                    {
                        "type": "thinking",
                        "context": "tool_execution",
                        "startTime": current_time_ms,
                    }
                )

            # Multi-agent coordination is represented by generic agent metadata,
            # not tool-specific lifecycle events.

        # Handle streaming updates - buffer and emit ONLY when complete
        elif tool_id in self.announced_tools and raw_input:
            old_input = self.tool_input_buffer.get(tool_id, {})
            new_input = self._parse_tool_input_from_stream(raw_input)

            # Lookup tool_name for this tool_id
            tool_name = self.tool_name_buffer.get(tool_id, "")

            # Update buffer with latest input
            self.tool_input_buffer[tool_id] = new_input

            # Check if we have complete, usable input (not partial JSON)
            is_partial_json = (
                isinstance(new_input, dict)
                and len(new_input) == 1
                and "value" in new_input
                and isinstance(new_input.get("value"), str)
            )

            # Don't emit anything if we have partial JSON
            if is_partial_json:
                return

            # Check if this is a meaningful update from empty/partial to complete
            was_empty_or_partial = (
                not old_input
                or old_input == {}
                or (
                    isinstance(old_input, dict)
                    and len(old_input) == 1
                    and "value" in old_input
                )
            )

            has_complete_content = new_input and new_input != {} and not is_partial_json

            # Only emit when we transition to complete content
            if was_empty_or_partial and has_complete_content:
                # Suppress OutputInterceptor during tool execution
                set_tool_execution_state(True)

                # Emit proper tool_start if not already done once streamed input completes.
                if (
                        tool_id in self.announced_tools
                    and tool_id not in self.tools_with_complete_input
                        and not (
                        tool_name == "handoff_to_agent"
                        and self._handoff_input_complete(new_input)
                )
                ):
                    tool_event = {
                        "type": "tool_start",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "tool_input": new_input,
                    }
                    self.emit_ui_event(tool_event)
                    # Also emit tool_invocation_start for compatibility
                    invocation_event = {
                        "type": "tool_invocation_start",
                        "tool_name": tool_name,
                    }
                    self.emit_ui_event(invocation_event)
                    # Mark as having complete input now
                    self.tools_with_complete_input.add(tool_id)

                # For handoff_to_agent, emit tool_start now that input is complete and skip tool_input_update.
                elif (
                        tool_name == "handoff_to_agent"
                    and tool_id in self.announced_tools
                    and tool_id not in self.tools_with_complete_input
                    and self._handoff_input_complete(new_input)
                ):
                    self.emit_ui_event(
                        {
                            "type": "tool_start",
                            "tool_name": tool_name,
                            "tool_id": tool_id,
                            "tool_input": new_input,
                        }
                    )
                    self.emit_ui_event(
                        {
                            "type": "tool_invocation_start",
                            "tool_name": tool_name,
                        }
                    )
                    self.tools_with_complete_input.add(tool_id)
                    # Skip tool_input_update for handoff_to_agent to avoid duplicate args listing
                    return

                # Emit a tool_input_update to let the UI refresh placeholders
                try:
                    if tool_id:
                        # Skip tool_input_update for handoff_to_agent to avoid duplicated fields when UI merges events
                        if tool_name != "handoff_to_agent":
                            self.emit_ui_event(
                                {
                                    "type": "tool_input_update",
                                    "tool_id": tool_id,
                                    "tool_input": new_input,
                                }
                            )
                except Exception:
                    pass

                # Emit tool-specific events now that we have the real input.
                if self._is_valid_input(new_input):
                    self.tool_emitter.emit_tool_specific_events(tool_name, new_input)

    def _process_tool_result_from_message(self, tool_result: Any) -> None:
        """Process tool execution results."""
        # Clear tool execution flag and get buffered output
        set_tool_execution_state(False)
        buffered_output = get_buffered_output()

        # Convert result to dict format early to extract tool_use_id
        if hasattr(tool_result, "__dict__"):
            tool_result_dict = tool_result.__dict__
        elif isinstance(tool_result, dict):
            tool_result_dict = tool_result
        else:
            tool_result_dict = {
                "content": [{"text": str(tool_result)}],
                "status": "success",
            }

        # Extract tool_use_id and get correct tool name early for proper attribution
        tool_use_id = self._tool_use_id(tool_result_dict)
        if not tool_use_id:
            # Drop tool results that cannot be safely attributed to a specific tool invocation
            logger.warning(
                "Dropping tool result without toolUseId/id: %s",
                tool_result_dict,
            )
            return
        tool_name = self.tool_name_buffer.get(tool_use_id) or tool_result_dict.get("name") or "unknown_tool"

        # Stop thinking animation
        self.emit_ui_event({"type": "thinking_end"})

        # Debug logging for shell tool results
        if tool_name == "shell":
            logger.debug(
                "Shell tool result structure: %s",
                tool_result_dict.keys()
                if isinstance(tool_result_dict, dict)
                else type(tool_result_dict),
            )
            if "content" in tool_result_dict:
                logger.debug(
                    "Shell content items: %d", len(tool_result_dict.get("content", []))
                )
                for i, item in enumerate(
                    tool_result_dict.get("content", [])[:3]
                ):  # Log first 3 items
                    logger.debug(
                        "Content item %d: %s",
                        i,
                        item if isinstance(item, dict) else str(item)[:100],
                    )

        # Extract result details
        content_items = tool_result_dict.get("content", [])
        status = tool_result_dict.get("status", "success")

        # If python_repl produced stdout/stderr, emit a brief preview so it's not lost
        try:
            if tool_name == "python_repl":
                # Stdout preview
                if buffered_output and str(buffered_output).strip():
                    lines = str(buffered_output).splitlines()
                    max_lines = 8
                    preview_lines = lines[:max_lines]
                    remainder = len(lines) - max_lines
                    if remainder > 0:
                        preview_lines.append(f"... ({remainder} more lines)")
                    preview = "\n".join(preview_lines).strip()
                    # Safety cap to avoid giant bursts
                    if len(preview) > 1200:
                        preview = preview[:1200] + "\n... (truncated)"
                    if preview:
                        self.emit_ui_event(
                            {
                                "type": "output",
                                "content": preview,
                                "metadata": {
                                    "fromToolBuffer": True,
                                    "tool": "python_repl",
                                    "preview": True,
                                    "stderr": False,
                                },
                            }
                        )
                        if tool_use_id:
                            self._python_preview_emitted.add(tool_use_id)
                # Stderr preview (very short)
                try:
                    buffered_err = get_buffered_error_output()
                except Exception:
                    buffered_err = ""
                if buffered_err and str(buffered_err).strip():
                    err_lines = str(buffered_err).splitlines()
                    err_max_lines = 4
                    err_preview_lines = err_lines[:err_max_lines]
                    err_remainder = len(err_lines) - err_max_lines
                    if err_remainder > 0:
                        err_preview_lines.append(f"... ({err_remainder} more lines)")
                    err_preview = "\n".join(err_preview_lines).strip()
                    if len(err_preview) > 800:
                        err_preview = err_preview[:800] + "\n... (truncated)"
                    if err_preview:
                        self.emit_ui_event(
                            {
                                "type": "output",
                                "content": err_preview,
                                "metadata": {
                                    "fromToolBuffer": True,
                                    "tool": "python_repl",
                                    "preview": True,
                                    "stderr": True,
                                },
                            }
                        )
                        if tool_use_id:
                            self._python_preview_emitted.add(tool_use_id)
        except Exception:
            pass

        # Get original tool input
        tool_input = self.tool_input_buffer.get(tool_use_id, {})

        success = status != "error"

        # Update live metrics for memory operations and evidence collection
        try:
            if tool_name in {"mem0_store"} and success:
                # Increment memory operation count on successful storage actions
                if isinstance(tool_input, dict):
                    self.memory_ops += 1
                    # Only count evidence for store actions with report-generating categories.
                    # Categories per memory.py: finding, signal, observation, discovery
                    if tool_name == "mem0_store":
                        metadata = (
                            tool_input.get("metadata", {})
                            if isinstance(tool_input.get("metadata"), dict)
                            else {}
                        )
                        category = str(metadata.get("category", "")).lower()
                        severity = str(metadata.get("severity", "") or tool_input.get("severity", ""))
                        content = tool_input.get("content") or tool_input.get("memory") or ""
                        if category in ("finding", "signal", "observation", "discovery"):
                            self.evidence_count += 1
                            if self.coordinator is not None:
                                self.coordinator.record_memory(
                                    evidence=True,
                                    category=category,
                                    severity=severity,
                                    content_length=len(str(content)),
                                    model_id=self.model_id,
                                )
                        elif self.coordinator is not None:
                            self.coordinator.record_memory(
                                evidence=False,
                                category=category,
                                severity=severity,
                                content_length=len(str(content)),
                                model_id=self.model_id,
                            )
        except Exception:
            # Never allow metrics update errors to disrupt output
            pass

        # Calculate duration if we have start time
        duration = None
        if tool_use_id and tool_use_id in self.tool_start_times:
            duration = time.time() - self.tool_start_times[tool_use_id]
            del self.tool_start_times[tool_use_id]  # Clean up

        # Defer tool_end emission until after output so reasoning can appear below output
        _deferred_tool_end = {
            "tool_name": tool_name,
            "tool_id": tool_use_id,
            "success": success,
        }
        if duration is not None:
            _deferred_tool_end["duration"] = f"{duration:.2f}s"
        # Handle errors with tool-specific processing
        if status == "error":
            error_text = ""
            for item in content_items:
                if isinstance(item, dict) and "text" in item:
                    error_text += item["text"] + "\n"

            if error_text.strip():
                # Combine buffered output with error text for single emission
                combined_output = ""
                if buffered_output:
                    combined_output = buffered_output + "\n"

                # Process errors through tool-specific handlers for cleaner display
                if tool_name == "shell":
                    clean_error = self._parse_shell_tool_output_detailed(
                        error_text.strip()
                    )
                elif tool_name == "http_request":
                    clean_error = self._parse_http_tool_output(error_text.strip())
                else:
                    clean_error = error_text.strip()
                combined_output += clean_error

                # Detect timeout specifics for clearer UI messaging
                timeout_seconds = None
                try:
                    # Common patterns: "timed out after 30 seconds", TimeoutExpired, etc.
                    m = re.search(
                        r"timed out after\s+(\d+)\s*seconds?",
                        clean_error,
                        re.IGNORECASE,
                    )
                    if m:
                        timeout_seconds = int(m.group(1))
                except Exception:
                    pass
                requested_timeout = None
                try:
                    requested_timeout = (
                        tool_input.get("timeout")
                        if isinstance(tool_input, dict)
                        else None
                    )
                except Exception:
                    requested_timeout = None

                # Emit a structured error event with guidance if this looks like a timeout
                looks_like_timeout = (
                    ("timed out" in clean_error.lower())
                    or ("timeout" in clean_error.lower())
                    or ("TimeoutExpired" in clean_error)
                )
                if looks_like_timeout:
                    friendly_msg_lines = [
                        "Shell command timed out"
                        + (
                            f" after {timeout_seconds}s"
                            if timeout_seconds
                            else (
                                f" after {requested_timeout}s"
                                if requested_timeout
                                else ""
                            )
                        )
                        + ".",
                        "Tip: Re-run with a higher timeout (e.g., add 'timeout': 300 to the shell tool input) or set SHELL_DEFAULT_TIMEOUT in your environment.",
                    ]
                    self.emit_ui_event(
                        {
                            "type": "error",
                            "content": "\n".join(friendly_msg_lines),
                            "metadata": {
                                "type": "timeout",
                                "tool": tool_name,
                                "timeout": timeout_seconds or requested_timeout,
                            },
                        }
                    )

                # Emit single consolidated output event (raw/cleaned details)
                self.emit_ui_event(
                    {
                        "type": "output",
                        "content": combined_output.strip(),
                        "metadata": {"fromToolBuffer": True, "tool": tool_name},
                    }
                )

                # Now emit tool completion after consolidated output is sent
                self.emit_ui_event(
                    {
                        "type": "tool_invocation_end",
                        "success": success,
                        "tool_name": tool_name,
                    }
                )
                # Emit tool_end after output and invocation_end
                self.emit_ui_event({"type": "tool_end", **_deferred_tool_end})

                # Mark that we've emitted output for this tool invocation
                if tool_use_id:
                    self.tool_use_output_emitted[tool_use_id] = True
            return

        # If we reach here, there was no buffered output, so process normally
        # But first check if output was already emitted
        if tool_use_id and self.tool_use_output_emitted.get(tool_use_id, False):
            return

        # Build output_text from content items (ensure defined before use)
        output_text = ""
        try:
            parts = []
            for item in content_items:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
            output_text = "\n".join(parts).strip()
        except Exception:
            # Fallback to stringified tool_result_dict if unexpected structure
            output_text = str(tool_result_dict)

        # Clean/parse known tool outputs
        if tool_name == "shell":
            output_text = self._parse_shell_tool_output_detailed(output_text)
        elif tool_name == "editor":
            output_text = self._parse_editor_tool_output(output_text)

        if not output_text.strip():
            # For python_repl with no textual output, emit executed code and suppress generic message if a preview was shown
            try:
                if tool_name == "python_repl":
                    preview_emitted = bool(
                        tool_use_id
                        and tool_use_id
                        in self._python_preview_emitted
                    )
                    code_input = self.tool_input_buffer.get(tool_use_id, {})
                    code_text = self._extract_code_from_input(code_input)
                    if code_text and code_text.strip():
                        code_event = {
                            "type": "tool_output",
                            "tool": "python_repl",
                            "status": "success",
                            "output": {"text": code_text},
                        }
                    self.emit_ui_event(code_event)
                    if preview_emitted:
                        if tool_use_id:
                            self.tool_use_output_emitted[tool_use_id] = True
                        # Emit tool completion without generic placeholder
                        self.emit_ui_event(
                            {
                                "type": "tool_invocation_end",
                                "success": success,
                                "tool_name": tool_name,
                            }
                        )
                        self.emit_ui_event({"type": "tool_end", **_deferred_tool_end})
                        return
            except Exception:
                pass

            # Emit generic completion after code emission (if any)
            self.emit_ui_event(
                {
                    "type": "output",
                    "content": "Command completed",
                    "metadata": {"fromToolBuffer": True, "tool": tool_name},
                }
            )
            if tool_use_id:
                self.tool_use_output_emitted[tool_use_id] = True
            # Emit tool completion
            self.emit_ui_event(
                {
                    "type": "tool_invocation_end",
                    "success": success,
                    "tool_name": tool_name,
                }
            )
            # Emit tool_end after output and invocation_end
            self.emit_ui_event({"type": "tool_end", **_deferred_tool_end})
            return

        # Check if we already processed this exact output
        output_key = f"{tool_use_id or ''}:{hash(output_text.strip())}"
        if (
            hasattr(self, "_processed_outputs")
            and output_key in self._processed_outputs
        ):
            return  # Skip duplicate output

        # Initialize tracking if not exists
        if not hasattr(self, "_processed_outputs"):
            self._processed_outputs = set()

        # Mark this output as processed
        self._processed_outputs.add(output_key)
        # Mark meaningful output for this tool invocation
        if tool_use_id:
            self.tool_use_output_emitted[tool_use_id] = True
        # Mark all tool outputs with metadata to prevent truncation
        self.emit_ui_event(
            {
                "type": "output",
                "content": output_text.strip(),
                "metadata": {"fromToolBuffer": True, "tool": tool_name},
            }
        )

        tool_inv_end_event = {
            "type": "tool_invocation_end",
            "success": success,
            "tool_name": tool_name,
        }
        self.emit_ui_event(tool_inv_end_event)
        # Emit tool_end after output and invocation_end
        self.emit_ui_event({"type": "tool_end", **_deferred_tool_end})

        # Ensure exactly one reasoning per action: if none occurred in this action, emit a brief rationale now
        try:
            if self._reasoning_required_for_current_action:
                fallback = f"Reviewed {tool_name or 'tool'} results and determined next action."
                self.emit_ui_event({"type": "reasoning", "content": fallback})
                self._emitted_any_reasoning = True
                self._reasoning_emitted_since_last_action_header = True
                self._reasoning_required_for_current_action = False
        except Exception:
            pass

    def _parse_editor_tool_output(self, output_text: str) -> str:
        """Parse editor tool output - keep raw output to show what was changed."""
        if not output_text:
            return ""

        # Just return the raw output - user wants to see full details including Old/New strings
        # This shows exactly what was replaced which is important for understanding changes
        return output_text.strip()

    def _parse_shell_tool_output(self, output_text: str) -> str:
        """Minimal parsing of shell tool output - show raw output as requested."""
        if not output_text:
            return ""

        # Only remove duplicate command echoes that start with ⎿
        # since those are already shown in the tool invocation
        lines = output_text.split("\n")
        filtered_lines = []
        for line in lines:
            # Skip lines that are just command echoes (they start with ⎿)
            if line.strip().startswith("⎿"):
                continue
            filtered_lines.append(line)

        # Return the output with minimal filtering - user wants raw output
        return "\n".join(filtered_lines).strip()

    def _parse_shell_tool_output_detailed(self, output_text: str) -> str:
        """Detailed shell parsing - not currently used but kept for reference."""
        if not output_text:
            return ""

        # Extract command info and actual output/error content
        lines = output_text.split("\n")
        command = ""
        actual_output = []
        in_output_section = False
        capture_error = False
        status = ""
        exit_code = ""

        for line in lines:
            if line.startswith("Command:"):
                command = line[8:].strip()
            elif line.startswith("Status:"):
                status = line[7:].strip()
                in_output_section = False
                capture_error = False
            elif line.startswith("Exit Code:"):
                exit_code = line[10:].strip()
            elif line.startswith("Output:"):
                in_output_section = True
                # Check for inline content
                content_after = line[7:].strip()
                if content_after:
                    actual_output.append(content_after)
                continue
            elif line.startswith("Error:"):
                in_output_section = False
                capture_error = True
                # Check for inline error message
                error_msg = line[6:].strip()
                if error_msg:
                    actual_output.append(f"Error: {error_msg}")
                continue
            elif line.startswith("Execution Summary:") or line.startswith(
                "Total commands:"
            ):
                continue  # Skip wrapper headers
            elif in_output_section:
                actual_output.append(line)
            elif capture_error and line.strip():
                actual_output.append(line)

        # If we have extracted content, return it
        if actual_output:
            return "\n".join(actual_output).strip()

        # If no output/error captured but we have command info, provide context
        # Also extract any other information from the full text that might be useful
        if command:
            # Try to extract any additional info from the original text
            additional_info = []
            for line in lines:
                # Skip already processed lines and wrapper lines
                if (
                    not line.startswith("Execution Summary:")
                    and not line.startswith("Total commands:")
                    and not line.startswith("Command:")
                    and not line.startswith("Status:")
                    and not line.startswith("Exit Code:")
                    and not line.startswith("Output:")
                    and not line.startswith("Error:")
                    and not line.startswith("Successful:")
                    and not line.startswith("Failed:")
                    and line.strip()
                ):
                    additional_info.append(line)

            if additional_info:
                return "\n".join(additional_info)
            elif status == "error" and exit_code:
                return f"Command failed: {command}\nExit code: {exit_code}\n(No output captured)"
            elif status == "success":
                return f"Command succeeded: {command}\n(No output)"
            else:
                return f"Command: {command}\nStatus: {status or 'unknown'}"

        # Return full output as fallback
        return output_text.strip()

    def _parse_http_tool_output(self, output_text: str) -> str:
        """Parse and clean HTTP tool output for display - show all content."""
        if not output_text:
            return ""

        # Return full HTTP output without truncation
        return output_text.strip()

    def _process_shell_output(
        self,
        output_text: str,
        _content_items: List,
        _status: str,
        tool_use_id: str = None,
    ) -> None:
        """Process shell command output with intelligent parsing and clean display."""
        # Skip if output was already emitted
        if tool_use_id and self.tool_use_output_emitted.get(tool_use_id, False):
            return

        if not output_text.strip():
            # Only emit generic completion if no prior meaningful output for this invocation
            self.emit_ui_event(
                {
                    "type": "output",
                    "content": "Command completed",
                    "metadata": {"fromToolBuffer": True,
                                 "tool": self.tool_name_buffer.get(tool_use_id, "unknown_tool")},
                }
            )
            if tool_use_id:
                self.tool_use_output_emitted[tool_use_id] = True
            return

        # Check if we already processed this exact output
        output_key = f"{tool_use_id}:{hash(output_text.strip())}"
        if (
            hasattr(self, "_processed_outputs")
            and output_key in self._processed_outputs
        ):
            return  # Skip duplicate output

        # Initialize tracking if not exists
        if not hasattr(self, "_processed_outputs"):
            self._processed_outputs = set()

        # Parse and clean shell tool output
        clean_output = self._parse_shell_tool_output_detailed(output_text.strip())

        # Agent tracking handled through explicit events, not text parsing

        # Mark this output as processed
        self._processed_outputs.add(output_key)
        if tool_use_id:
            self.tool_use_output_emitted[tool_use_id] = True
        # Always mark shell output with metadata to prevent truncation
        self.emit_ui_event(
            {
                "type": "output",
                "content": clean_output,
                "metadata": {"fromToolBuffer": True, "tool": "shell"},
            }
        )

    def _process_http_output(
        self,
        output_text: str,
        _content_items: List,
        _status: str,
        tool_use_id: str = None,
    ) -> None:
        """Process HTTP request output with intelligent parsing and clean display."""
        # Skip if output was already emitted
        if tool_use_id and self.tool_use_output_emitted.get(tool_use_id, False):
            return

        if not output_text.strip():
            # Only emit generic completion if no prior meaningful output for this invocation
            self.emit_ui_event(
                {
                    "type": "output",
                    "content": "Request completed",
                    "metadata": {"fromToolBuffer": True,
                                 "tool": self.tool_name_buffer.get(tool_use_id, "unknown_tool")},
                }
            )
            if tool_use_id:
                self.tool_use_output_emitted[tool_use_id] = True
            return

        # Check if we already processed this exact output
        output_key = f"{tool_use_id}:{hash(output_text.strip())}"
        if (
            hasattr(self, "_processed_outputs")
            and output_key in self._processed_outputs
        ):
            return  # Skip duplicate output

        # Initialize tracking if not exists
        if not hasattr(self, "_processed_outputs"):
            self._processed_outputs = set()

        # Parse and clean HTTP tool output
        clean_output = self._parse_http_tool_output(output_text.strip())

        # Agent tracking handled through explicit events, not text parsing

        # Mark this output as processed
        self._processed_outputs.add(output_key)
        if tool_use_id:
            self.tool_use_output_emitted[tool_use_id] = True
        # Always mark HTTP output with metadata to prevent truncation
        self.emit_ui_event(
            {
                "type": "output",
                "content": clean_output,
                "metadata": {"fromToolBuffer": True, "tool": "http_request"},
            }
        )

    def _collapse_repeated_sentences(self, text: str) -> str:
        """Collapse immediate duplicate sentences within a single chunk without reformatting whitespace.

        We keep the original spacing and newlines by extracting sentence-like segments
        including their trailing separator/whitespace and only dropping adjacent duplicates
        (compared with a normalized form).
        """
        try:
            s = str(
                text
            )  # DO NOT strip; leading/trailing spaces are meaningful for streaming joins
            if not s:
                return s
            # Grab segments ending with . ! ? : (plus following whitespace) or the tail
            parts = re.findall(r".*?(?:[\.!\?:](?=\s)|$)\s*", s, flags=re.S)
            out = []
            prev_norm = None
            for p in parts:
                if not p:
                    continue
                # Normalized form for comparison only
                n = re.sub(r"\s+", " ", p).strip().lower()
                if prev_norm is not None and n == prev_norm:
                    # skip immediate duplicate segment
                    continue
                out.append(p)  # preserve original spacing/newlines
                prev_norm = n
            return "".join(out)
        except Exception:
            return text

    def _accumulate_reasoning_text(self, text: str) -> None:
        """Accumulate reasoning text to prevent fragmentation."""
        if not text:
            return

        if text.strip().lower() == "reasoning":
            return

        # Merge with previous fragment to avoid duplicate prefixes (e.g., "Great" then "Great! I can...")
        try:
            if self.reasoning_buffer:
                last_chunk = self.reasoning_buffer[-1]
                last_norm = str(last_chunk).strip()
                cur_norm = str(text).strip()
                if last_norm and cur_norm:
                    if ' ' in cur_norm and cur_norm.startswith(last_norm) and len(cur_norm) > len(last_norm):
                        # Replace last short fragment with the longer current one
                        self.reasoning_buffer[-1] = text
                    else:
                        self.reasoning_buffer.append(text)
                else:
                    self.reasoning_buffer.append(text)
            else:
                self.reasoning_buffer.append(text)
        except Exception:
            # Fallback to simple append on any error
            self.reasoning_buffer.append(text)

        now = time.time()
        self.last_reasoning_time = now

    def _begin_reasoning_action_if_needed(self) -> None:
        """Pre-emit action header for reasoning-only cycles once per cycle.

        Special case: Do NOT pre-emit for the initial reasoning (before any action starts).
        The initial reasoning should appear above the first progress boundary.
        """
        try:
            if self._reasoning_action_header_emitted:
                return
            # If no actions yet, do not emit a header here; the first tool will establish action 1
            if self.action_count == 0:
                return
            if self.should_stop():
                raise BudgetLimitReached("Budget limit reached")
            self.action_count += 1
            self._record_action_boundary()
            self._reasoning_action_header_emitted = True
        except Exception:
            # Never break streaming on header pre-emit issues
            pass

    def _emit_accumulated_reasoning(self, force: bool = False) -> None:
        """Emit accumulated reasoning text as a complete block.

        Args:
            force: If True, bypass per-action gating (used at action transitions and completion)
        """
        if not self.reasoning_buffer:
            return

        combined_reasoning = collapse_first_repeated_sequence("".join(self.reasoning_buffer)).strip()
        if not combined_reasoning:
            # Nothing meaningful; clear and return
            self.reasoning_buffer = []
            return

        # Per-action gating: at most one reasoning emission between action headers
        if (
            (not force)
            and self._any_action_header_emitted
            and self._reasoning_emitted_since_last_action_header
        ):
            # Keep buffer for next action header flush
            return

        reasoning_event = {"type": "reasoning", "content": combined_reasoning}

        self.emit_ui_event(reasoning_event)
        # Mark that we have emitted reasoning at least once in this operation
        self._emitted_any_reasoning = True
        self._reasoning_emitted_since_last_action_header = True
        # This action now has its reasoning
        self._reasoning_required_for_current_action = False
        # Update last flush time for streaming control
        self._last_reasoning_flush = time.time()

        # Clear after successful emission
        self.reasoning_buffer = []

        # Emit tool_preparation spinner after reasoning
        # This indicates the agent is selecting tools based on the reasoning
        self.emit_ui_event(
            {"type": "thinking", "context": "tool_preparation", "urgent": True}
        )

    def _record_action_boundary(self) -> None:
        """Emit progress boundary."""
        # Reset per-action reasoning gate for the new action and flush buffered reasoning before header
        flushed_here = False
        try:
            if self.reasoning_buffer:
                # Flush accumulated reasoning for the upcoming action (appears above header)
                self._emit_accumulated_reasoning(force=True)
                flushed_here = True
            if self._any_action_header_emitted:
                # Starting a new step interval: allow one reasoning emission again
                # BUT if we just flushed here, keep the emission gate set (True) to avoid a second
                # reasoning block.
                if not flushed_here:
                    self._reasoning_emitted_since_last_action_header = False
        except Exception:
            pass

        try:
            progress_percent = self.get_budget_progress()
            self.emit_ui_event(
                {
                    "type": "progress_update",
                    "step": self.action_count,
                    "progressPercent": progress_percent,
                    "operation": self.operation_id,
                    "duration": self._format_duration(self._operation_elapsed_seconds()),
                    "totalTools": len(self.tools_used),
                }
            )

            # This new action requires a reasoning emission (unless a pre-header flush already sufficed)
            if not flushed_here:
                self._reasoning_required_for_current_action = True
            else:
                # If we flushed reasoning just before the header, consider this action satisfied
                self._reasoning_required_for_current_action = False

            self._any_action_header_emitted = True

            # Emit tool_preparation spinner after progress update
            # Provides visual feedback while agent selects tools for this action
            self.emit_ui_event(
                {"type": "thinking", "context": "tool_preparation", "urgent": True}
            )
        except Exception:
            pass

    def _emit_initial_metrics(self) -> None:
        """Emit initial metrics on startup."""
        self.emit_ui_event(
            {
                "type": "metrics_update",
                "metrics": {
                    "tokens": 0,
                    "cost": 0.0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                    "cacheReadTokens": self.sdk_cache_read_tokens,
                    "cacheWriteTokens": self.sdk_cache_write_tokens,
                    "duration": "0s",
                    "memoryOps": 0,
                    "evidence": 0,
                    "reportEstimate": self._report_budget_estimate_payload(),
                    "budget": {
                        "maxDurationMinutes": self._budget_max_duration(),
                        "maxTokens": self._budget_max_tokens(),
                        "maxCost": self._budget_max_cost(),
                    },
                    "progress": 0.0,
                    "progressPercent": 0,
                },
            }
        )

    def get_budget_progress(self) -> int:
        """Return budget utilization percent as the max usage across configured caps."""
        try:
            totals = self._budgeted_usage_totals()
            total_tokens = int(totals["input_tokens"]) + int(totals["output_tokens"])
            cost = float(totals.get("cost", self._compute_total_cost_from_usage()))
            return self._calculate_budget_progress(total_tokens=total_tokens, cost=cost)[1]
        except Exception:
            return 0

    def _calculate_budget_progress(self, total_tokens: int, cost: float) -> tuple[float, int]:
        elapsed_s = self._operation_elapsed_seconds()
        max_duration = self._budget_max_duration()
        max_tokens = self._budget_max_tokens()
        max_cost = self._budget_max_cost()
        utilizations = []
        if isinstance(max_duration, int) and max_duration > 0:
            utilizations.append(elapsed_s / (float(max_duration) * 60.0))
        if isinstance(max_tokens, int) and max_tokens > 0:
            utilizations.append(float(total_tokens) / float(max_tokens))
        if isinstance(max_cost, (int, float)) and max_cost > 0:
            utilizations.append(float(cost) / float(max_cost))
        progress = max(utilizations) if utilizations else 0.0
        progress = max(0.0, progress)
        return progress, int(progress * 100)

    def _operation_elapsed_seconds(self) -> float:
        start_times = []
        coordinator = self.coordinator
        if coordinator is not None:
            try:
                start_times.append(float(coordinator.start_time))
            except Exception:
                pass
        try:
            start_times.append(float(self.start_time))
        except Exception:
            pass
        if not start_times:
            return 0.0
        try:
            return max(0.0, time.time() - min(start_times))
        except Exception:
            return 0.0

    def _budget_max_duration(self) -> int:
        coordinator = self.coordinator
        if coordinator is not None and isinstance(coordinator.budget_max_duration, int):
            if coordinator.budget_max_duration > 0:
                return coordinator.budget_max_duration
        return self.budget_max_duration

    def _budget_max_tokens(self) -> Optional[int]:
        coordinator = self.coordinator
        if coordinator is not None and isinstance(coordinator.budget_max_tokens, int):
            if coordinator.budget_max_tokens > 0:
                return coordinator.budget_max_tokens
        return self.budget_max_tokens

    def _budget_max_cost(self) -> Optional[float]:
        coordinator = self.coordinator
        if coordinator is not None and isinstance(coordinator.budget_max_cost, (int, float)):
            if coordinator.budget_max_cost > 0:
                return float(coordinator.budget_max_cost)
        return self.budget_max_cost

    def _start_metrics_thread(self) -> None:
        """Start a background thread for periodic metrics updates."""

        def update_metrics_loop():
            """Background loop to emit metrics every 5 seconds."""
            logger.debug("Metrics update thread started")
            update_count = 0
            while True:
                if self._stop_metrics_event.wait(5):
                    break
                try:
                    # Only emit if we're not stopped
                    if not self._stop_metrics and not self.should_stop():
                        update_count += 1
                        # Force emission every 6 updates (30 seconds) for duration updates
                        force_update = update_count % 6 == 0
                        self._emit_estimated_metrics(force=force_update)

                except Exception as e:
                    logger.error(f"Error in metrics update thread: {e}", exc_info=True)

        # Start the background thread
        self._metrics_thread = threading.Thread(target=update_metrics_loop, daemon=True)
        self._metrics_thread.start()
        logger.debug("Started periodic metrics update thread")

    def _stop_metrics_thread(self) -> None:
        """Stop the metrics update thread."""
        self._stop_metrics = True
        self._stop_metrics_event.set()
        if self._metrics_thread and self._metrics_thread.is_alive():
            self._metrics_thread.join(timeout=1)

    def _extract_code_from_input(self, tool_input: Any) -> str:
        """Best-effort extraction of code from tool input for python_repl.

        Looks for common keys and returns a string.
        """
        try:
            if not tool_input:
                return ""
            if isinstance(tool_input, str):
                return tool_input
            if isinstance(tool_input, dict):
                val = (
                    tool_input.get("code")
                    or tool_input.get("source")
                    or tool_input.get("input")
                )
                if isinstance(val, str):
                    return val
                if val is not None:
                    try:
                        return json.dumps(val, indent=2)
                    except Exception:
                        return str(val)
                # As a fallback, return JSON of tool_input if it looks like code
                pretty = json.dumps(tool_input, indent=2)
                return pretty
            # Fallback stringification
            return str(tool_input)
        except Exception:
            return ""

    def _compute_cost_from_metrics(
            self,
            input_tokens: int,
            output_tokens: int,
            cache_read_tokens: int,
            cache_write_tokens: int,
            agent: Any = None,
    ) -> float:
        cost = self.pricing_input * (input_tokens / 1_000_000) \
               + self.pricing_output * (output_tokens / 1_000_000) \
               + self.pricing_cache_read * (cache_read_tokens / 1_000_000) \
               + self.pricing_cache_write * (cache_write_tokens / 1_000_000)

        if self._pricing_override_configured:
            return cost

        if self.models_client is None:
            return cost

        pricing_agent = agent or self._last_agent
        if pricing_agent:
            provider = get_provider_from_agent(pricing_agent)
            model_id = get_model_id_from_agent(pricing_agent)
        else:
            provider = self.provider_id
            model_id = self.model_id

        if provider not in [None, "ollama"] and model_id:
            try:
                pricing = None
                try:
                    if provider != "litellm":
                        pricing = self.models_client.get_pricing(provider + "/" + model_id)
                except Exception:
                    pass
                if pricing is None:
                    pricing = self.models_client.get_pricing(model_id)
                if pricing is None:
                    raise Exception(f"No pricing for model {model_id}")
                return (pricing.input or 0.0) * (input_tokens / 1_000_000) \
                    + (pricing.output or 0.0) * (output_tokens / 1_000_000) \
                    + (pricing.cache_read or 0.0) * (cache_read_tokens / 1_000_000) \
                    + (pricing.cache_write or 0.0) * (cache_write_tokens / 1_000_000)
            except Exception as e:
                # only report this once
                if hasattr(self, "_pricing_failures"):
                    pricing_failures = getattr(self, "_pricing_failures")
                else:
                    pricing_failures = set()
                    setattr(self, "_pricing_failures", pricing_failures)
                if model_id not in pricing_failures:
                    pricing_failures.add(model_id)
                    logger.debug("Error getting pricing: {}".format(e), exc_info=True)

        return cost

    def _compute_total_cost_from_usage(self) -> float:
        with self._metrics_lock:
            totals = self._current_usage_totals()
            has_agent_usage = bool(self._agent_usage_cache) or any(
                [
                    self._aggregate_input_tokens,
                    self._aggregate_output_tokens,
                    self._aggregate_cache_read_tokens,
                    self._aggregate_cache_write_tokens,
                    self._aggregate_cost,
                ]
            )
            if has_agent_usage:
                legacy_cost = self._compute_cost_from_metrics(
                    int(self._sdk_input_tokens),
                    int(self._sdk_output_tokens),
                    int(self._sdk_cache_read_tokens),
                    int(self._sdk_cache_write_tokens),
                )
                return float(totals["cost"]) + legacy_cost
            return self._compute_cost_from_metrics(
                int(totals["input_tokens"]),
                int(totals["output_tokens"]),
                int(totals["cache_read_tokens"]),
                int(totals["cache_write_tokens"]),
            )

    def _emit_estimated_metrics(self, force=False) -> None:
        """Emit metrics based on SDK token counts.

        Args:
            force: If True, emit even if metrics have not changed (for periodic duration updates)
        """
        with self._state_lock:
            # Try to get fresh metrics from stored agent reference if available
            if self._last_agent:
                try:
                    if hasattr(self._last_agent, "event_loop_metrics"):
                        usage = self._last_agent.event_loop_metrics.accumulated_usage
                        if usage:
                            self._capture_agent_usage(self._last_agent, usage)
                except Exception as e:
                    logger.debug(f"Could not get metrics from agent: {e}")

            self._publish_usage_to_coordinator()
            totals = self._operation_usage_totals()
            input_tokens = int(totals["input_tokens"])
            output_tokens = int(totals["output_tokens"])
            cache_read_tokens = int(totals["cache_read_tokens"])
            cache_write_tokens = int(totals["cache_write_tokens"])
            total_tokens = input_tokens + output_tokens
            cost = float(totals.get("cost", self._compute_total_cost_from_usage()))
            budgeted_totals = self._budgeted_usage_totals()
            budgeted_tokens = int(budgeted_totals["input_tokens"]) + int(budgeted_totals["output_tokens"])
            budgeted_cost = float(budgeted_totals["cost"])
            report_estimate = self._report_budget_estimate_payload()

            progress, progress_percent = self._calculate_budget_progress(
                total_tokens=budgeted_tokens,
                cost=budgeted_cost,
            )

            current_metrics = {
                "tokens": total_tokens,  # For Footer compatibility
                "cost": cost,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "cacheReadTokens": cache_read_tokens,
                "cacheWriteTokens": cache_write_tokens,
                "duration": self._format_duration(self._operation_elapsed_seconds()),
                "memoryOps": self.coordinator.memory_ops,
                "evidence": self.coordinator.evidence_count,
                "reportEstimate": report_estimate,
                "budget": {
                    "maxDurationMinutes": self._budget_max_duration(),
                    "maxTokens": self._budget_max_tokens(),
                    "maxCost": self._budget_max_cost(),
                },
                "progress": progress,
                "progressPercent": progress_percent,
            }

            meaningful_fields = {
                "tokens": total_tokens,
                "memoryOps": self.coordinator.memory_ops,
                "evidence": self.coordinator.evidence_count,
                "reportEstimate": report_estimate,
            }

            should_emit = (
                force
                or not hasattr(self, "_last_meaningful_metrics")
                or self._last_meaningful_metrics != meaningful_fields
            )
            if should_emit:
                self._last_meaningful_metrics = meaningful_fields.copy()

        if should_emit:
            logger.debug(
                "Emitting metrics: input=%s, output=%s, total=%s",
                current_metrics["inputTokens"],
                current_metrics["outputTokens"],
                current_metrics["totalTokens"],
            )
            self.emit_ui_event({"type": "metrics_update", "metrics": current_metrics})

    def process_metrics(self, event_loop_metrics: Dict[str, Any], agent: Any = None) -> None:
        """Process SDK metrics - only updates internal counters."""

        usage = event_loop_metrics.accumulated_usage

        cache_read = usage.get("cacheReadInputTokens", 0)
        cache_write = usage.get("cacheWriteInputTokens", 0)
        # Log cache activity for validation (only when caching is active)
        if cache_read > 0 or cache_write > 0:
            logger.info(
                "Prompt cache metrics - Read: %d tokens, Write: %d tokens",
                cache_read,
                cache_write,
            )

        if agent:
            self._capture_agent_usage(agent, usage)
        elif self._report_generation_active:
            self._capture_report_usage(usage)
        else:
            with self._metrics_lock:
                self._sdk_input_tokens = usage.get("inputTokens", 0)
                self._sdk_output_tokens = usage.get("outputTokens", 0)
                self._sdk_cache_read_tokens = usage.get("cacheReadInputTokens", 0)
                self._sdk_cache_write_tokens = usage.get("cacheWriteInputTokens", 0)

        self._publish_usage_to_coordinator()

        # Metrics emission is handled by the background thread
        # This method only updates the internal counters

    def _handle_completion(self) -> None:
        """Handle completion events."""
        self._emit_accumulated_reasoning(force=True)

        # End any active thinking indicator
        self.emit_ui_event({"type": "thinking_end"})

        # Emit explicit completion summary for UI/logs
        try:
            totals = self._operation_usage_totals()
            input_tokens = int(totals["input_tokens"])
            output_tokens = int(totals["output_tokens"])
            total_tokens = input_tokens + output_tokens
            cost = float(totals.get("cost", self._compute_total_cost_from_usage()))
            self.emit_ui_event(
                {
                    "type": "operation_complete",
                    "operation": self.operation_id,
                    "duration": self._format_duration(self._operation_elapsed_seconds()),
                    "metrics": {
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "totalTokens": total_tokens,
                        "cost": cost,
                        # Cache metrics for prompt caching cost calculation
                        "cacheReadTokens": int(totals["cache_read_tokens"]),
                        "cacheWriteTokens": int(totals["cache_write_tokens"]),
                        "memoryOps": self.coordinator.memory_ops,
                        "evidence": self.coordinator.evidence_count,
                    },
                }
            )
        except Exception:
            pass

        # Stop metrics thread on completion
        self._stop_metrics_thread()

    def _is_valid_input(self, tool_input: Any) -> bool:
        """Check if tool input is valid."""
        # Allow empty dicts as valid - tools may have no required parameters
        return isinstance(tool_input, (dict, str))

    def _parse_tool_input_from_stream(self, tool_input: Any) -> Dict[str, Any]:
        """Parse tool input from SDK streaming format into usable dictionary.

        The Strands SDK sends tool inputs through multiple streaming updates:
        1. Initial: Empty dict {}
        2. Streaming: Wrapped partial JSON {"value": "{\"task\": \"..."}
        3. Complete: Full JSON that can be unwrapped and parsed

        This function handles all these cases elegantly:
        - Unwraps nested JSON strings from streaming updates
        - Preserves partial JSON for buffering
        - Returns clean dict for tool consumption

        Args:
            tool_input: Raw input from SDK (dict, str, or other)

        Returns:
            Dict with parsed tool parameters or wrapped value
        """
        # Handle None or empty input
        if not tool_input:
            return {}

        # Handle dictionary input (most common case)
        if isinstance(tool_input, dict):
            # Check for SDK streaming pattern: {"value": "json_string"}
            if len(tool_input) == 1 and "value" in tool_input:
                value = tool_input["value"]

                # If value is a JSON string, try to parse it
                if isinstance(value, str):
                    stripped = value.strip()

                    # Check if it looks like JSON
                    if stripped and (stripped[0] in "{[" and stripped[-1] in "}]"):
                        try:
                            # Attempt to parse complete JSON
                            parsed = json.loads(stripped)
                            # Return parsed dict directly, or wrap non-dict values
                            return (
                                parsed
                                if isinstance(parsed, dict)
                                else {"value": parsed}
                            )
                        except json.JSONDecodeError:
                            # Partial JSON - keep wrapped for buffering
                            return tool_input

                    # Non-JSON string value
                    return tool_input

            # Regular dict - return as-is
            return tool_input

        # Handle string input (less common)
        if isinstance(tool_input, str):
            stripped = tool_input.strip()
            if not stripped:
                return {}

            # Try to parse as JSON
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                # Plain string - wrap in value key
                return {"value": stripped}

        # Fallback for unexpected types
        return {"value": str(tool_input)} if tool_input else {}

    def _extract_output_text(self, content_items: List[Any]) -> str:
        """Extract text from content items, handling all possible formats."""
        output_text = ""
        for item in content_items:
            if isinstance(item, dict):
                # Extract text from various possible keys
                if "text" in item:
                    output_text += item["text"]
                elif "json" in item:
                    output_text += json.dumps(item["json"], indent=2)
                elif "content" in item:
                    output_text += str(item["content"])
                elif "output" in item:
                    output_text += str(item["output"])
                elif "result" in item:
                    output_text += str(item["result"])
                elif "message" in item:
                    output_text += str(item["message"])
                elif "data" in item:
                    output_text += str(item["data"])
                else:
                    # If dict has no recognized keys, convert entire dict to string
                    output_text += json.dumps(item, indent=2)
            elif isinstance(item, str):
                output_text += item
            else:
                output_text += str(item)
        return output_text

    def _format_duration(self, seconds: float) -> str:
        """Format duration for human-readable display."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m {int(seconds % 60)}s"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            mins = int((seconds % 3600) / 60)
            return f"{hours}h {mins}m"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days}d {hours}h"

    # Report generation methods
    def ensure_report_generated(
        self, agent, target: str, objective: str, module: str = None
    ) -> None:
        """Ensure report is generated only once."""
        if not self._report_generated:
            self.generate_final_report(agent, target, objective, module)

    def generate_final_report(
        self, agent, target: str, objective: str, module: str = None
    ) -> None:
        """Generate final security assessment report.

        Report generation is allowed to query persisted memory so report-only
        runs can rebuild reports from previous operation evidence.
        """
        if self._report_generated:
            return

        try:
            self._report_generated = True

            # Import report generator function (not a tool, called directly by handler)
            from modules.handlers.report_generator import generate_security_report

            # Emit completion section before generating report
            # FIXME: Add an operation_stage parameter for progress_update: assessment, finding_report, final_report, ...
            self.emit_ui_event(
                {
                    "type": "progress_update",
                    "step": "FINAL REPORT",
                    "progressPercent": self.get_budget_progress(),
                    "operation": self.operation_id,
                    "duration": self._format_duration(self._operation_elapsed_seconds()),
                }
            )

            # Determine provider from handler configuration first. In report-only mode there is no main agent.
            provider = self.provider_id or "bedrock"
            if agent is not None and hasattr(agent, "model"):
                model_class = agent.model.__class__.__name__
                if "Bedrock" in model_class:
                    provider = "bedrock"
                elif "Ollama" in model_class:
                    provider = "ollama"
                elif "LiteLLM" in model_class:
                    provider = "litellm"

            self.emit_ui_event(
                {
                    "type": "output",
                    "content": "\n◆ Generating comprehensive security assessment report...",
                }
            )

            # Prepare config data for report generation
            # Build tools_used list reflecting true usage counts for accurate reporting
            try:
                if self.tool_counts:
                    tools_used_list = []
                    # Deterministic order for reproducibility
                    for name in sorted(self.tool_counts.keys()):
                        count = int(self.tool_counts.get(name, 0) or 0)
                        if count > 0:
                            tools_used_list.extend([name] * count)
                else:
                    tools_used_list = list(self.tools_used)
            except Exception:
                tools_used_list = list(self.tools_used)

            # Get the main model ID from config for report generation
            model_id = None
            try:
                from modules.config.manager import get_config_manager

                cfg = get_config_manager()
                llm_cfg = cfg.get_llm_config(provider)
                model_id = llm_cfg.model_id
            except Exception:
                pass

            config_params = {
                "actions_executed": self.action_count,
                "tools_used": tools_used_list,
                "provider": provider,
                "module": module,
                "model_id": model_id,  # Pass main model for reports
            }

            target_name = sanitize_target_name(target)
            output_dir = get_output_path(
                target_name, self.operation_id, ""
            )

            # Create output directory if it doesn't exist
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            # Save report as markdown file
            report_path = os.path.join(
                output_dir, "security_assessment_report.md"
            )
            self._evaluation_report_path = report_path

            self._report_generation_active = True
            try:
                generate_security_report(
                    target=target,
                    objective=objective,
                    operation_id=self.operation_id,
                    config_params=config_params,
                    callback_handler=self,
                    filename=report_path,
                )
            finally:
                self._report_generation_active = False

            # Read report from file
            report_content = ""
            if os.path.exists(report_path):
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        report_content = f.read(15000)  # 15KB threshold for IPC safety, 200 lines for event output
                    report_content = "\n".join(report_content.split("\n")[:200])  # 200 lines for event output
                except Exception as read_error:
                    logger.warning(f"Could not read generated report: {read_error}")

            # Accept any non-empty report content
            if report_content:
                self._completed_report_path = report_path
                self.emit_ui_event({"type": "report_content", "content": report_content})

                # Also emit file path information for reference
                self.emit_ui_event(
                    {
                        "type": "output",
                        "content": (
                            f"\n{'━' * 80}\n\nREPORT GENERATED\n\nREPORT ALSO SAVED TO:\n"
                            f"  • {report_path}\n\nMEMORY STORED IN:\n  • {output_dir}/memory/\n\n"
                            f"OPERATION LOGS:\n  • {os.path.join(output_dir, 'cyber_operations.log')}\n\n"
                            f"{'━' * 80}\n"
                        ),
                    }
                )

                if hasattr(self.emitter, "flush_immediate"):
                    self.emitter.flush_immediate()

                logger.info("Report saved to %s", report_path)
            else:
                logger.info(
                    "Report generation skipped - no evidence collected during operation"
                )
                try:
                    self.emit_ui_event(
                        {
                            "type": "output",
                            "content": "◆ No memories or evidence were collected during this operation. Skipping report generation.",
                        }
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error("Error generating final report: %s", e)
            self.emit_ui_event(
                {"type": "error", "content": f"Error generating report: {str(e)}"}
            )

    def emit_assessment_complete(self) -> None:
        """Emit the terminal assessment event once report and evaluation work has ended."""
        if self._assessment_completion_emitted:
            return

        self._assessment_completion_emitted = True
        self.emit_ui_event(
            {
                "type": "assessment_complete",
                "operation_id": self.operation_id,
                "report_path": self._completed_report_path,
            }
        )
        if hasattr(self.emitter, "flush_immediate"):
            self.emitter.flush_immediate()

    # Evaluation methods
    def trigger_evaluation_on_completion(self) -> None:
        """Trigger evaluation after operation completion."""
        from modules.evaluation.manager import EvaluationManager, TraceType

        logger.debug(
            "trigger_evaluation_on_completion called for operation %s",
            self.operation_id,
        )

        # Check if observability is enabled first - evaluation requires Langfuse infrastructure
        if os.getenv("ENABLE_OBSERVABILITY", "false").lower() != "true":
            logger.debug(
                "Observability is disabled - skipping evaluation (requires Langfuse)"
            )
            return

        # Default evaluation to same setting as observability
        default_evaluation = os.getenv("ENABLE_OBSERVABILITY", "false")
        if os.getenv("ENABLE_AUTO_EVALUATION", default_evaluation).lower() != "true":
            logger.debug("Auto-evaluation disabled via ENABLE_AUTO_EVALUATION!=true, skipping")
            return

        try:
            logger.debug(
                "Starting evaluation process for operation %s",
                self.operation_id,
            )

            eval_manager = EvaluationManager(
                operation_id=self.operation_id,
                emitter=self.emitter,
                report_path=getattr(self, "_evaluation_report_path", None),
            )

            eval_manager.register_trace(
                trace_id=self.operation_id,
                trace_type=TraceType.MAIN_AGENT,
                name=f"Security Assessment - {self.operation_id}",
                session_id=self.operation_id,
            )

            logger.debug("Registered trace for evaluation")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            logger.info("Starting evaluation for operation %s", self.operation_id)

            results = loop.run_until_complete(eval_manager.evaluate_all_traces())

            if results:
                logger.info(
                    "Evaluation completed successfully: %d traces evaluated",
                    len(results),
                )
                self.emit_ui_event(
                    {
                        "type": "evaluation_complete",
                        "operation_id": self.operation_id,
                        "traces_evaluated": len(results),
                    }
                )
            else:
                logger.warning("No evaluation results - check trace finding and metric evaluation")

        except Exception as e:
            logger.warning("Evaluation failed but continuing operation: %s", str(e), exc_info=True)

    # Property methods for compatibility
    @property
    def state(self):
        """Mock state object for compatibility."""

        class MockState:
            report_generated = self._report_generated
            budget_limit_reached = bool(self._budget_limit_reached)

        return MockState()

    def should_stop(self) -> bool:
        """Check if execution should stop (budget-aware).

        Also emits a termination_reason event once when a stop condition is detected.
        """
        with self._state_lock:
            if self._report_generation_active:
                return False

            if self._budget_limit_reached:
                return True

            # Budget checks
            try:
                # Duration cap
                max_duration = self._budget_max_duration()
                if isinstance(max_duration, int) and max_duration > 0:
                    if self._operation_elapsed_seconds() >= float(max_duration) * 60.0:
                        if not self._termination_emitted:
                            self.emit_termination(
                                "budget_limit",
                                f"Duration limit reached: {max_duration}m",
                            )
                        self._budget_limit_reached = True
                        self._budget_limit_reason = "duration"
                        return True

                # Token cap
                max_tokens = self._budget_max_tokens()
                if isinstance(max_tokens, int) and max_tokens > 0:
                    totals = self._budgeted_usage_totals()
                    total_tokens = int(totals["input_tokens"]) + int(totals["output_tokens"])
                    if total_tokens >= int(max_tokens):
                        if not self._termination_emitted:
                            self.emit_termination(
                                "budget_limit",
                                f"Token limit reached: {total_tokens}/{max_tokens}",
                            )
                        self._budget_limit_reached = True
                        self._budget_limit_reason = "tokens"
                        return True

                # Cost cap
                max_cost = self._budget_max_cost()
                if isinstance(max_cost, (int, float)) and max_cost > 0:
                    totals = self._budgeted_usage_totals()
                    cost = float(totals.get("cost", self._compute_total_cost_from_usage()))
                    if cost >= float(max_cost):
                        if not self._termination_emitted:
                            self.emit_termination(
                                "budget_limit",
                                f"Cost limit reached: {cost:.4f}/{max_cost}",
                            )
                        self._budget_limit_reached = True
                        self._budget_limit_reason = "cost"
                        return True
            except Exception:
                # Never fail stop checks due to metric calculation errors
                pass

        return False

    def has_reached_limit(self) -> bool:
        """Check if any budget limit reached."""
        return bool(self._budget_limit_reached)

    @property
    def termination_emitted(self) -> bool:
        return self._termination_emitted

    @property
    def termination_reason(self) -> Optional[str]:
        return self._termination_reason

    @property
    def report_generated(self) -> bool:
        """Check if report was generated."""
        return self._report_generated

    def get_summary(self) -> Dict[str, Any]:
        """Get operation summary for reporting."""
        with self._state_lock:
            totals = self._operation_usage_totals()
            input_tokens = int(totals["input_tokens"])
            output_tokens = int(totals["output_tokens"])
            total_tokens = input_tokens + output_tokens
            current_metrics = {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "cost": float(totals.get("cost", self._compute_total_cost_from_usage())),
                "cacheReadTokens": int(totals["cache_read_tokens"]),
                "cacheWriteTokens": int(totals["cache_write_tokens"]),
            }
            memory_ops = self.coordinator.memory_ops
            evidence_count = self.coordinator.evidence_count
            tool_counts = self.coordinator.tool_counts

            return {
                "total_actions": self.action_count,
                "tools_created": len(tool_counts),
                "evidence_collected": evidence_count,
                "memory_operations": memory_ops,
                "capability_expansion": list(tool_counts.keys()),
                "memory_ops": memory_ops,
                "evidence_count": evidence_count,
                "duration": self._format_duration(self._operation_elapsed_seconds()),
                "metrics": current_metrics,
            }
