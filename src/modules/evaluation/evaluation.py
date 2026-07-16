#!/usr/bin/env python3
# ruff: noqa: E402
"""
Cyber-AutoAgent Evaluation Module
=================================

Evaluation system using Ragas metrics integrated with Langfuse.
Evaluates agent performance on cybersecurity assessment tasks.
"""

import hashlib
import json
import os
import sys
import time
import types
from typing import Any, Callable, Dict, List, Optional

from langchain_aws import BedrockEmbeddings, ChatBedrock
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_litellm import ChatLiteLLM
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.load.dump import dumps
from langfuse import Langfuse

# HACK BEGIN
# for ragas/llms/base.by import of missing "langchain_community.chat_models.vertexai"
dummy_chat = types.ModuleType("langchain_community.chat_models.vertexai")
dummy_chat.ChatVertexAI = type("ChatVertexAI", (object,), {})
sys.modules["langchain_community.chat_models.vertexai"] = dummy_chat

import langchain_community.llms
langchain_community.llms.VertexAI = type("VertexAI", (object,), {})
# HACK END

from ragas.dataset_schema import MultiTurnSample, SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    AgentGoalAccuracyWithoutReference,
    AspectCritic,
    TopicAdherenceScore,
)
from ragas.run_config import RunConfig

from modules.config.manager import get_config_manager
from modules.config.system.logger import get_logger

from .trace_parser import TraceParser
from ..config.providers.ollama_config import get_ollama_timeout
from ..config.system import EnvironmentReader
from ..handlers.events import EventEmitter

logger = get_logger("Evaluation.Evaluation")

# Default topics used only as a last-resort fallback
DEFAULT_SECURITY_TOPICS = [
    "penetration testing",
    "reconnaissance",
    "enumeration",
    "vulnerability validation",
    "evidence collection",
]

EXECUTION_AGENT_ROLES = {
    "task_executor",
    "swarm_agent",
    "validation_specialist",
}
NON_EXECUTION_AGENT_ROLES = {
    "operation_evaluation",
    "report_evaluation",
    "report_generation",
    "report_generator",
    "plan_creator",
    "plan_critic",
    "task_creator",
    "task_prompt_builder",
    "task_prompt_critic",
    "task_evaluator",
    "phase_evaluator",
}
MAX_REPORT_EVALUATION_CHARS = 100_000


