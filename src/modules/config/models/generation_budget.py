"""Deterministic, request-local generation allocation shared by model transports."""

import inspect
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from types import MethodType
from typing import Any

from strands.types.exceptions import ContextWindowOverflowException

logger = logging.getLogger(__name__)
_SUMMARIZING: ContextVar[bool] = ContextVar("generation_summarizing", default=False)


@contextmanager
def summary_generation_budget() -> Iterator[None]:
    """Isolate SDK summary output from the parent task's structured-answer floor."""
    token = _SUMMARIZING.set(True)
    try:
        yield
    finally:
        _SUMMARIZING.reset(token)


def is_summary_generation() -> bool:
    """Identify SDK compaction calls that must retain their ordinary text transport."""
    return _SUMMARIZING.get()


@dataclass(frozen=True)
class GenerationRequest:
    """Token counts for one fully assembled request; limits are positive when known."""

    context_tokens: int
    input_tokens: int
    preferred_tokens: int
    answer_tokens: int
    reasoning_tokens: int = 0
    output_ceiling: int | None = None

    @property
    def safety_tokens(self) -> int:
        return max(512, self.context_tokens // 50)

    @property
    def minimum_tokens(self) -> int:
        return self.answer_tokens + self.reasoning_tokens


@dataclass(frozen=True)
class GenerationAllocation:
    """The allocation and its explanation, safe to include in diagnostics."""

    generation_tokens: int
    minimum_tokens: int
    answer_tokens: int
    reasoning_tokens: int
    safety_tokens: int
    preferred_tokens: int
    input_tokens: int
    context_tokens: int
    output_ceiling: int | None
    reason: str


def allocate_generation(request: GenerationRequest) -> GenerationAllocation:
    """Fit a complete answer before spending additional capacity on generation."""
    if (min(request.context_tokens, request.preferred_tokens, request.answer_tokens) <= 0
            or min(request.input_tokens, request.reasoning_tokens) < 0
            or (request.output_ceiling is not None and request.output_ceiling <= 0)):
        raise ValueError("Generation token counts must be positive; input and reasoning may be zero")
    available = request.context_tokens - request.safety_tokens - request.input_tokens
    cap = min(available, request.output_ceiling) if request.output_ceiling is not None else available
    if cap < request.minimum_tokens:
        raise ContextWindowOverflowException(
            f"Required generation minimum {request.minimum_tokens} cannot fit: available={available}, "
            f"output_ceiling={request.output_ceiling}, input={request.input_tokens}, "
            f"context={request.context_tokens}, safety={request.safety_tokens}"
        )
    target = max(request.preferred_tokens, request.minimum_tokens)
    allocated = min(target, cap)
    reason = "capacity_limited" if allocated < target else (
        "required_answer" if target > request.preferred_tokens else "preferred_target"
    )
    return GenerationAllocation(
        allocated, request.minimum_tokens, request.answer_tokens, request.reasoning_tokens,
        request.safety_tokens, request.preferred_tokens,
        request.input_tokens, request.context_tokens, request.output_ceiling, reason,
    )


def schema_answer_tokens(schema: dict[str, Any], model_id: str) -> int:
    """Estimate required serialized records independently of input inventory cardinality."""
    from modules.agents.review_contracts import required_review_answer_tokens

    return required_review_answer_tokens(schema, model_id)


def _reasoning_tokens(config: dict[str, Any], preferred: int) -> int:
    """Reserve explicit thinking budgets or a bounded allowance for effort-based reasoning."""
    fields = config.get("additional_request_fields") or config.get("additional_args") or {}
    thinking = fields.get("thinking") or (config.get("params") or {}).get("thinking_config") or {}
    budget = thinking.get("budget_tokens", thinking.get("thinking_budget", 0))
    if isinstance(budget, int) and budget > 0:
        return budget
    effort = (fields.get("reasoning_effort") or fields.get("think")
              or fields.get("output_config", {}).get("effort"))
    if effort and effort != "none":
        return min(1024, preferred // 4)
    return 0


def model_generation_request(
    model: Any, input_tokens: int, *, answer_tokens: int = 0,
) -> GenerationRequest:
    """Resolve a stable target and current transport reasoning configuration."""
    from modules.config.models.invocation import (
        invocation_answer_tokens,
        invocation_minimum_output_tokens,
        invocation_output_tokens,
    )

    preferred = invocation_output_tokens(model._cyber_llm_role) or model._cyber_preferred_output_tokens
    if _SUMMARIZING.get():
        preferred = min(4096, preferred)
    floor = max(min(1024, preferred), answer_tokens)
    if not _SUMMARIZING.get():
        floor = max(floor, invocation_answer_tokens(model._cyber_llm_role)
                    or getattr(model, "_cyber_answer_tokens", 0) or 0)
    reasoning = _reasoning_tokens({**model.config, "additional_args": {
        **getattr(model, "client_args", {}), **(model.config.get("params") or {}),
    }}
                                 if model._cyber_provider == "litellm" else model.config, preferred)
    explicit_minimum = (invocation_minimum_output_tokens(model._cyber_llm_role)
                        or getattr(model, "_cyber_minimum_output_tokens", None))
    if explicit_minimum is not None and not _SUMMARIZING.get():
        floor = max(floor, explicit_minimum - reasoning)
    return GenerationRequest(
        model.config["context_window_limit"], input_tokens, preferred, floor, reasoning,
        model._cyber_output_ceiling,
    )


def set_generation_tokens(model: Any, tokens: int) -> None:
    """Keep the provider wire setting and controller reservation synchronized."""
    if getattr(model, "_cyber_provider", None) == "gemini":
        model.update_config(params={**model.config.get("params", {}), "max_output_tokens": tokens})
    elif getattr(model, "_cyber_provider", None) == "litellm":
        params = dict(model.config.get("params") or {})
        key = "max_completion_tokens" if "max_completion_tokens" in params else "max_tokens"
        params.pop("max_tokens", None)
        params[key] = tokens
        model.update_config(params=params)
    else:
        model.update_config(max_tokens=tokens)
    model._output_tokens = tokens


def prepare_generation_model(
    model: Any, model_id: str, messages: list[dict], system_prompt: Any = None,
    tool_specs: list[dict] | None = None, schema: dict | None = None,
) -> tuple[Any, list[dict]]:
    """Allocate against final transport input on a copy, including direct SDK summaries."""
    from modules.config.models.capabilities import allows_reasoning_content_replay
    from modules.handlers.conversation_budget import estimate_prompt_tokens

    local = copy(model)
    local.config = deepcopy(model.config)
    if model._cyber_provider == "litellm":
        local.client_args = deepcopy(model.client_args)
    if not allows_reasoning_content_replay(model._cyber_provider, model_id):
        messages = [
            {**message, "content": [block for block in message.get("content", []) if "reasoningContent" not in block]}
            for message in messages
        ]
    specs = [*(tool_specs or []), *([{"output_schema": schema}] if schema is not None else [])]
    estimated = estimate_prompt_tokens(model_id, messages, system_prompt, specs)
    answer = schema_answer_tokens(schema, model_id) if schema is not None else 0
    allocation = allocate_generation(model_generation_request(model, estimated, answer_tokens=answer))
    set_generation_tokens(local, allocation.generation_tokens)
    logger.info("Generation allocation: role=%s model=%s %s", model._cyber_llm_role, model_id, asdict(allocation))
    # A native structured-output method can call stream internally. It must use this copy.
    local.stream = MethodType(type(model).stream, local)
    if (model._cyber_provider == "litellm"
            and callable(getattr(type(model), "_structured_output_using_response_schema", None))):
        local._structured_output_using_response_schema = MethodType(_litellm_schema_output, local)
    return local, messages


async def _litellm_schema_output(model: Any, output_model: Any, prompt: Any, system_prompt: Any = None) -> Any:
    """The SDK native-schema path forwards client_args but omits config.params."""
    native = copy(model)
    params = {key: value for key, value in model.config.get("params", {}).items()
              if key not in {"model", "messages", "response_format", "stream"}}
    native.client_args = {**model.client_args, **params, "stream": False}
    return await type(model)._structured_output_using_response_schema(native, output_model, prompt, system_prompt)


async def _native_schema_events(model: Any, output_model: Any, messages: Any, system_prompt: Any):
    """Use SDK schema-capable transports while retaining their usage and stop metadata."""
    from strands.event_loop.streaming import process_stream
    from strands.types.exceptions import MaxTokensReachedException

    params = dict(model.config.get("params") or {})
    if model._cyber_provider == "litellm":
        # LiteLLM converts the Pydantic class to the provider's native schema wire format.
        params["response_format"] = output_model
    else:
        params.update(response_mime_type="application/json", response_schema=output_model.model_json_schema())
    model.update_config(params=params)
    last = None
    async for event in process_stream(model.stream(messages, system_prompt=system_prompt)):
        if "stop" in event:
            last = event["stop"]
    if last is None:
        raise ValueError("Native schema transport returned no completion")
    stop_reason, message, usage, metrics = last
    yield {"metadata": {"usage": usage, "metrics": metrics}}
    if stop_reason == "max_tokens":
        raise MaxTokensReachedException("Native structured generation reached its output limit")
    content = "".join(block.get("text", "") for block in message["content"])
    yield {"output": output_model.model_validate_json(content)}


def attach_generation_budget(model: Any, provider: str, model_id: str, ceiling: int | None) -> Any:
    """Cover both SDK model entrypoints, including calls bypassing agent hooks."""
    model._cyber_provider = provider
    model._cyber_native_structured_output = provider in {"ollama", "litellm", "gemini"}
    model._cyber_preferred_output_tokens = model._output_tokens
    model._cyber_output_ceiling = ceiling
    from modules.config.models.invocation import invocation_answer_tokens, invocation_minimum_output_tokens

    model._cyber_minimum_output_tokens = invocation_minimum_output_tokens(model._cyber_llm_role)
    model._cyber_answer_tokens = invocation_answer_tokens(model._cyber_llm_role)
    for name in ("stream", "structured_output"):
        original = getattr(type(model), name, None)
        if original is None:
            continue
        signature = inspect.signature(original)

        def wrap(method, parameters, name_of_method):
            async def invoke(*args, **kwargs):
                bound = parameters.bind(model, *args, **kwargs)
                values = bound.arguments
                messages_key = "messages" if "messages" in values else "prompt"
                output_model = values.get("output_model")
                schema = output_model.model_json_schema() if output_model is not None else None
                local, messages = prepare_generation_model(
                    model, model_id, values[messages_key],
                    values.get("system_prompt_content") or values.get("system_prompt"),
                    values.get("tool_specs"), schema,
                )
                values["self"] = local
                values[messages_key] = messages
                if (name_of_method == "structured_output" and model._cyber_provider in {"litellm", "gemini"}
                        and getattr(model, "_cyber_native_schema", False)):
                    async for event in _native_schema_events(local, output_model, messages, values.get("system_prompt")):
                        yield event
                    return
                async for event in method(*bound.args, **bound.kwargs):
                    yield event
            return invoke

        setattr(model, name, wrap(original, signature, name))
    return model
