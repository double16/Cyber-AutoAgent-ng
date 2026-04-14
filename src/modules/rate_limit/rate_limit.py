#!/usr/bin/env python3
"""
Patches Model subclasses to enforce rate limiting on the client side to prevent the provider rate limit from stopping
the operation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
import time
import threading
import json

from typing import Any, Optional, Type, TypeVar, Callable, Dict, List

from modules.config.models.factory import get_model_id_from_model
from modules.config.types import RateLimitConfig
from modules.handlers.conversation_budget import estimate_prompt_tokens

T = TypeVar("T")

logger = logging.getLogger("RateLimit")


class _RetryableError(Exception):
    """Internal exception to trigger retry logic in patches."""

    def __init__(self, code: int) -> None:
        self.code = code


# ----------------------------
# Thread-safe token buckets
# ----------------------------

class _TokenBucket:
    """
    Thread-safe token bucket.
    - capacity: max tokens in bucket
    - refill_rate_per_sec: tokens added per second
    """

    def __init__(self, capacity: float, refill_rate_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate_per_sec)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = now

    def consume_blocking(self, amount: float) -> None:
        amount = float(amount)
        if amount <= 0:
            return
        if amount > self.capacity:
            logger.warning("Requested amount %f exceeds capacity %f", amount, self.capacity)
            # limit amount or we will wait forever
            amount = self.capacity

        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= amount:
                    self._tokens -= amount
                    return

                # Need to wait for more tokens
                needed = amount - self._tokens
                logger.debug("Need to refill %f", needed)
                # Avoid division by zero
                wait_s = needed / self.refill_rate if self.refill_rate > 0 else 0.5

            # Sleep outside lock
            sleep_time = min(120.0, max(wait_s, 0.1))
            # TODO: use an EventEmitter object
            rate_limit_event = {
                "type": "rate_limit",
                "timestamp": datetime.now().isoformat(),
                "needed": needed,
                "wait_total": wait_s,
                "sleep_time": sleep_time,
                "message": f"Sleeping for {sleep_time:.1f} seconds",
            }
            print(f"__CYBER_EVENT__{json.dumps(rate_limit_event)}__CYBER_EVENT_END__")
            time.sleep(sleep_time)


class ThreadSafeRateLimiter:
    """
    A process-wide limiter that works even if the caller creates a fresh asyncio loop per call
    (because it's based on threading primitives + blocking sleeps).
    """

    def __init__(self, cfg: RateLimitConfig) -> None:
        self.cfg = cfg
        # capacity = rpm, refill rate = rpm / 60 tokens per second
        self._req_bucket = _TokenBucket(cfg.rpm, cfg.rpm / 60.0) if cfg.rpm else None
        self._tok_bucket = _TokenBucket(cfg.tpm, cfg.tpm / 60.0) if cfg.tpm else None
        self._max_concurrent = cfg.max_concurrent
        self._sem = threading.BoundedSemaphore(cfg.max_concurrent) if cfg.max_concurrent else None

        # Cooldown state
        self._cooldown_active = False
        self._last_error_time = 0.0
        self._cooldown_sem = threading.Semaphore(1)
        self._lock = threading.Lock()

    def acquire_blocking(self, token_cost: int) -> Callable[[], None]:
        """
        Blocks until:
        - a concurrency slot is available
        - request budget allows 1 request
        - token budget allows token_cost
        Returns a release() callable.
        """
        # 1. Check cooldown
        in_cooldown = False
        with self._lock:
            if self._cooldown_active:
                now = time.monotonic()
                if now - self._last_error_time > self.cfg.cooldown_period:
                    logger.info("Rate limit: Cooldown period expired, resuming normal operation")
                    self._cooldown_active = False
                else:
                    in_cooldown = True

        # 2. Acquire concurrency slots
        if in_cooldown:
            self._cooldown_sem.acquire()

        if self._sem:
            self._sem.acquire()

        # 3. Consume buckets
        try:
            if self._req_bucket:
                self._req_bucket.consume_blocking(1.0)
            if self._tok_bucket and token_cost > 0:
                self._tok_bucket.consume_blocking(float(token_cost))
        except Exception:
            if self._sem:
                self._sem.release()
            if in_cooldown:
                self._cooldown_sem.release()
            raise

        def release() -> None:
            if self._sem:
                self._sem.release()
            if in_cooldown:
                self._cooldown_sem.release()

        return release

    def report_error(self, code) -> bool:
        """
        Reports an HTTP error code. If it's a retryable code,
        triggers cooldown mode and returns True.
        """
        if not code:
            return False
        try:
            code = int(code)
        except ValueError:
            return False
        if code in self.cfg.retry_codes:
            with self._lock:
                if not self._cooldown_active:
                    logger.warning("Rate limit: Received %d, entering cooldown mode (concurrency=1)", code)
                self._cooldown_active = True
                self._last_error_time = time.monotonic()
            return True
        return False

    async def ahandle_exception(self, e: Exception, attempt: int):
        await asyncio.sleep(self._handle_exception(e, attempt))

    def handle_exception(self, e: Exception, attempt: int):
        time.sleep(self._handle_exception(e, attempt))

    def _handle_exception(self, e: Exception, attempt: int) -> float:
        """
        Handle exceptions from the event loop. Raises an exception unless retry is acceptable.
        :param e: the exception
        :param attempt: attempt number, 0 based
        :return: Delay time in seconds
        """
        assert e is not None

        is_retryable = False
        if isinstance(e, _RetryableError):
            is_retryable = True
            code = e.code
            # Don't call self.report_error a second time
        else:
            code = getattr(e, "status_code", None)
            if not code and hasattr(e, "response") and hasattr(e.response, "status_code"):  # type: ignore
                code = e.response.status_code  # type: ignore
            if code and self.report_error(code):
                is_retryable = True

        if attempt >= self.cfg.max_retries:
            logger.error("Rate limit: Max retries reached for code %d", code)
            is_retryable = False

        if not is_retryable:
            if isinstance(e, _RetryableError):
                raise RuntimeError(f"Response error {code}")
            raise e

        delay = min(self.cfg.retry_max_delay, self.cfg.retry_base_delay * (2 ** attempt))
        # TODO: use an EventEmitter object
        rate_limit_event = {
            "type": "rate_limit",
            "timestamp": datetime.now().isoformat(),
            "needed": delay,
            "wait_total": delay,
            "sleep_time": delay,
            "message": f"Cool down for {delay:.1f} seconds ({code} {attempt + 1}/{self.cfg.max_retries})",
        }
        print(f"__CYBER_EVENT__{json.dumps(rate_limit_event)}__CYBER_EVENT_END__")
        return delay


def _batch_messages_to_strands_messages(
        batch_messages: Any,
) -> List[Dict[str, Any]]:
    """
    LangChain ChatModel.generate/agenerate signature typically uses:
      generate(messages: list[list[BaseMessage]], ...)
      agenerate(messages: list[list[BaseMessage]], ...)
    """
    # Accept a single conversation list as a convenience.
    # If user passes list[BaseMessage], treat as one item batch.
    if isinstance(batch_messages, list) and batch_messages and not isinstance(batch_messages[0], list):
        batch = [batch_messages]
    else:
        batch = batch_messages or []

    messages = []
    if isinstance(batch, list):
        for conv in batch:
            if not isinstance(conv, list):
                continue
            for msg in conv:
                strands_msg = {}
                content = getattr(msg, "content", None)
                if content is not None:
                    strands_msg["content"] = content

                # BaseMessage-like: additional kwargs (tool calls, function call, etc)
                ak = getattr(msg, "additional_kwargs", None)
                if isinstance(ak, dict) and ak:
                    strands_msg["json"] = ak

                messages.append(strands_msg)

    return messages


# ----------------------------
# Strands Class patching
# ----------------------------

_ORIG_STREAM_ATTR = "_rl_orig_stream"
_ORIG_STRUCT_ATTR = "_rl_orig_structured_output"


def patch_model_provider_class(model_cls: Type[Any], limiter: ThreadSafeRateLimiter) -> None:
    """
    Monkey-patches model_cls.stream and model_cls.structured_output (if present),
    preserving originals on the class.

    Patch the *concrete provider classes* you use (GeminiModel, BedrockModel, LiteLLMModel, OllamaModel, ...).
    """
    if not hasattr(model_cls, "stream"):
        logger.warning(f"Rate limit: {model_cls} has no stream() to patch")
        return

    if not hasattr(model_cls, _ORIG_STREAM_ATTR):
        logger.info("Rate limit: Applying Strands rate limit to %s: %s", model_cls.__name__, str(limiter.cfg))
        setattr(model_cls, _ORIG_STREAM_ATTR, model_cls.stream)

    orig_stream = getattr(model_cls, _ORIG_STREAM_ATTR)

    async def stream(
            self,
            messages,
            tool_specs=None,
            system_prompt: Optional[str] = None,
            *,
            tool_choice=None,
            system_prompt_content=None,
            **kwargs: Any,
    ):
        token_cost = estimate_prompt_tokens(
            model_id=get_model_id_from_model(self),
            messages=messages,
            system_prompt=system_prompt,
            tool_specs=tool_specs,
        )
        token_cost += limiter.cfg.assume_output_tokens

        for attempt in range(limiter.cfg.max_retries + 1):
            release = await asyncio.to_thread(limiter.acquire_blocking, token_cost)
            try:
                async for event in orig_stream(
                        self,
                        messages,
                        tool_specs,
                        system_prompt,
                        tool_choice=tool_choice,
                        system_prompt_content=system_prompt_content,
                        **kwargs,
                ):
                    # Check for 429/503 error event
                    # Strands models typically yield events as dicts or objects
                    # We need to detect if any of them represent an HTTP error
                    if isinstance(event, dict) and event.get("type") == "error":
                        code = event.get("code")
                        if code and limiter.report_error(code):
                            # It's a retryable error
                            raise _RetryableError(code)
                    yield event
                return  # Success
            except Exception as e:
                await limiter.ahandle_exception(e, attempt)
            finally:
                release()

    model_cls.stream = stream  # type: ignore[assignment]

    # structured_output is optional on some providers, but common in Strands
    if hasattr(model_cls, "structured_output"):
        if not hasattr(model_cls, _ORIG_STRUCT_ATTR):
            setattr(model_cls, _ORIG_STRUCT_ATTR, model_cls.structured_output)

        orig_struct = getattr(model_cls, _ORIG_STRUCT_ATTR)

        async def structured_output(
                self,
                output_model: Type[T],
                prompt,
                system_prompt: Optional[str] = None,
                **kwargs: Any,
        ):
            token_cost = estimate_prompt_tokens(
                model_id=get_model_id_from_model(self),
                extra_content=prompt,
                system_prompt=system_prompt,
            )
            token_cost += limiter.cfg.assume_output_tokens

            for attempt in range(limiter.cfg.max_retries + 1):
                release = await asyncio.to_thread(limiter.acquire_blocking, token_cost)
                try:
                    async for event in orig_struct(self, output_model, prompt, system_prompt=system_prompt, **kwargs):
                        if isinstance(event, dict) and event.get("type") == "error":
                            code = event.get("code")
                            if code and limiter.report_error(code):
                                raise _RetryableError(code)
                        yield event
                    return
                except Exception as e:
                    await limiter.ahandle_exception(e, attempt)
                finally:
                    release()

        model_cls.structured_output = structured_output  # type: ignore[assignment]


def unpatch_model_provider_class(model_cls: Type[Any]) -> None:
    if hasattr(model_cls, _ORIG_STREAM_ATTR):
        model_cls.stream = getattr(model_cls, _ORIG_STREAM_ATTR)  # type: ignore[assignment]
        delattr(model_cls, _ORIG_STREAM_ATTR)

    if hasattr(model_cls, _ORIG_STRUCT_ATTR):
        model_cls.structured_output = getattr(model_cls, _ORIG_STRUCT_ATTR)  # type: ignore[assignment]
        delattr(model_cls, _ORIG_STRUCT_ATTR)


# ----------------------------
# Langchain Class patching
# ----------------------------

_ORIG_GENERATE_ATTR = "_rl_orig_generate"
_ORIG_AGENERATE_ATTR = "_rl_orig_agenerate"


def patch_langchain_chat_class_generate(model_cls: Type[Any], limiter: ThreadSafeRateLimiter) -> None:
    """
    Monkey-patch LangChain chat model classes (ChatLiteLLM, ChatOllama, ChatBedrock, etc.)
    at the CLASS level, rate-limiting generate/agenerate.
    """

    # ---- generate (sync) ----
    if hasattr(model_cls, "generate") and callable(getattr(model_cls, "generate")):
        if not hasattr(model_cls, _ORIG_GENERATE_ATTR):
            logger.info(
                "Rate limit: Applying LangChain generate rate limit to %s: %s",
                model_cls.__name__,
                str(limiter.cfg),
            )
            setattr(model_cls, _ORIG_GENERATE_ATTR, model_cls.generate)

        orig_generate = getattr(model_cls, _ORIG_GENERATE_ATTR)

        def generate(self, messages, *args: Any, **kwargs: Any) -> Any:
            token_cost = estimate_prompt_tokens(
                model_id=get_model_id_from_model(self),
                messages=_batch_messages_to_strands_messages(messages),
            )
            token_cost += limiter.cfg.assume_output_tokens

            for attempt in range(limiter.cfg.max_retries + 1):
                release = limiter.acquire_blocking(token_cost)
                try:
                    return orig_generate(self, messages, *args, **kwargs)
                except Exception as e:
                    limiter.handle_exception(e, attempt)
                finally:
                    release()

        model_cls.generate = generate  # type: ignore[assignment]
    else:
        logger.warning("Rate limit: %s has no generate() to patch", model_cls)

    # ---- agenerate (async) ----
    if hasattr(model_cls, "agenerate") and callable(getattr(model_cls, "agenerate")):
        if not hasattr(model_cls, _ORIG_AGENERATE_ATTR):
            logger.info(
                "Rate limit: Applying LangChain agenerate rate limit to %s: %s",
                model_cls.__name__,
                str(limiter.cfg),
            )
            setattr(model_cls, _ORIG_AGENERATE_ATTR, model_cls.agenerate)

        orig_agenerate = getattr(model_cls, _ORIG_AGENERATE_ATTR)

        async def agenerate(self, messages, *args: Any, **kwargs: Any) -> Any:
            token_cost = estimate_prompt_tokens(
                model_id=get_model_id_from_model(self),
                messages=_batch_messages_to_strands_messages(messages),
            )
            token_cost += limiter.cfg.assume_output_tokens

            for attempt in range(limiter.cfg.max_retries + 1):
                release = await asyncio.to_thread(limiter.acquire_blocking, token_cost)
                try:
                    result = orig_agenerate(self, messages, *args, **kwargs)
                    if asyncio.iscoroutine(result):
                        return await result
                    return result
                except Exception as e:
                    await limiter.ahandle_exception(e, attempt)
                finally:
                    release()

        model_cls.agenerate = agenerate  # type: ignore[assignment]
    else:
        logger.warning("Rate limit: %s has no agenerate() to patch", model_cls)


def unpatch_langchain_chat_class_generate(model_cls: Type[Any]) -> None:
    if hasattr(model_cls, _ORIG_GENERATE_ATTR):
        model_cls.generate = getattr(model_cls, _ORIG_GENERATE_ATTR)  # type: ignore[assignment]
        delattr(model_cls, _ORIG_GENERATE_ATTR)

    if hasattr(model_cls, _ORIG_AGENERATE_ATTR):
        model_cls.agenerate = getattr(model_cls, _ORIG_AGENERATE_ATTR)  # type: ignore[assignment]
        delattr(model_cls, _ORIG_AGENERATE_ATTR)