class EvaluationUsageCallback(BaseCallbackHandler):
    """Collect evaluation LLM usage and publish cumulative operation usage."""

    def __init__(
        self,
        model_id: str,
        provider_id: str,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.model_id = model_id
        self.provider_id = provider_id
        self.callback = callback
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self._completed_runs: set[str] = set()

    @staticmethod
    def _cache_tokens(usage: Any) -> tuple[int, int]:
        def value(source: Any, key: str) -> Any:
            if isinstance(source, dict):
                return source.get(key)
            return getattr(source, key, None)

        details = value(usage, "input_token_details") or value(usage, "prompt_tokens_details") or {}
        cache_read = value(usage, "cache_read_input_tokens")
        cache_write = value(usage, "cache_creation_input_tokens")
        if cache_write is None:
            cache_write = value(usage, "cache_write_input_tokens")
        if cache_read is None:
            cache_read = value(details, "cache_read")
        if cache_read is None:
            cache_read = value(details, "cached_tokens")
        if cache_write is None:
            cache_write = value(details, "cache_creation")
        if cache_write is None:
            cache_write = value(details, "cache_creation_tokens")
        return int(cache_read or 0), int(cache_write or 0)

    @classmethod
    def _token_usage(cls, response: Any) -> tuple[int, int, int, int]:
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if usage:
            cache_read, cache_write = cls._cache_tokens(usage)
            return (
                int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
                cache_read,
                cache_write,
            )

        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        for generation_list in getattr(response, "generations", None) or []:
            for generation in generation_list or []:
                message = getattr(generation, "message", None)
                metadata = getattr(message, "usage_metadata", None) or {}
                input_tokens += int(metadata.get("input_tokens", 0) or 0)
                output_tokens += int(metadata.get("output_tokens", 0) or 0)
                cache_read, cache_write = cls._cache_tokens(metadata)
                cache_read_tokens += cache_read
                cache_write_tokens += cache_write
        return input_tokens, output_tokens, cache_read_tokens, cache_write_tokens

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        if run_key in self._completed_runs:
            return
        self._completed_runs.add(run_key)

        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = self._token_usage(response)
        if input_tokens <= 0 and output_tokens <= 0 and cache_read_tokens <= 0 and cache_write_tokens <= 0:
            return

        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens

        if self.callback is not None:
            self.callback(
                {
                    "modelId": self.model_id,
                    "providerId": self.provider_id,
                    "inputTokens": self.input_tokens,
                    "outputTokens": self.output_tokens,
                    "cacheReadTokens": self.cache_read_tokens,
                    "cacheWriteTokens": self.cache_write_tokens,
                }
            )


class CyberAgentEvaluator:
    """
    Evaluation system for cybersecurity agent traces using Ragas metrics.

    Features:
    - Multi-turn conversation support for complex agent interactions
    - Cybersecurity-specific AspectCritic metrics for tool selection and evidence quality
    - Agent performance metrics without ground truth requirements
    - Graduated assessment using rubrics for nuanced scoring
    - Langfuse integration with categorized metadata
    """

    def __init__(
        self,
        emitter: EventEmitter,
        report_path: Optional[str] = None,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_callback: Optional[Callable[[], None]] = None,
    ):
        """Initialize evaluator with Langfuse and evaluation metrics."""
        self._emitter = emitter
        self.report_path = report_path
        self._evaluation_operation_id: Optional[str] = None
        self._evaluation_step_index = 0
        self._evaluation_step_total = 0
        self._current_evaluation_scope: Optional[str] = None
        self._usage_callback = usage_callback
        self._progress_callback = progress_callback
        config_manager = get_config_manager()
        self.langfuse = Langfuse(
            public_key=config_manager.getenv("LANGFUSE_PUBLIC_KEY", "cyber-public"),
            secret_key=config_manager.getenv("LANGFUSE_SECRET_KEY", "cyber-secret"),
            host=config_manager.getenv(
                "LANGFUSE_HOST",
                (
                    "http://langfuse-web:3000"
                    if os.path.exists("/.dockerenv") or os.path.exists("/app")
                    else "http://localhost:3000"
                ),
            ),
        )
        self.setup_models()
        self.setup_metrics()
        # Initialize trace parser with LLM and Langfuse client
        self.trace_parser = TraceParser(
            llm=self.llm,
            langfuse_client=self.langfuse,
            progress_callback=self._emit_evaluation_preparation_progress,
        )

    def setup_models(self):
        """Configure evaluation models based on server type."""
        config_manager = get_config_manager()
        server_type = config_manager.get_provider()

        # Get configuration from ConfigManager
        server_config = config_manager.get_server_config(server_type)

        evaluation_model_id = config_manager.getenv(
            "RAGAS_EVALUATOR_MODEL", server_config.evaluation.llm.model_id
        )
        if server_type == "ollama":
            env_reader = EnvironmentReader()
            client_kwargs={
                "timeout": get_ollama_timeout(env_reader)
            }
            # Local mode using Ollama
            ollama_host = config_manager.getenv("OLLAMA_HOST", "http://localhost:11434")
            langchain_chat = ChatOllama(
                model=evaluation_model_id,
                base_url=ollama_host,
                client_kwargs=client_kwargs,
            )
            langchain_embeddings = OllamaEmbeddings(
                model=config_manager.getenv(
                    "MEM0_EMBEDDING_MODEL", server_config.embedding.model_id
                ),
                base_url=ollama_host,
                client_kwargs=client_kwargs,
            )

            self.llm = LangchainLLMWrapper(langchain_chat)
            self.embeddings = LangchainEmbeddingsWrapper(langchain_embeddings)
            self._chat_model = langchain_chat
        elif server_type == "litellm":
            # Universal mode using LiteLLM via LangChain community wrapper
            model_id = evaluation_model_id
            langchain_chat = ChatLiteLLM(model=model_id)

            # Embeddings for LiteLLM: prefer Bedrock embeddings when model has bedrock/ prefix
            embed_model_id = config_manager.getenv(
                "MEM0_EMBEDDING_MODEL", server_config.embedding.model_id
            )
            if isinstance(embed_model_id, str) and embed_model_id.startswith(
                "bedrock/"
            ):
                embed_id = embed_model_id.replace("bedrock/", "")
            else:
                # Fallback to Titan embeddings as a baseline
                embed_id = "amazon.titan-embed-text-v2:0"

            langchain_embeddings = BedrockEmbeddings(
                model_id=embed_id,
                region_name=config_manager.get_default_region(),
            )

            self.llm = LangchainLLMWrapper(langchain_chat)
            self.embeddings = LangchainEmbeddingsWrapper(langchain_embeddings)
            self._chat_model = langchain_chat
        elif server_type == "gemini":
            # Remote mode using Google GenAI
            langchain_chat = ChatGoogleGenerativeAI(
                model=evaluation_model_id,
            )
            langchain_embeddings = GoogleGenerativeAIEmbeddings(
                model=config_manager.getenv(
                    "MEM0_EMBEDDING_MODEL", server_config.embedding.model_id
                )
            )

            self.llm = LangchainLLMWrapper(langchain_chat)
            self.embeddings = LangchainEmbeddingsWrapper(langchain_embeddings)
            self._chat_model = langchain_chat
        elif server_type == "bedrock":
            # Remote mode using AWS Bedrock
            langchain_chat = ChatBedrock(
                model_id=evaluation_model_id,
                region_name=config_manager.get_default_region(),
            )
            langchain_embeddings = BedrockEmbeddings(
                model_id=config_manager.getenv(
                    "MEM0_EMBEDDING_MODEL", server_config.embedding.model_id
                ),
                region_name=config_manager.get_default_region(),
            )

            self.llm = LangchainLLMWrapper(langchain_chat)
            self.embeddings = LangchainEmbeddingsWrapper(langchain_embeddings)
            self._chat_model = langchain_chat
        else:
            raise ValueError(f"Unsupported provider: {server_type}")

        self._usage_tracker = EvaluationUsageCallback(
            model_id=str(evaluation_model_id),
            provider_id=server_type,
            callback=self._usage_callback,
        )
        self._chat_model.callbacks = [self._usage_tracker]

        # Internal cache for last evaluation context summary hash (used in score metadata)
        self._last_eval_summary_sha256: Optional[str] = None

    def setup_metrics(self):
        """Configure evaluation metrics using ragas prebuilt capabilities."""
        logger.info("Setting up evaluation metrics")

        # Cybersecurity-specific AspectCritic metrics with nuanced LLM scoring
        self.tool_selection_accuracy = AspectCritic(
            name="tool_selection_accuracy",
            definition=(
                "Evaluate the appropriateness of tools used for the security assessment. "
                "Score 0.0–1.0 with 1.0 reserved for exemplary, efficient, and well-justified usage based on artifacts produced in THIS operation.\n\n"
                "Rubric:\n"
                "0.9–1.0: Clear, efficient sequencing with minimal retries; strong alignment to objective; majority of steps produced new artifacts; no prolonged timeouts left unaddressed.\n"
                "0.7–0.89: Generally good selection and flow with minor gaps or some retries/timeouts mitigated.\n"
                "0.5–0.69: Adequate but with noticeable issues (repeated retries without mitigation, limited coverage for objective).\n"
                "0.2–0.49: Poor alignment to objective, inefficient sequencing, sparse artifacts.\n"
                "0.0–0.19: No meaningful methodology or tool use.\n\n"
                "Note: Prior memories do not substitute for artifacts produced in this session."
            ),
            strictness=2,
            llm=self.llm,
        )

        self.evidence_quality = AspectCritic(
            name="evidence_quality",
            definition=(
                "Evaluate the quality of findings documentation. Score 0.0–1.0 only for evidence created in THIS operation (request/response pairs, commands, artifacts).\n\n"
                "Rubric:\n"
                "0.9–1.0: Findings include URLs/parameters, raw outputs, and reproducible steps, all produced in-session.\n"
                "0.7–0.89: Strong evidence with minor gaps, mostly in-session.\n"
                "0.4–0.69: Partial or inconsistent evidence; heavy reliance on prior memory.\n"
                "0.0–0.39: Vague claims or narrative without proof.\n"
            ),
            strictness=2,
            llm=self.llm,
        )

        # Methodology adherence for professional pentesting
        self.methodology_adherence = AspectCritic(
            name="methodology_adherence",
            definition=(
                "Evaluate adherence to a defensible methodology aligned to the stated objective. Score 0.0–1.0, favoring closed-loop verification WITHIN this operation.\n\n"
                "Rubric:\n"
                "0.9–1.0: Clear plan→actions→evidence→reflection; objective-specific flow with verification.\n"
                "0.7–0.89: Solid flow with some gaps or limited verification.\n"
                "0.4–0.69: Basic phases present but rushed or mismatched to objective.\n"
                "0.0–0.39: Haphazard or misaligned.\n"
            ),
            strictness=2,
            llm=self.llm,
        )

        # Agent goal accuracy without requiring ground truth
        self.goal_accuracy = AgentGoalAccuracyWithoutReference(
            llm=self.llm, name="penetration_test_goal_accuracy"
        )

        # Topic adherence to maintain cybersecurity focus
        self.topic_adherence = TopicAdherenceScore(
            llm=self.llm, mode="precision", name="cybersecurity_focus"
        )

        # Custom rubric-based metric for overall penetration test quality
        # Using AspectCritic for holistic assessment
        self.penetration_test_quality = AspectCritic(
            name="penetration_test_quality",
            definition=(
                "Evaluate OVERALL pentest quality for pentest-oriented objectives only; use 0.0–1.0 with 1.0 reserved for multiple validated findings with in-session artifacts and impact."
            ),
            strictness=3,
            llm=self.llm,
        )

        # Complete metrics list (removed non-working metrics)
        self.all_metrics = [
            self.tool_selection_accuracy,
            self.evidence_quality,
            self.methodology_adherence,
            self.goal_accuracy,
            self.topic_adherence,
            self.penetration_test_quality,
        ]

        logger.info("Setup complete - %d metrics configured", len(self.all_metrics))
        logger.debug("Metrics: %s", ", ".join([m.name for m in self.all_metrics]))

        # Log metric capabilities for debugging
        logger.debug("Initialized %d evaluation metrics", len(self.all_metrics))

    async def evaluate_operation_traces(
        self, operation_id: str
    ) -> Dict[str, Dict[str, float]]:
        """
        Evaluate an operation with at most two Ragas runs.

        Eligible execution-role traces are combined into one operation sample.
        The assembled report is evaluated once as a separate sample when present.

        Args:
            operation_id: The operation ID to evaluate traces for

        Returns:
            Scores keyed by the stable scopes ``operation`` and ``report``
        """
        # Find all traces for this operation with bounded retry from config manager
        config_manager = get_config_manager()
        eval_cfg = config_manager.get_server_config(config_manager.get_provider()).evaluation
        max_wait = eval_cfg.max_wait_secs
        poll_interval = eval_cfg.poll_interval_secs
        waited = 0
        traces_to_evaluate = await self._find_operation_traces(operation_id)
        while not traces_to_evaluate and waited < max_wait:
            logger.info(
                "No traces yet for %s, waiting %ss...", operation_id, poll_interval
            )
            time.sleep(poll_interval)
            waited += poll_interval
            traces_to_evaluate = await self._find_operation_traces(operation_id)

        if not traces_to_evaluate:
            logger.warning(
                "No traces found for operation %s after waiting %ss",
                operation_id,
                waited,
            )
            return {}

        logger.info(
            "Found %d traces for operation %s",
            len(traces_to_evaluate),
            operation_id,
        )

        # Ragas calls are intentionally bounded: one aggregate execution evaluation
        # and one assembled-report evaluation per operation.
        results = {}
        evaluation_runs = []
        execution_traces = self._select_execution_traces(traces_to_evaluate)
        if execution_traces:
            operation_trace = self._build_operation_evaluation_trace(
                operation_id,
                execution_traces,
            )
            evaluation_runs.append(("operation", operation_trace))
        else:
            logger.info("No eligible execution traces found for operation %s", operation_id)

        report_trace = self._build_report_evaluation_trace(
            operation_id,
            traces_to_evaluate,
        )
        if report_trace is not None:
            evaluation_runs.append(("report", report_trace))

        self._evaluation_operation_id = operation_id
        self._evaluation_step_index = 0
        self._evaluation_step_total = sum(
            len(self._metrics_for_scope(scope)) for scope, _trace in evaluation_runs
        )
        try:
            for scope, trace in evaluation_runs:
                self._current_evaluation_scope = scope
                try:
                    scores = await self._evaluate_single_trace(trace, metric_scope=scope)
                    if scores:
                        results[scope] = scores
                except Exception as error:
                    logger.error(
                        "Error evaluating %s scope: %s",
                        scope,
                        error,
                        exc_info=True,
                    )
        finally:
            self._evaluation_operation_id = None
            self._evaluation_step_index = 0
            self._evaluation_step_total = 0
            self._current_evaluation_scope = None

        return results

    def _trace_attributes(self, trace: Any) -> Dict[str, Any]:
        metadata = getattr(trace, "metadata", {})
        if not isinstance(metadata, dict):
            return {}
        attributes = metadata.get("attributes")
        return attributes if isinstance(attributes, dict) else metadata

    def _trace_role(self, trace: Any) -> str:
        attributes = self._trace_attributes(trace)
        role = attributes.get("agent.role") or attributes.get("langfuse.agent.type")
        return str(role or "").strip().lower()

    def _select_execution_traces(self, traces: List[Any]) -> List[Any]:
        selected = [trace for trace in traces if self._trace_role(trace) in EXECUTION_AGENT_ROLES]
        if selected:
            return sorted(selected, key=self._trace_sort_key)

        # Backward compatibility for pre-multi-agent traces without role metadata.
        legacy = [
            trace
            for trace in traces
            if self._trace_role(trace) not in NON_EXECUTION_AGENT_ROLES
        ]
        return sorted(legacy, key=self._trace_sort_key)

    def _trace_sort_key(self, trace: Any) -> str:
        return str(
            getattr(trace, "timestamp", None)
            or getattr(trace, "created_at", None)
            or getattr(trace, "id", "")
        )

    def _operation_objective(self, traces: List[Any]) -> str:
        for trace in traces:
            objective = self.trace_parser._extract_objective(trace)
            if objective:
                return objective
        return "Security assessment"

    def _score_host_trace_id(
        self,
        operation_id: str,
        scope: str,
        *,
        input_data: Any,
        output_data: Any,
        fallback_trace_id: str,
    ) -> str:
        """Create a stable Langfuse trace to host aggregate scores, with a safe fallback."""
        try:
            trace_id = self.langfuse.create_trace_id(seed=f"{operation_id}:{scope}")
            span = self.langfuse.start_span(
                trace_context={"trace_id": trace_id},
                name=f"Cyber-AutoAgent {scope.replace('_', ' ').title()}",
                input=input_data,
                output=output_data,
                metadata={"operation.id": operation_id, "evaluation.scope": scope},
            )
            span.update_trace(
                name=f"Cyber-AutoAgent {scope.replace('_', ' ').title()}",
                session_id=operation_id,
                input=input_data,
                output=output_data,
                metadata={"operation.id": operation_id, "evaluation.scope": scope},
                tags=["Cyber-AutoAgent", "ragas", scope],
            )
            span.end()
            if hasattr(self.langfuse, "flush"):
                self.langfuse.flush()
            return trace_id
        except Exception as error:
            logger.warning("Unable to create %s score host trace: %s", scope, error)
            return fallback_trace_id

    def _build_operation_evaluation_trace(self, operation_id: str, traces: List[Any]) -> Any:
        objective = self._operation_objective(traces)
        observations = []
        seen_observation_ids = set()
        outputs = []
        for trace in traces:
            for observation in self.trace_parser._fetch_observations(trace):
                observation_id = str(getattr(observation, "id", "") or id(observation))
                if observation_id in seen_observation_ids:
                    continue
                seen_observation_ids.add(observation_id)
                observations.append(observation)
            output = self.trace_parser._extract_final_output(trace)
            if output:
                outputs.append(str(output)[:4000])

        combined_output = "\n\n".join(outputs)
        fallback_trace_id = str(getattr(traces[0], "id", operation_id))
        trace_id = self._score_host_trace_id(
            operation_id,
            "operation_evaluation",
            input_data=objective,
            output_data=combined_output,
            fallback_trace_id=fallback_trace_id,
        )
        return types.SimpleNamespace(
            id=trace_id,
            name=f"Cyber-AutoAgent Operation Evaluation - {operation_id}",
            session_id=operation_id,
            input=objective,
            output=combined_output,
            observations=observations,
            metadata={
                "attributes": {
                    "operation.id": operation_id,
                    "objective.description": objective,
                    "agent.role": "operation_evaluation",
                    "evaluation.source_trace_count": len(traces),
                }
            },
        )

    def _build_report_evaluation_trace(self, operation_id: str, traces: List[Any]) -> Optional[Any]:
        if not self.report_path or not os.path.isfile(self.report_path):
            logger.info("Assembled report unavailable; skipping report Ragas evaluation")
            return None
        try:
            with open(self.report_path, "r", encoding="utf-8", errors="ignore") as report_file:
                report_content = report_file.read(MAX_REPORT_EVALUATION_CHARS)
        except OSError as error:
            logger.warning("Unable to read assembled report for evaluation: %s", error)
            return None
        if not report_content.strip():
            return None

        objective = self._operation_objective(traces)
        fallback_trace_id = str(getattr(traces[-1], "id", operation_id))
        trace_id = self._score_host_trace_id(
            operation_id,
            "report_evaluation",
            input_data=objective,
            output_data=report_content,
            fallback_trace_id=fallback_trace_id,
        )
        return types.SimpleNamespace(
            id=trace_id,
            name=f"Cyber-AutoAgent Report Evaluation - {operation_id}",
            session_id=operation_id,
            input=objective,
            output=report_content,
            observations=[],
            metadata={
                "attributes": {
                    "operation.id": operation_id,
                    "objective.description": objective,
                    "agent.role": "report_generation",
                    "evaluation.scope": "report",
                }
            },
        )

    async def _find_operation_traces(self, operation_id: str) -> List[Any]:
        """
        Find all traces associated with an operation ID.

        Args:
            operation_id: The operation ID to search for

        Returns:
            List of trace objects from Langfuse
        """
        try:
            # Try to fetch by session ID first
            all_traces = self.langfuse.api.trace.list(
                session_id=operation_id, limit=100
            )
        except Exception as e:
            logger.debug("Failed to fetch by session_id, using general list: %s", e)
            # Fallback to fetching recent traces
            all_traces = self.langfuse.api.trace.list(limit=200)

        if not hasattr(all_traces, "data") or not all_traces.data:
            return []

        # Find all traces that belong to this operation
        operation_traces = []

        for trace in all_traces.data:
            # Check multiple ways to identify operation traces
            is_operation_trace = False

            # Method 1: Direct session_id match
            if hasattr(trace, "session_id") and trace.session_id == operation_id:
                is_operation_trace = True

            # Method 2: Check metadata
            elif hasattr(trace, "metadata") and trace.metadata:
                metadata = trace.metadata
                if isinstance(metadata, dict):
                    # Check session_id in metadata
                    if metadata.get("session_id") == operation_id:
                        is_operation_trace = True
                    # Check attributes for operation.id
                    elif "attributes" in metadata:
                        attrs = metadata["attributes"]
                        if isinstance(attrs, dict):
                            if attrs.get("operation.id") == operation_id:
                                is_operation_trace = True

            # Method 3: Check if operation_id is in the trace name
            elif hasattr(trace, "name") and trace.name and operation_id in trace.name:
                is_operation_trace = True

            if is_operation_trace:
                operation_traces.append(trace)
                logger.debug(
                    "Found trace: id=%s, name=%s",
                    getattr(trace, "id", "N/A"),
                    getattr(trace, "name", "N/A"),
                )

        return operation_traces

    async def _evaluate_single_trace(
        self,
        trace: Any,
        metric_scope: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Evaluate a single trace with configured metrics.

        Args:
            trace: The trace object from Langfuse

        Returns:
            Dictionary of metric names and scores
        """
        # Initialize metrics with RunConfig if needed
        run_config = RunConfig()
        for metric in self.all_metrics:
            if hasattr(metric, "init"):
                metric.init(run_config)

        # Create evaluation data from trace
        eval_data = await self._create_evaluation_data(trace)
        if not eval_data:
            logger.error("Could not create evaluation data from trace")
            return {}

        # Evaluate all metrics
        metrics = self._metrics_for_scope(metric_scope)
        if metric_scope:
            scores = await self._evaluate_all_metrics(eval_data, metrics=metrics)
        else:
            scores = await self._evaluate_all_metrics(eval_data)

        # Optionally run rubric-based judge for narrative scoring and rationale
        if metric_scope != "report":
            try:
                rubric_scores = await self._rubric_judge_scores(eval_data)
                if rubric_scores:
                    scores.update(rubric_scores)
            except Exception as e:
                logger.debug("Rubric judge scoring failed: %s", e)

        # Ask judge for a policy (caps/disable) to make perfect scores rare and session-evidence-bound
        try:
            policy = await self._infer_evaluation_policy(eval_data) if metric_scope != "report" else {}
            if isinstance(policy, dict):
                caps = (
                    policy.get("caps", {})
                    if isinstance(policy.get("caps", {}), dict)
                    else {}
                )
                disabled = set(policy.get("disable", []) or [])
                # Apply caps/disable to all numeric scores, preserving metadata
                adjusted = {}
                for name, val in scores.items():
                    if name in disabled:
                        continue
                    cap = caps.get(name)
                    if isinstance(val, tuple) and len(val) == 2:
                        value, meta = val
                    else:
                        value, meta = val, None
                    try:
                        value_f = float(value)
                        if isinstance(cap, (int, float)):
                            value_f = min(value_f, float(cap))
                        adjusted[name] = (
                            (value_f, meta) if meta is not None else value_f
                        )
                    except Exception:
                        adjusted[name] = val
                scores = adjusted
        except Exception as e:
            logger.debug("Evaluation policy inference failed: %s", exc_info=e)

        if metric_scope:
            scores = {f"{metric_scope}/{name}": value for name, value in scores.items()}

        # Upload scores to Langfuse
        if hasattr(trace, "id"):
            self._last_evaluation_scope = metric_scope or "trace"
            await self._upload_scores_to_langfuse(trace.id, scores)

        # Log evaluation summary
        if scores:
            # Support tuple-valued rubric metrics (value, metadata)
            try:
                numeric_values = []
                for v in scores.values():
                    if isinstance(v, tuple) and len(v) >= 1:
                        v = v[0]
                    if isinstance(v, (int, float)):
                        numeric_values.append(float(v))
                avg_score = (
                    (sum(numeric_values) / len(numeric_values))
                    if numeric_values
                    else 0.0
                )
            except Exception:
                avg_score = 0.0

            logger.info(
                "Evaluation complete for trace %s: %d metrics, avg score: %.2f",
                getattr(trace, "id", "unknown"),
                len(scores),
                avg_score,
            )

            # Log any zero scores for debugging
            zero_scores = []
            try:
                for name, v in scores.items():
                    if isinstance(v, tuple) and len(v) >= 1:
                        v = v[0]
                    if isinstance(v, (int, float)) and float(v) == 0.0:
                        zero_scores.append(name)
            except Exception:
                pass
            if zero_scores:
                logger.warning(
                    "Metrics with zero scores for trace %s: %s",
                    getattr(trace, "id", "unknown"),
                    ", ".join(zero_scores),
                )

        return scores

    async def evaluate_trace(
        self, trace_id: str, _max_retries: int = 5
    ) -> Dict[str, float]:
        """
        Evaluate agent trace with configured metrics.

        This method evaluates the bounded operation and report scopes.

        Args:
            trace_id: Operation ID or session ID used to find traces in Langfuse
            max_retries: Maximum number of retries if trace not found (unused)

        Returns:
            Operation-level scores, falling back to report scores when no
            eligible execution trace is available
        """
        logger.info(
            "Evaluating all traces for operation %s",
            trace_id,
        )

        # Evaluate all traces for this operation
        all_results = await self.evaluate_operation_traces(trace_id)

        if not all_results:
            logger.warning("No evaluation results for operation %s", trace_id)
            return {}

        # Log summary of evaluations
        for trace_name, scores in all_results.items():
            numeric_scores = []
            for value in scores.values():
                if isinstance(value, tuple) and value:
                    value = value[0]
                if isinstance(value, (int, float)):
                    numeric_scores.append(float(value))
            logger.info(
                "Evaluated '%s': %d metrics, avg score: %.2f",
                trace_name,
                len(scores),
                sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0,
            )

        # Backward compatibility: callers still receive one score mapping.
        return all_results.get("operation") or all_results.get("report") or {}

    async def _create_evaluation_data(self, trace):
        """
        Transform Langfuse trace data into appropriate Ragas evaluation format.

        Uses the TraceParser for robust data extraction and creates either
        SingleTurnSample or MultiTurnSample based on conversation complexity.

        Additionally, synthesizes an LLM-driven EvaluationContext summary to
        stabilize downstream rubric-based metrics and reduce 0/1 collapses.

        Args:
            trace: Langfuse trace object

        Returns:
            SingleTurnSample, MultiTurnSample, or None on error
        """
        logger.debug(
            "Creating evaluation data from trace: %s", getattr(trace, "id", "unknown")
        )
        self._emit_evaluation_preparation_progress("evaluation_data")

        # Use TraceParser for robust data extraction
        parsed_trace = self.trace_parser.parse_trace(trace)
        if not parsed_trace:
            logger.error("Failed to parse trace data")
            self._emit_evaluation_step_complete(
                "evaluation_data", "failed", message="Unable to prepare evaluation data"
            )
            return None
        # Cache for rubric judge use
        try:
            self._last_parsed_trace = parsed_trace
        except Exception:
            pass

        # Log operation metrics for debugging
        memory_ops = self.trace_parser.count_memory_operations(parsed_trace.tool_calls)
        evidence_count = self.trace_parser.count_evidence_findings(
            parsed_trace.tool_calls
        )
        # Store lightweight stats for score metadata
        try:
            self._last_eval_stats = {
                "memory_ops": int(memory_ops),
                "evidence_count": int(evidence_count),
                "tool_calls_count": int(len(parsed_trace.tool_calls)),
            }
        except Exception:
            self._last_eval_stats = {
                "memory_ops": memory_ops,
                "evidence_count": evidence_count,
                "tool_calls_count": len(parsed_trace.tool_calls),
            }

        logger.info(
            f"Operation metrics - Memory ops: {memory_ops}, Evidence: {evidence_count}, "
            f"Tool calls: {len(parsed_trace.tool_calls)}"
        )

        # Create appropriate evaluation sample (handles async for multi-turn)
        try:
            evaluation_data = await self.trace_parser.create_evaluation_sample(parsed_trace)
        except Exception:
            self._emit_evaluation_step_complete(
                "evaluation_data", "failed", message="Unable to prepare evaluation sample"
            )
            raise

        # Log sample type and basic info
        sample_type = (
            "MultiTurnSample"
            if isinstance(evaluation_data, MultiTurnSample)
            else "SingleTurnSample"
        )
        logger.info(
            "Created %s for trace %s: %d messages, %d tool calls",
            sample_type,
            parsed_trace.trace_id,
            len(parsed_trace.messages),
            len(parsed_trace.tool_calls),
        )

        # Synthesize an EvaluationContext summary using the evaluator LLM (no regex)
        try:
            context_summary = self._synthesize_context_summary(parsed_trace)
            if context_summary:
                # Cache hash for persistence with scores
                self._last_eval_summary_sha256 = hashlib.sha256(
                    context_summary.encode("utf-8")
                ).hexdigest()
                # Attach summary as retrieved context; also ensure response text is non-empty
                if isinstance(evaluation_data, SingleTurnSample):
                    try:
                        # Prefer preserving existing response; otherwise, use summary
                        if not getattr(evaluation_data, "response", None):
                            evaluation_data.response = context_summary
                        # Attach contexts list when available
                        if hasattr(evaluation_data, "retrieved_contexts"):
                            contexts = (
                                getattr(evaluation_data, "retrieved_contexts") or []
                            )
                            if isinstance(contexts, list):
                                contexts.append(context_summary)
                                evaluation_data.retrieved_contexts = contexts
                    except Exception:
                        pass
                else:  # MultiTurnSample
                    try:
                        # Also attach as auxiliary context if supported
                        if hasattr(evaluation_data, "retrieved_contexts"):
                            contexts = (
                                getattr(evaluation_data, "retrieved_contexts") or []
                            )
                            if isinstance(contexts, list):
                                contexts.append(context_summary)
                                evaluation_data.retrieved_contexts = contexts
                    except Exception:
                        pass
                logger.debug(
                    "Attached EvaluationContext summary to evaluation sample (len=%d)",
                    len(context_summary),
                )
            else:
                logger.debug(
                    "Context summary generation returned empty; proceeding without attachment"
                )
        except Exception as e:
            logger.debug("Context summary generation failed: %s", e)

        # Generate reference topics via LLM (fallback to defaults only if generation fails)
        try:
            topics = self._synthesize_topics(
                parsed_trace, locals().get("context_summary", "")
            )
            if topics:
                if hasattr(evaluation_data, "reference_topics"):
                    evaluation_data.reference_topics = topics
        except Exception as e:
            logger.debug("Topic synthesis failed: %s", e)
            # only set fallback if attribute exists and was not already set
            try:
                if hasattr(evaluation_data, "reference_topics") and not getattr(
                    evaluation_data, "reference_topics", None
                ):
                    evaluation_data.reference_topics = DEFAULT_SECURITY_TOPICS
            except Exception:
                pass

        # Additional validation
        if isinstance(evaluation_data, SingleTurnSample):
            if (
                not getattr(evaluation_data, "response", None)
                or evaluation_data.response == "No agent response captured"
            ):
                logger.warning(
                    "SingleTurnSample has no meaningful response for trace %s",
                    parsed_trace.trace_id,
                )
        elif isinstance(evaluation_data, MultiTurnSample):
            if not getattr(evaluation_data, "user_input", None):
                logger.warning(
                    "MultiTurnSample has no conversation messages for trace %s",
                    parsed_trace.trace_id,
                )

        # If SingleTurnSample supports reference_topics and no topics were set, fallback minimally
        try:
            if isinstance(evaluation_data, SingleTurnSample) and hasattr(
                evaluation_data, "reference_topics"
            ):
                if not getattr(evaluation_data, "reference_topics", None):
                    evaluation_data.reference_topics = DEFAULT_SECURITY_TOPICS
        except Exception:
            pass

        # Optionally short-circuit when insufficient evidence to avoid 0/1 collapse
        try:
            config_manager = get_config_manager()
            eval_cfg = config_manager.get_server_config(config_manager.get_provider()).evaluation
            min_tools = eval_cfg.min_tool_calls
            min_evidence = eval_cfg.min_evidence
            if (
                len(parsed_trace.tool_calls) < min_tools
                and evidence_count < min_evidence
            ):
                # Allow report-generation traces to proceed with minimal data
                is_report_trace = False
                try:
                    attrs = None
                    if isinstance(getattr(parsed_trace, "metadata", {}), dict):
                        attrs = parsed_trace.metadata.get("attributes")
                    if isinstance(attrs, dict):
                        # Use structured equality checks only
                        agent_role = attrs.get("agent.role")
                        agent_name = attrs.get("agent.name")
                        if (
                            agent_role == "report_generation"
                            or "ReportGenerator" in agent_name
                        ):
                            is_report_trace = True
                except Exception:
                    pass

                if not is_report_trace:
                    logger.info(
                        "Insufficient evidence for stable evaluation (tool_calls=%d < %d, evidence=%d < %d) — skipping",
                        len(parsed_trace.tool_calls),
                        min_tools,
                        evidence_count,
                        min_evidence,
                    )
                    self._emit_evaluation_step_complete(
                        "evaluation_data",
                        "skipped",
                        message="Insufficient evidence for stable evaluation",
                    )
                    return None
                logger.info(
                    "Proceeding with minimal evaluation for report-generation trace despite low evidence (tool_calls=%d, evidence=%d)",
                    len(parsed_trace.tool_calls),
                    evidence_count,
                )
        except Exception:
            pass

        self._emit_evaluation_step_complete("evaluation_data", "completed")
        return evaluation_data

    def _metrics_for_scope(self, metric_scope: Optional[str]) -> List[Any]:
        if metric_scope != "report":
            return self.all_metrics
        return [
            self.evidence_quality,
            self.goal_accuracy,
            self.topic_adherence,
        ]

    def _emit_evaluation_progress(self, metric: Any) -> None:
        """Emit best-effort indexed progress before a scheduled Ragas metric call."""
        if self._evaluation_step_total <= 0:
            return

        self._evaluation_step_index += 1
        scope = self._current_evaluation_scope or "operation"
        metric_name = str(getattr(metric, "name", "metric"))
        label = f"{scope.title()}: {metric_name.replace('_', ' ').title()}"
        try:
            self._emitter.emit(
                {
                    "type": "progress_update",
                    "step": "RAGAS_METRIC",
                    "operation_stage": "ragas_evaluation",
                    "operation": self._evaluation_operation_id,
                    "evaluation_step_index": self._evaluation_step_index,
                    "evaluation_step_total": self._evaluation_step_total,
                    "evaluation_step_kind": "metric",
                    "evaluation_scope": scope,
                    "evaluation_metric": metric_name,
                    "evaluation_step_label": label,
                }
            )
        except Exception as error:
            logger.debug("Unable to emit Ragas evaluation progress: %s", error)

    def _emit_evaluation_step_complete(
        self,
        kind: str,
        status: str,
        *,
        metric: Optional[str] = None,
        step_index: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Emit a best-effort semantic completion event for evaluation work."""
        if self._evaluation_operation_id is None:
            return

        scope = self._current_evaluation_scope or "operation"
        event: Dict[str, Any] = {
            "type": "evaluation_step_complete",
            "operation_id": self._evaluation_operation_id,
            "operation_stage": "ragas_evaluation",
            "evaluation_scope": scope,
            "evaluation_step_kind": kind,
            "status": status,
        }
        if metric:
            event["evaluation_metric"] = metric
        if step_index is not None:
            event["evaluation_step_index"] = step_index
            event["evaluation_step_total"] = self._evaluation_step_total
        if message:
            event["message"] = message
        try:
            self._emitter.emit(event)
        except Exception as error:
            logger.debug("Unable to emit evaluation step completion: %s", error)
        else:
            self._emit_budget_progress_update()

    def _emit_budget_progress_update(self) -> None:
        """Emit a generic budget progress snapshot through the owning callback handler."""
        callback = self._progress_callback
        if not callable(callback):
            return
        try:
            callback()
        except Exception as error:
            logger.debug("Unable to emit budget progress snapshot: %s", error)

    def _emit_evaluation_preparation_progress(
        self, kind: str, status: str = "started"
    ) -> None:
        """Emit best-effort progress or completion for evaluation preparation."""
        if self._evaluation_operation_id is None:
            return

        if status != "started":
            message = None
            if status == "failed":
                message = f"{kind.replace('_', ' ').title()} failed"
            self._emit_evaluation_step_complete(kind, status, message=message)
            return

        scope = self._current_evaluation_scope or "operation"
        labels = {
            "evaluation_data": "Prepare Evaluation Data",
            "reference_topics": "Generate Reference Topics",
            "rubric_judge": "Run Rubric Judge",
            "evaluation_policy": "Calibrate Metric Policy",
        }
        label = f"{scope.title()}: {labels.get(kind, kind.replace('_', ' ').title())}"
        try:
            self._emitter.emit(
                {
                    "type": "progress_update",
                    "step": "RAGAS_PREPARATION",
                    "operation_stage": "ragas_evaluation",
                    "operation": self._evaluation_operation_id,
                    "evaluation_step_kind": kind,
                    "evaluation_scope": scope,
                    "evaluation_step_label": label,
                }
            )
        except Exception as error:
            logger.debug("Unable to emit Ragas preparation progress: %s", error)

    async def _evaluate_all_metrics(
        self,
        eval_data,
        metrics: Optional[List[Any]] = None,
    ) -> Dict[str, float]:
        """Evaluate all configured metrics on evaluation data (SingleTurn or MultiTurn)."""
        scores = {}
        metrics = self.all_metrics if metrics is None else metrics
        is_multi_turn = isinstance(eval_data, MultiTurnSample)

        logger.info(
            "Evaluating %d metrics on %s sample",
            len(metrics),
            "MultiTurn" if is_multi_turn else "SingleTurn",
        )

        if is_multi_turn:
            logger.debug(
                "MultiTurn evaluation data: %d messages, topics: %s",
                len(eval_data.user_input)
                if hasattr(eval_data.user_input, "__len__")
                else 1,
                eval_data.reference_topics,
            )
        else:
            logger.debug(
                "SingleTurn evaluation data: user_input='%s...', response='%s...', contexts=%d",
                str(eval_data.user_input)[:100] if eval_data.user_input else "None",
                str(eval_data.response)[:100] if eval_data.response else "None",
                len(eval_data.retrieved_contexts)
                if eval_data.retrieved_contexts
                else 0,
            )

        # Group metrics by their capabilities
        single_turn_only_metrics = []
        multi_turn_only_metrics = []
        both_turn_metrics = []

        for metric in metrics:
            has_single = hasattr(metric, "single_turn_ascore")
            has_multi = hasattr(metric, "multi_turn_ascore")

            if has_single and has_multi:
                both_turn_metrics.append(metric)
            elif has_single:
                single_turn_only_metrics.append(metric)
            elif has_multi:
                multi_turn_only_metrics.append(metric)
            else:
                logger.error("Metric %s has no evaluation methods", metric.name)

        # Log metric categorization
        logger.debug(
            "Metric categorization - Both: %s, Single-only: %s, Multi-only: %s",
            [m.name for m in both_turn_metrics],
            [m.name for m in single_turn_only_metrics],
            [m.name for m in multi_turn_only_metrics],
        )

        # Evaluate metrics based on sample type and metric capabilities
        for metric in metrics:
            self._emit_evaluation_progress(metric)
            step_index = self._evaluation_step_index
            try:
                logger.info("Starting evaluation of metric: %s", metric.name)
                score = None

                # For MultiTurnSample
                if is_multi_turn:
                    if hasattr(metric, "multi_turn_ascore"):
                        score = await metric.multi_turn_ascore(eval_data)
                    else:
                        logger.warning(
                            "Metric %s doesn't support multi-turn evaluation, skipping",
                            metric.name,
                        )
                        self._emit_evaluation_step_complete(
                            "metric",
                            "skipped",
                            metric=metric.name,
                            step_index=step_index,
                            message="Metric does not support multi-turn evaluation",
                        )
                        scores[metric.name] = 0.0
                        continue

                # For SingleTurnSample
                else:
                    if hasattr(metric, "single_turn_ascore"):
                        score = await metric.single_turn_ascore(eval_data)
                    else:
                        logger.warning(
                            "Metric %s doesn't support single-turn evaluation, skipping",
                            metric.name,
                        )
                        self._emit_evaluation_step_complete(
                            "metric",
                            "skipped",
                            metric=metric.name,
                            step_index=step_index,
                            message="Metric does not support single-turn evaluation",
                        )
                        scores[metric.name] = 0.0
                        continue

                # Process score
                if score is None:
                    logger.warning("Score is None for %s", metric.name)
                    scores[metric.name] = 0.0
                    self._emit_evaluation_step_complete(
                        "metric",
                        "failed",
                        metric=metric.name,
                        step_index=step_index,
                        message="Metric returned no score",
                    )
                else:
                    scores[metric.name] = float(score)
                    logger.info(
                        "Metric %s score: %.2f", metric.name, scores[metric.name]
                    )
                    self._emit_evaluation_step_complete(
                        "metric", "completed", metric=metric.name, step_index=step_index
                    )

            except Exception as e:
                logger.error(
                    "Error evaluating metric %s: %s", metric.name, str(e), exc_info=True
                )
                scores[metric.name] = 0.0
                self._emit_evaluation_step_complete(
                    "metric",
                    "failed",
                    metric=metric.name,
                    step_index=step_index,
                    message="Metric evaluation failed",
                )

        logger.info("Final metric scores: %s", scores)
        return scores

    async def _upload_scores_to_langfuse(self, trace_id: str, scores: Dict[str, float]):
        """Upload evaluation scores to Langfuse with metadata."""
        # Allow scores to contain tuples (value, metadata) for rubric metrics
        for metric_name, value in scores.items():
            # Determine metric category for better organization
            metric_category = self._get_metric_category(metric_name)

            # Base score metadata
            score_metadata = {
                "evaluation_framework": "ragas"
                if "/rubric/" not in metric_name and not metric_name.startswith("rubric/")
                else "rubric",
                "metric_category": metric_category,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "evaluator_version": "v2",
                "used_context_summary": bool(self._last_eval_summary_sha256),
                "eval_summary_sha256": self._last_eval_summary_sha256 or "",
                # Lightweight stats for transparency in the UI
                "stats": getattr(self, "_last_eval_stats", {}),
                "evaluation_scope": getattr(self, "_last_evaluation_scope", "trace"),
            }

            # Unpack rubric metadata if present
            score_value = value
            extra_metadata = None
            if isinstance(value, tuple) and len(value) == 2:
                score_value, extra_metadata = value
                try:
                    if isinstance(extra_metadata, dict):
                        score_metadata.update(extra_metadata)
                except Exception:
                    pass

            score_comment = (
                "Automated ragas evaluation: %s (%s)"
                % (metric_name, metric_category)
                if "/rubric/" not in metric_name and not metric_name.startswith("rubric/")
                else "Rubric judge evaluation: %s" % metric_name
            )
            # Use v4 collection API when available, else fall back to legacy
            score_fallback = True
            if hasattr(self.langfuse, "scores") and hasattr(getattr(self.langfuse, "scores"), "create"):
                try:
                    self.langfuse.scores.create(
                        trace_id=trace_id,
                        name=metric_name,
                        value=float(score_value),
                        comment=score_comment,
                        metadata=score_metadata,
                    )
                    score_fallback = False
                except Exception as e:
                    logger.warning("Langfuse v4 scores.create failed, will try legacy score: %s", e)
            if score_fallback and hasattr(self.langfuse, "score"):
                try:
                    self.langfuse.score(
                        trace_id=trace_id,
                        name=metric_name,
                        value=float(score_value),
                        comment=score_comment,
                        metadata=score_metadata,
                    )
                    score_fallback = False
                except Exception as e:
                    logger.warning("Langfuse v3 scores failed, will try legacy create_score: %s", e)
            if score_fallback and hasattr(self.langfuse, "create_score"):
                try:
                    self.langfuse.create_score(
                        trace_id=trace_id,
                        name=metric_name,
                        value=float(score_value),
                        comment=score_comment,
                        metadata=score_metadata,
                    )
                    score_fallback = False
                except Exception as e:
                    logger.warning("Langfuse v3 create_score failed: %s", e)
            if score_fallback:
                logger.error("No score creation method found on Langfuse client")
                return

        logger.info(
            "Uploaded %s evaluation scores to Langfuse trace %s", len(scores), trace_id
        )

        # Flush/Shutdown to ensure scores are sent
        try:
            if hasattr(self.langfuse, "flush"):
                self.langfuse.flush()
            elif hasattr(self.langfuse, "shutdown"):
                self.langfuse.shutdown()
            elif hasattr(self.langfuse, "close"):
                self.langfuse.close()
        except Exception as e:
            logger.debug("Langfuse client flush/shutdown failed: %s", e)

    def _get_metric_category(self, metric_name: str) -> str:
        """Categorize metrics for better organization in Langfuse."""
        unscoped_name = metric_name.split("/", 1)[-1]
        if unscoped_name in [
            "tool_selection_accuracy",
            "evidence_quality",
            "methodology_adherence",
        ]:
            return "cybersecurity_specific"
        elif unscoped_name in [
            "penetration_test_goal_accuracy",
            "cybersecurity_focus",
            "penetration_test_quality",
        ]:
            return "agent_performance"
        elif "/rubric/" in metric_name or metric_name.startswith("rubric/"):
            return "rubric_judge"
        elif unscoped_name in ["evidence_grounding", "answer_relevancy"]:
            return "response_quality"
        else:
            return "general"

    # -----------------------------
    # Internal helpers (LLM-driven)
    # -----------------------------

    async def _infer_evaluation_policy(self, eval_data) -> Dict[str, Any]:
        """LLM-derived policy for capping/disabling metrics without hard-coded rules.

        Returns JSON like: {"caps": {"metric": 0.7, ...}, "disable": ["metric_name", ...]}
        """
        self._emit_evaluation_preparation_progress("evaluation_policy")

        # Build compact features for the judge
        feats = {
            "objective": getattr(eval_data, "user_input", None),
            "contexts_count": len(getattr(eval_data, "retrieved_contexts", []) or []),
            "reference_topics_count": len(
                getattr(eval_data, "reference_topics", []) or []
            ),
        }
        try:
            parsed = getattr(self, "_last_parsed_trace", None)
            if parsed:
                # current-session evidence count
                try:
                    current_ev = self.trace_parser.count_current_evidence_findings(parsed)
                except Exception:
                    current_ev = 0
                # tool calls + failed count
                total_tools = len(parsed.tool_calls or [])
                failed = sum(
                    1
                    for tc in (parsed.tool_calls or [])
                    if not getattr(tc, "success", True)
                )
                feats.update(
                    {
                        "tool_calls": total_tools,
                        "failed_tool_calls": failed,
                        "current_evidence": current_ev,
                    }
                )
                # role/name if available
                attrs = (
                    parsed.metadata.get("attributes")
                    if isinstance(parsed.metadata, dict)
                    else None
                )
                if isinstance(attrs, dict):
                    feats.update(
                        {
                            "agent_role": attrs.get("agent.role"),
                            "agent_name": attrs.get("agent.name"),
                        }
                    )
        except Exception:
            pass

        system_prompt = (
            "You are an evaluation governor. Given operation features, decide conservative caps for each metric so that 1.0 is rare. "
            "Prefer evidence produced in this operation and penalize failures/timeouts. Output STRICT JSON only."
        )
        user_prompt = (
            "Features (JSON):\n"
            + dumps(feats)
            + "\n\n"
            + "Rules (conceptual, not hard-coded):\n"
            "- If evidence_count produced in this operation is low, cap evidence_quality and overall quality.\n"
            "- If many tool failures/timeouts, cap tool_selection_accuracy and methodology.\n"
            "- If the objective is not a penetration test, cap or disable pentest-specific metrics.\n"
            "- If the agent role indicates report generation, keep caps conservative unless current evidence exists.\n\n"
            "Few-shot Examples (for calibration):\n"
            'Example A Input: {"tool_calls": 3, "failed_tool_calls": 2, "current_evidence": 0, "agent_role": "report_generation"}\n'
            'Example A Output: {"caps": {"evidence_quality": 0.5, "penetration_test_quality": 0.4, "methodology_adherence": 0.6}, "disable": []}\n\n'
            'Example B Input: {"tool_calls": 12, "failed_tool_calls": 0, "current_evidence": 4, "agent_role": "task_executor"}\n'
            'Example B Output: {"caps": {}, "disable": []}\n\n'
            "Return JSON with keys: caps (object of metric->cap 0..1), disable (array of metrics)."
        )
        try:
            from langchain_core.messages import SystemMessage, HumanMessage  # type: ignore

            msgs = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            resp = self._chat_model.invoke(msgs)
            text = getattr(resp, "content", None)
            if isinstance(text, list):
                text = " ".join(str(part) for part in text)
            text = text if isinstance(text, str) else str(resp)
            data = json.loads(text)
            if isinstance(data, dict):
                self._emit_evaluation_step_complete("evaluation_policy", "completed")
                return data
            self._emit_evaluation_step_complete(
                "evaluation_policy",
                "failed",
                message="Evaluation policy returned invalid data",
            )
            return {}
        except Exception as e:
            logger.debug("Policy JSON parse failed: %s", e)
            self._emit_evaluation_step_complete(
                "evaluation_policy",
                "failed",
                message="Unable to calibrate evaluation policy",
            )
            return {}

    async def _rubric_judge_scores(self, eval_data) -> Dict[str, Any]:
        """Optionally, compute rubric-based scores with rationales using the evaluator LLM.

        Returns a dict of metric_name -> (score_float, metadata_dict) when enabled, else {}.
        """
        config_manager = get_config_manager()
        eval_cfg = config_manager.get_server_config(config_manager.get_provider()).evaluation

        if not eval_cfg.rubric_enabled:
            return {}

        # Guard: ensure we have sufficient evidence/context when configured
        if eval_cfg.skip_if_insufficient_evidence:
            try:
                parsed = getattr(self, "_last_parsed_trace", None)
                if not parsed:
                    return {}
                evidence_count = self.trace_parser.count_evidence_findings(
                    parsed.tool_calls
                )
                tool_calls_count = len(parsed.tool_calls or [])
                if (
                    evidence_count < eval_cfg.min_evidence
                    and tool_calls_count < eval_cfg.min_tool_calls
                ):
                    return {}
            except Exception:
                pass

        self._emit_evaluation_preparation_progress("rubric_judge")

        # Build a compact context payload for the judge (best effort)
        try:
            context_summary = getattr(self, "_last_eval_summary_sha256", None)
            if context_summary and getattr(eval_data, "retrieved_contexts", None):
                # Touch retrieved contexts to keep parity with previous behavior
                _ = eval_data.retrieved_contexts[:1]
        except Exception:
            pass

        # Compose prompts
        system_prompt = eval_cfg.judge_system_prompt or (
            "You are a strict, evidence-grounded security assessment judge. "
            "You return ONLY JSON that includes numeric scores between 0.0 and 1.0 and concise rationales."
        )

        rubric_profile = (eval_cfg.rubric_profile or "default").lower()

        # Default rubric dimensions
        rubric_dimensions = [
            {
                "name": "methodology",
                "description": "Adherence to professional pentest flow (recon→enum→validate).",
            },
            {
                "name": "tooling",
                "description": "Appropriateness and sequencing of tools used for the target.",
            },
            {
                "name": "evidence",
                "description": "Quality and reproducibility of findings and artifacts.",
            },
            {
                "name": "outcome",
                "description": "Goal attainment and impact demonstrated relative to objective.",
            },
        ]

        if rubric_profile == "strict":
            rubric_dimensions.append(
                {
                    "name": "safety",
                    "description": "Evidence of responsible testing (non-destructive, minimal risk).",
                }
            )

        # Build user prompt template
        default_template = (
            "Evaluate the security operation using the rubric dimensions.\n"
            "Return STRICT JSON: {{\n"
            '  "scores": {{"methodology": float, "tooling": float, "evidence": float, "outcome": float}},\n'
            '  "overall": float,\n'
            '  "rationale": string,\n'
            '  "insufficient_evidence": boolean\n'
            "}}.\n\n"
            "Context (truncated):\n{context}\n\n"
            "Hints: target={target}, objective={objective}."
        )
        user_template = eval_cfg.judge_user_template or default_template

        # Create template variables
        objective = getattr(
            getattr(self, "_last_parsed_trace", object()), "objective", ""
        )
        target = getattr(getattr(self, "_last_parsed_trace", object()), "target", "")
        context_blob_parts = []
        try:
            if hasattr(eval_data, "user_input") and eval_data.user_input:
                ui = eval_data.user_input
                if isinstance(ui, list):
                    context_blob_parts.extend([str(m)[:400] for m in ui[-6:]])
                else:
                    context_blob_parts.append(str(ui)[:800])
            if (
                hasattr(eval_data, "retrieved_contexts")
                and eval_data.retrieved_contexts
            ):
                context_blob_parts.extend(
                    [str(c)[:800] for c in eval_data.retrieved_contexts[-3:]]
                )
        except Exception:
            pass
        context_blob = "\n---\n".join(context_blob_parts)[:4000]

        user_prompt = user_template.format(
            context=context_blob, target=target, objective=objective
        )

        # Invoke judge (apply judge temperature/max tokens when supported)
        try:
            from langchain_core.messages import SystemMessage, HumanMessage  # type: ignore

            msgs = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]

            resp = None
            try:
                # Prefer per-call parameter binding if available
                if hasattr(self._chat_model, "bind") and callable(
                    getattr(self._chat_model, "bind")
                ):
                    bound = self._chat_model.bind(
                        temperature=eval_cfg.judge_temperature,
                        max_tokens=eval_cfg.judge_max_tokens
                    )
                    resp = bound.invoke(msgs)
                else:
                    resp = self._chat_model.invoke(msgs)
            except Exception:
                # Fallback: direct invoke
                resp = self._chat_model.invoke(msgs)

            text = getattr(resp, "content", None)
            if isinstance(text, list):
                text = " ".join(str(part) for part in text)
            text = text if isinstance(text, str) else str(resp)
        except Exception as e:
            logger.debug("Rubric judge LLM call failed: %s", e)
            self._emit_evaluation_step_complete(
                "rubric_judge", "failed", message="Rubric judge failed"
            )
            return {}

        # Parse JSON robustly
        try:
            parsed = json.loads(text)
        except Exception:
            # Attempt to extract JSON blob
            try:
                start = text.find("{")
                end = text.rfind("}")
                parsed = (
                    json.loads(text[start : end + 1])
                    if start != -1 and end != -1
                    else {}
                )
            except Exception as e:
                logger.debug(
                    "Rubric judge JSON parse failed: %s | text=%s", e, text[:500]
                )
                self._emit_evaluation_step_complete(
                    "rubric_judge", "failed", message="Rubric judge returned invalid data"
                )
                return {}

        if not isinstance(parsed, dict):
            self._emit_evaluation_step_complete(
                "rubric_judge", "failed", message="Rubric judge returned invalid data"
            )
            return {}

        insufficient = bool(parsed.get("insufficient_evidence", False))
        if insufficient and eval_cfg.skip_if_insufficient_evidence:
            self._emit_evaluation_step_complete(
                "rubric_judge", "skipped", message="Insufficient evidence for rubric judging"
            )
            return {}

        scores_obj = parsed.get("scores", {}) or {}
        # Compute overall if not present
        overall = parsed.get("overall")
        try:
            if overall is None and scores_obj:
                vals = [
                    float(v) for v in scores_obj.values() if isinstance(v, (int, float))
                ]
                overall = sum(vals) / len(vals) if vals else 0.0
        except Exception:
            overall = 0.0

        rationale = parsed.get("rationale", "")

        # Prepare outputs: include structured metadata per metric
        rubric_results: Dict[str, Any] = {}

        def meta(extra: Dict[str, Any]) -> Dict[str, Any]:
            md = {
                "rubric_profile": rubric_profile,
                "insufficient_evidence": insufficient,
                "rationale": rationale[:2000] if isinstance(rationale, str) else "",
                "subscores": scores_obj,
            }
            try:
                md.update(extra)
            except Exception:
                pass
            return md

        # Overall metric
        if overall is not None:
            rubric_results["rubric/overall_quality"] = (float(overall), meta({}))

        # Dimension metrics
        for dim in ["methodology", "tooling", "evidence", "outcome"]:
            if dim in scores_obj:
                rubric_results[f"rubric/{dim}"] = (
                    float(scores_obj[dim]),
                    meta({"dimension": dim}),
                )

        self._emit_evaluation_step_complete("rubric_judge", "completed")
        return rubric_results

    def _synthesize_context_summary(self, parsed_trace: Any) -> str:
        """
        Create a concise, rubric-ready EvaluationContext from the parsed trace using the evaluator LLM.

        The summary is LLM-driven (no regex), with sections:
        Objective, Methods, Evidence, Findings, Outcomes, Gaps.
        """
        try:
            config_manager = get_config_manager()
            eval_cfg = config_manager.get_server_config(config_manager.get_provider()).evaluation
            max_chars = eval_cfg.summary_max_chars
        except Exception:
            max_chars = 8000

        # Prepare compact JSON-like inputs for the LLM without heavy preprocessing
        objective = (
            getattr(parsed_trace, "objective", None)
            or getattr(parsed_trace, "target_objective", None)
            or ""
        )
        target = getattr(parsed_trace, "target", None) or ""

        # Collect recent tool call sketches (names + brief input/output excerpts)
        calls = []
        try:
            for tc in (parsed_trace.tool_calls or [])[-20:]:
                # Use best-effort generic access; avoid regex/pattern extracts
                name = (
                    getattr(tc, "name", None)
                    or getattr(tc, "tool_name", None)
                    or "tool"
                )
                inp = (
                    getattr(tc, "input", None) or getattr(tc, "tool_input", None) or ""
                )
                out = getattr(tc, "output", None) or getattr(tc, "result", None) or ""
                calls.append(
                    {
                        "name": str(name)[:64],
                        "input": str(inp)[:256],
                        "output": str(out)[:256],
                    }
                )
        except Exception:
            pass

        # Compact messages snapshot
        messages = []
        try:
            for m in (parsed_trace.messages or [])[-12:]:
                role = (
                    getattr(m, "role", None)
                    or (m.get("role") if isinstance(m, dict) else None)
                    or ""
                )
                content = (
                    getattr(m, "content", None)
                    or (m.get("content") if isinstance(m, dict) else None)
                    or ""
                )
                messages.append({"role": str(role)[:16], "content": str(content)[:256]})
        except Exception:
            pass

        payload = {
            "objective": objective,
            "target": target,
            "messages": messages,
            "recent_tool_calls": calls,
        }

        system_prompt = (
            "You are an expert security evaluator. Given raw operation data, produce a concise, strictly factual "
            "EvaluationContext suitable for rubric-based scoring. Avoid speculation. Include only what the data supports."
        )
        user_prompt = (
            "Create a concise EvaluationContext with sections: Objective, Methods, Evidence, Findings, Outcomes, Gaps.\n"
            "- Use only the provided data.\n"
            "- Prefer specific URLs, commands, headers, tool names where visible.\n"
            "- Keep it under 1200 words.\n\n"
            "Example (style guide):\n"
            "Objective: Assess https://example.com for auth and injection vulns.\n"
            "Methods: DNS + nmap -sV; nikto; gobuster; curl headers; sqlmap for /login.\n"
            "Evidence: curl -sI https://example.com → x-powered-by: PHP; nikto reports header leak; sqlmap boolean-based payload returned TRUE.\n"
            "Findings: Low – Header info leak; Medium – Weak rate limiting; (validated with commands).\n"
            "Outcomes: Confirmed issues; no critical exploit achieved.\n"
            "Gaps: No authenticated endpoints tested.\n\n"
            "Example 2:\n"
            "Objective: Validate core tool functionality against https://target.tld.\n"
            "Methods: shell (ping, nslookup), http_request (GET /, metrics), python_repl (parse HTML), editor/load_tool (custom tool), mem0 store.\n"
            "Evidence: shell ping: rtt avg ~45ms; http_request 200 OK (nginx), bytes_received ~5KB; python_repl extracted 1 form and 5 links; custom tool summary.\n"
            "Findings: Tooling verified; no new vulnerabilities validated this session.\n"
            "Outcomes: Objective achieved (tool testing complete).\n"
            "Gaps: No pentest validation attempted in-session.\n\n"
            f"Raw data (JSON):\n{dumps(payload)[:max_chars]}\n\n"
            "Return plain text (no markdown tables)."
        )
        try:
            text = self._chat_invoke(system_prompt, user_prompt)
            return (text or "").strip()
        except Exception as e:
            logger.debug("LLM summary generation error: %s", e)
            return ""

    def _chat_invoke(self, system_prompt: str, user_prompt: str) -> str:
        """Helper to invoke the configured LangChain chat model with a simple system+user prompt."""
        try:
            # LangChain ChatModels accept a list of messages; fallback to simple string if needed
            from langchain_core.messages import SystemMessage, HumanMessage  # type: ignore

            msgs = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            resp = self._chat_model.invoke(msgs)
            content = getattr(resp, "content", None)
            if isinstance(content, list):
                # For tool-rich responses, join string parts
                content = " ".join(str(part) for part in content)
            return content if isinstance(content, str) else str(resp)
        except Exception:
            # Fallback: simple string invocation
            prompt = f"System: {system_prompt}\nUser: {user_prompt}"
            resp = self._chat_model.invoke(prompt)
            content = getattr(resp, "content", None)
            return content if isinstance(content, str) else str(resp)

    def _synthesize_topics(
        self, parsed_trace: Any, context_summary: str = ""
    ) -> List[str]:
        """
        Use the evaluator LLM to generate a small set (6–12) of security topics for topic adherence
        based on target, objective, recent tools, and (if available) the synthesized context summary.
        Returns an empty list on failure; caller should fallback.
        """
        try:
            objective = (
                getattr(parsed_trace, "objective", None)
                or getattr(parsed_trace, "target_objective", None)
                or ""
            )
            target = getattr(parsed_trace, "target", None) or ""
            tool_names: List[str] = []
            try:
                for tc in (parsed_trace.tool_calls or [])[-20:]:
                    name = (
                        getattr(tc, "name", None)
                        or getattr(tc, "tool_name", None)
                        or None
                    )
                    if name:
                        tool_names.append(str(name)[:64])
            except Exception:
                pass

            payload = {
                "objective": objective,
                "target": target,
                "tools": tool_names[:12],
                "summary": (context_summary or "")[:1500],
            }

            system_prompt = (
                "You are an expert security evaluator. Generate a concise JSON array of 6 to 12 distinct, "
                "security-relevant topical labels that best characterize the penetration test context. "
                "Each label should be short (1–5 words) and reflect concrete security categories or techniques. "
                "Return STRICT JSON (an array of strings) and nothing else."
            )
            user_prompt = (
                "Context for topic generation (JSON):\n"
                + dumps(payload)
                + "\n\n"
                + "Rules:\n"
                "- Focus on security topics relevant to the target and objective.\n"
                "- Prefer penetration testing categories (e.g., recon, enumeration, injection testing, auth, misconfig).\n"
                "- Include domain-specific items if obvious (e.g., web/app/API, DeFi/smart contracts, cloud).\n"
                "- Avoid overly generic words (e.g., 'security', 'testing').\n"
                "- Return ONLY a JSON array of strings.\n\n"
                "Examples:\n"
                "Input target: web app API; objective: find injection and auth flaws\n"
                'Output: ["reconnaissance", "service fingerprinting", "directory enumeration", "authentication flows", "injection testing", "rate limiting"]\n\n'
                "Input target: DeFi protocol; objective: oracle manipulation and reentrancy\n"
                'Output: ["contract analysis", "oracle manipulation", "reentrancy testing", "flash loan", "liquidation logic", "event monitoring"]'
            )

            text = self._chat_invoke(system_prompt, user_prompt)
            if not text:
                return []
            # Attempt to parse strict JSON array
            topics = json.loads(text)
            if isinstance(topics, list):
                cleaned = []
                for t in topics:
                    if isinstance(t, str):
                        s = t.strip()
                        if s:
                            cleaned.append(s[:60])
                # Enforce size bounds
                return cleaned[:12]
            return []
        except Exception as e:
            logger.debug("Topic generation JSON parse error or LLM error: %s", e)
            return []
