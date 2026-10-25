"""Budget final LangChain requests, including prompts assembled inside Ragas."""

import inspect
import json
import logging
from copy import copy
from types import MethodType
from typing import Any

from modules.config.models.generation_budget import GenerationRequest, allocate_generation, schema_answer_tokens

logger = logging.getLogger(__name__)


def attach_evaluation_budget(
    model: Any, provider: str, model_id: str, context_tokens: int,
    preferred_tokens: int, ceiling: int | None,
) -> None:
    """Install per-instance transport adapters without changing shared model configuration."""
    methods = {
        name: getattr(type(model), name, None)
        for name in ("_generate", "_agenerate", "_stream", "_astream")
    }

    def prepare(messages, kwargs):
        from modules.handlers.conversation_budget import estimate_prompt_tokens

        local = copy(model)
        for name, method in methods.items():
            if method is not None:
                object.__setattr__(local, name, MethodType(method, local))
        # Count the final library prompt plus native schemas and tool definitions.
        specs = {key: kwargs[key] for key in ("tools", "functions", "response_format", "format") if key in kwargs}
        serialized = [{"role": message.type, "content": [{"text": str(message.content)}]} for message in messages]
        estimated = estimate_prompt_tokens(model_id, serialized, extra_content=json.dumps(specs, default=str))
        preferred = kwargs.get("max_tokens", kwargs.get("max_output_tokens", preferred_tokens))
        preferred = preferred if isinstance(preferred, int) and preferred > 0 else preferred_tokens
        answer = min(1024, preferred)
        schemas = []
        for tool in specs.get("tools", []):
            schemas.append(tool.get("function", {}).get("parameters", {}))
        schemas.extend(item.get("parameters", {}) for item in specs.get("functions", []))
        if isinstance(specs.get("format"), dict):
            schemas.append(specs["format"])
        response_format = specs.get("response_format", {})
        if isinstance(response_format, dict):
            schemas.append(response_format.get("json_schema", {}).get("schema", {}))
        for schema in schemas:
            answer = max(answer, schema_answer_tokens(schema, model_id))
        allocation = allocate_generation(GenerationRequest(context_tokens, estimated, preferred, answer,
                                                           output_ceiling=ceiling))
        tokens = allocation.generation_tokens
        kwargs = dict(kwargs)
        kwargs.pop("max_tokens", None)
        kwargs.pop("max_output_tokens", None)
        if provider == "ollama":
            object.__setattr__(local, "num_predict", tokens)
            object.__setattr__(local, "num_ctx", context_tokens)
            if "options" in kwargs:
                kwargs["options"] = {**kwargs["options"], "num_predict": tokens, "num_ctx": context_tokens}
        elif provider == "gemini":
            kwargs["max_output_tokens"] = tokens
        else:
            kwargs["max_tokens"] = tokens
        logger.info("Evaluation generation allocation: model=%s allocation=%s", model_id, allocation)
        return local, kwargs

    def wrap(method):
        if inspect.isasyncgenfunction(method):
            async def astream(messages, *args, **kwargs):
                local, parameters = prepare(messages, kwargs)
                async for event in method(local, messages, *args, **parameters):
                    yield event
            return astream
        if inspect.iscoroutinefunction(method):
            async def ainvoke(messages, *args, **kwargs):
                local, parameters = prepare(messages, kwargs)
                return await method(local, messages, *args, **parameters)
            return ainvoke
        if inspect.isgeneratorfunction(method):
            def stream(messages, *args, **kwargs):
                local, parameters = prepare(messages, kwargs)
                yield from method(local, messages, *args, **parameters)
            return stream

        def invoke(messages, *args, **kwargs):
            local, parameters = prepare(messages, kwargs)
            return method(local, messages, *args, **parameters)
        return invoke

    for name, method in methods.items():
        if method is not None:
            object.__setattr__(model, name, wrap(method))
