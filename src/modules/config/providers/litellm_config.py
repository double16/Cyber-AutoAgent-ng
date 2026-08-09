#!/usr/bin/env python3
"""
LiteLLM provider configuration helpers.

This module provides configuration utilities specific to LiteLLM universal gateway,
including model ID parsing, embedding defaults, and configuration alignment.
"""

import importlib.util
import asyncio
import threading
import weakref
from typing import Any, Dict, List, Optional, Tuple

import litellm

from modules.config.system.env_reader import EnvironmentReader
from modules.config.system.logger import get_logger
from modules.config.types import (
    LITELLM_EMBEDDING_DEFAULTS,
    DEFAULT_LITELLM_EMBEDDING,
    EMBEDDING_DIMENSIONS,
    ModelProvider,
    LLMConfig,
    EmbeddingConfig,
    MemoryLLMConfig,
)

logger = get_logger("Config.LiteLLMProvider")


class _LoopLocalLiteLLMLoggingWorker:
    """Proxy LiteLLM async logging to one worker per event loop.

    LiteLLM's global logging worker owns an asyncio.Queue. Under Strands/tool
    thread pools, multiple event loops can call LiteLLM in the same process, and
    Python 3.13 rejects awaiting a queue bound to another loop.
    """

    def __init__(self, original_worker: Any, worker_cls: Any) -> None:
        self._original_worker = original_worker
        self._worker_cls = worker_cls
        self._workers: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
        self._retired_workers: weakref.WeakSet[Any] = weakref.WeakSet()
        self._lock = threading.RLock()
        self._caa_loop_local_proxy = True

    def _all_workers(self) -> List[Any]:
        with self._lock:
            workers = list(self._retired_workers)
            seen_worker_ids = {id(worker) for worker in workers}
            for worker in self._workers.values():
                if id(worker) not in seen_worker_ids:
                    workers.append(worker)
        return workers

    def _worker_for_current_loop(self) -> Any:
        loop = asyncio.get_running_loop()
        with self._lock:
            worker = self._workers.get(loop)
            if worker is None:
                worker = self._worker_cls(
                    timeout=getattr(self._original_worker, "timeout", None),
                    max_queue_size=getattr(self._original_worker, "max_queue_size", None),
                )
                self._workers[loop] = worker
            return worker

    def ensure_initialized_and_enqueue(self, async_coroutine: Any) -> None:
        try:
            self._worker_for_current_loop().ensure_initialized_and_enqueue(async_coroutine)
        except RuntimeError:
            close = getattr(async_coroutine, "close", None)
            if callable(close):
                close()
            logger.debug("LiteLLM async logging called without a running event loop", exc_info=True)

    def start(self) -> None:
        self._worker_for_current_loop().start()

    def enqueue(self, coroutine: Any) -> None:
        self._worker_for_current_loop().enqueue(coroutine)

    async def stop(self) -> None:
        with self._lock:
            active_workers = list(self._workers.values())
            workers = self._all_workers()
        for worker in workers:
            await worker.stop()
        with self._lock:
            for worker in active_workers:
                self._retired_workers.add(worker)
            self._workers.clear()

    async def flush(self) -> None:
        for worker in self._all_workers():
            await worker.flush()

    async def clear_queue(self) -> None:
        for worker in self._all_workers():
            await worker.clear_queue()

    def _flush_on_exit(self) -> None:
        flush_on_exit = getattr(self._original_worker, "_flush_on_exit", None)
        if callable(flush_on_exit):
            flush_on_exit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original_worker, name)


def configure_litellm_runtime() -> None:
    """Apply Cyber-AutoAgent runtime defaults and LiteLLM compatibility patches."""
    litellm.drop_params = True
    litellm.modify_params = True
    litellm.num_retries = 5
    litellm.respect_retry_after_header = True

    try:
        from litellm.litellm_core_utils import logging_worker

        current_worker = logging_worker.GLOBAL_LOGGING_WORKER
        if getattr(current_worker, "_caa_loop_local_proxy", False):
            return
        logging_worker.GLOBAL_LOGGING_WORKER = _LoopLocalLiteLLMLoggingWorker(
            current_worker,
            logging_worker.LoggingWorker,
        )
    except Exception:
        logger.debug("Unable to install LiteLLM loop-local logging worker", exc_info=True)


configure_litellm_runtime()


def split_litellm_model_id(model_id: str) -> Tuple[str, str, str]:
    """Split LiteLLM model id into provider prefix and base id.

    Args:
        model_id: Full LiteLLM model ID (e.g., "bedrock/claude-3", "openai/gpt-4")

    Returns:
        Tuple of (provider_prefix, base_model_id)
        Returns ("", model_id) if no prefix found
    """
    variant = None
    if not model_id or not isinstance(model_id, str):
        return "", "", ""
    if ":" in model_id:
        model_id, variant = model_id.split(":", maxsplit=1)
    if "/" in model_id:
        prefix, base = model_id.split("/", 1)
        # Special handling for Gemini "models/" prefix
        if prefix.lower() == "models":
            prefix = "gemini"
        return prefix.lower(), base, f"{base}:{variant}" if variant else base
    return "", model_id, f"{model_id}:{variant}" if variant else model_id


def get_context_window_fallbacks(provider: str) -> Optional[List[Dict[str, List[str]]]]:
    """Optional model fallback mappings for context window resolution.

    Currently returns None by default. Kept as an extension point if a future
    config source wants to provide structured context window fallbacks.

    Args:
        provider: Provider name

    Returns:
        None (no fallbacks configured by default)
    """
    return None


def align_litellm_defaults(
    defaults: Dict[str, Any], env_reader: EnvironmentReader
) -> None:
    """Ensure LiteLLM configuration components stay aligned with the selected model.

    Uses LiteLLM's get_max_tokens() API to dynamically cap max_tokens based on
    model limits from model_prices_and_context_window.json. This ensures we stay
    within model limits without hardcoding values that may change.

    Aligns:
    - Main LLM max_tokens to model limits
    - memory_llm, evaluation_llm, swarm_llm configs to main LLM model
    - Embedding model and dimensions based on provider

    Args:
        defaults: Default configuration dictionary (modified in-place)
        env_reader: Environment variable reader for overrides

    Raises:
        ImportError: If required dependencies for embeddings are missing
    """
    llm_cfg = defaults.get("llm")
    if not isinstance(llm_cfg, LLMConfig):
        return

    provider_prefix, base_model, _ = split_litellm_model_id(llm_cfg.model_id)
    if not base_model:
        return

    # Use LiteLLM's model database to get max_output_tokens for this model
    # This handles all providers and updates automatically with LiteLLM
    try:
        # Query LiteLLM's model database for max output tokens
        model_max_tokens = litellm.get_max_tokens(base_model)

        if model_max_tokens and llm_cfg.max_tokens > model_max_tokens:
            logger.info(
                "Capping max_tokens from %d to %d for model '%s' (model limit from LiteLLM database)",
                llm_cfg.max_tokens,
                model_max_tokens,
                llm_cfg.model_id,
            )
            llm_cfg.max_tokens = model_max_tokens
            llm_cfg.parameters["max_tokens"] = model_max_tokens
        elif model_max_tokens:
            logger.debug(
                "Model '%s' max_tokens=%d is within limit (model max: %d)",
                llm_cfg.model_id,
                llm_cfg.max_tokens,
                model_max_tokens,
            )
        else:
            logger.debug(
                "Model '%s' not in LiteLLM database, using configured max_tokens=%d",
                llm_cfg.model_id,
                llm_cfg.max_tokens,
            )
    except Exception as e:
        # If LiteLLM doesn't know about this model, log and continue
        # The API call will fail with a clear error if max_tokens is invalid
        logger.debug(
            "Could not query max_tokens for model '%s': %s (will use configured value)",
            llm_cfg.model_id,
            str(e),
        )

    embed_override = env_reader.get("CYBER_AGENT_EMBEDDING_MODEL")

    # Align all LLM configs to the main LLM model
    for key in ("memory_llm", "evaluation_llm", "swarm_llm"):
        cfg = defaults.get(key)
        if isinstance(cfg, MemoryLLMConfig):
            cfg.model_id = "ollama/llama3.2:3b" if embed_override and embed_override.startswith("ollama/") else llm_cfg.model_id
            cfg.provider = ModelProvider.LITELLM
            cfg.parameters["temperature"] = cfg.temperature
            cfg.parameters["max_tokens"] = cfg.max_tokens
        elif isinstance(cfg, LLMConfig):
            cfg.model_id = llm_cfg.model_id
            cfg.provider = ModelProvider.LITELLM
            # Align swarm output cap to primary llm to avoid premature max_tokens stops
            if key == "swarm_llm":
                cfg.max_tokens = llm_cfg.max_tokens
            cfg.parameters["temperature"] = cfg.temperature
            cfg.parameters["max_tokens"] = cfg.max_tokens

    # Configure embedding model
    embed_cfg = defaults.get("embedding")
    if isinstance(embed_cfg, EmbeddingConfig):
        if embed_override:
            embed_model = embed_override
            dims = EMBEDDING_DIMENSIONS.get(embed_model)
            if dims is None:
                logger.warning(
                    "Unknown embedding model '%s', dimensions not in lookup table. "
                    "Attempting to infer from model name or defaulting to 1536.",
                    embed_model,
                )
                # Infer dimensions from model name
                if "3-large" in embed_model:
                    dims = 3072
                elif "ada-002" in embed_model or "3-small" in embed_model:
                    dims = 1536
                elif "text-embedding-004" in embed_model:
                    dims = 768
                elif "MiniLM" in embed_model:
                    dims = 384
                elif "titan" in embed_model and "v2" in embed_model:
                    dims = 1024
                elif "mxbai-embed-large" in embed_model:
                    dims = 1024
                else:
                    dims = 1536
                    logger.warning(
                        "Could not infer dimensions for '%s', defaulting to 1536. "
                        "If this is incorrect, Qdrant will reject generated vectors.",
                        embed_model,
                    )
        else:
            # Use provider-specific embedding defaults
            embed_model, dims = LITELLM_EMBEDDING_DEFAULTS.get(
                provider_prefix, DEFAULT_LITELLM_EMBEDDING
            )

        # Check required dependencies for specific embedding models
        if embed_model == "models/text-embedding-004":
            if importlib.util.find_spec("google.genai") is None:
                logger.error(
                    "LiteLLM provider '%s' requires optional dependency 'google-genai'. "
                    "Install it or set CYBER_AGENT_EMBEDDING_MODEL to a supported embedding.",
                    provider_prefix,
                )
                raise ImportError("google-genai is required for Gemini embeddings")
        elif embed_model == "multi-qa-MiniLM-L6-cos-v1":
            if importlib.util.find_spec("sentence_transformers") is None:
                logger.error(
                    "LiteLLM provider '%s' requires optional dependency 'sentence-transformers'. "
                    "Install it or set CYBER_AGENT_EMBEDDING_MODEL to a supported embedding.",
                    provider_prefix,
                )
                raise ImportError(
                    "sentence-transformers is required for Hugging Face embeddings"
                )

        # Update embedding configuration
        embed_cfg.model_id = embed_model
        embed_cfg.dimensions = dims
        embed_cfg.provider = ModelProvider.LITELLM
        embed_cfg.parameters["dimensions"] = dims
