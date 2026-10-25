"""Native provider output inside the Strands lifecycle, after bounded investigation."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ValidationError
from strands.models.model import Model
from strands.tools.registry import ToolRegistry

from modules.agents.structured_outputs import StructuredOutputUnavailableError
from modules.config.models.generation_budget import is_summary_generation

logger = logging.getLogger(__name__)
_UNSUPPORTED_NATIVE: set[tuple[str, str, str]] = set()

INVESTIGATION_GUIDANCE = (
    "\n\n## Investigation handoff\n"
    "This pass gathers evidence for a separate structured final decision. Use only the supplied "
    "investigation tools as needed, then end your turn with concise evidence notes. Do not call an "
    "output-schema tool or serialize the final decision here. The controller will supply the output "
    "schema to a tool-free finalization call. Existing task scope and evidence limits still apply."
)
FINALIZATION_GUIDANCE = (
    "\n\n## Structured finalization\n"
    "Investigation is closed. Use the supplied task, evidence, controller observations, and correction "
    "feedback to return the final decision matching the response schema. No investigation or "
    "publication tools are available in this pass. Do not invent missing evidence."
)


def native_unavailable(error: BaseException) -> bool:
    """Recognize explicit transport unavailability without confusing it with invalid model data."""
    if isinstance(error, (NotImplementedError, StructuredOutputUnavailableError)):
        return True
    message = str(error).lower()
    if any(marker in message for marker in (
        "invalid schema", "schema keyword", "schema property", "unsupported schema", "schema type",
        "additionalproperties", "maxitems", "minitems", "anyof", "oneof", "$ref",
    )):
        return False
    return (
        any(word in message for word in ("not supported", "unsupported", "not implemented"))
        and any(word in message for word in ("response_format", "json_schema", "structured output", "format"))
        and not any(word in message for word in ("context", "token", "authentication", "credential"))
    )


class StructuredPayload(dict):
    """Canonical output plus controller-only evidence for subsequent consistency correction."""

    def __init__(self, data: dict[str, Any], messages: list[dict[str, Any]]) -> None:
        super().__init__(data)
        self.finalization_messages = deepcopy(messages)


class NativeFinalizer(Model):
    """Bridge validated provider results into SDK events; never expose investigation tools."""

    def __init__(self, model: Model, output_model: type[BaseModel]) -> None:
        self.model = model
        self.output_model = output_model
        self.schema_id = hashlib.sha256(json.dumps(output_model.model_json_schema(), sort_keys=True).encode()).hexdigest()
        provider = getattr(model, "_cyber_provider", "")
        config = model.get_config()
        endpoint = str(getattr(model, "host", "")) or str(
            getattr(model, "client_args", {}).get("api_base", "")
        )
        self.capability_key = (provider, endpoint, str(config.get("model_id", "")))
        self.native = (getattr(model, "_cyber_native_structured_output", False)
                       and self.capability_key not in _UNSUPPORTED_NATIVE)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.model, name)

    def get_config(self) -> Any:
        return self.model.get_config()

    def update_config(self, **model_config: Any) -> None:
        self.model.update_config(**model_config)

    async def structured_output(self, *args: Any, **kwargs: Any) -> AsyncGenerator:
        async for event in self.model.structured_output(*args, **kwargs):
            yield event

    async def stream(
        self, messages, tool_specs=None, system_prompt=None, tool_choice=None, **kwargs,
    ) -> AsyncGenerator:
        # The SDK creates exactly one output tool for this finalization. Its Python-validated
        # arguments are constructed locally after native decoding, never generated as tool syntax.
        specs = [spec for spec in tool_specs or [] if spec["name"] == self.output_model.__name__]
        if not specs and is_summary_generation():
            async for event in self.model.stream(messages, system_prompt=system_prompt, **kwargs):
                yield event
            return
        if len(specs) != 1:
            raise ValueError("Native finalization requires exactly its declared output schema")
        if self.native:
            try:
                output = None
                metadata = []
                async for event in self.model.structured_output(
                    self.output_model, messages, system_prompt=system_prompt,
                ):
                    if "output" in event:
                        output = self.output_model.model_validate(event["output"])
                    elif "metadata" in event:
                        metadata.append(event)
                if output is None:
                    raise ValueError("Native structured generation returned no output")
            except Exception as error:
                if not native_unavailable(error):
                    logger.info("Structured finalization mode=native schema=%s outcome=%s",
                                self.schema_id, type(error).__name__)
                    raise
                self.native = False
                _UNSUPPORTED_NATIVE.add(self.capability_key)
                logger.info("Structured finalization mode=tool schema=%s fallback=native_unsupported",
                            self.schema_id)
            else:
                logger.info("Structured finalization mode=native schema=%s outcome=validated", self.schema_id)
                yield {"messageStart": {"role": "assistant"}}
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "toolUseId": uuid.uuid4().hex, "name": specs[0]["name"],
                }}, "contentBlockIndex": 0}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": output.model_dump_json()}},
                                             "contentBlockIndex": 0}}
                yield {"contentBlockStop": {"contentBlockIndex": 0}}
                yield {"messageStop": {"stopReason": "tool_use"}}
                for event in metadata:
                    yield event
                return
        logger.info("Structured finalization mode=tool schema=%s", self.schema_id)
        async for event in self.model.stream(
            messages, tool_specs=specs, system_prompt=system_prompt, tool_choice=tool_choice, **kwargs,
        ):
            yield event


def invoke_structured(
    agent: Any, prompt: str, output_model: type[BaseModel], *, investigate: bool = False,
    invocation_kwargs: dict[str, Any] | None = None,
    before_finalize: Callable[[], None] | None = None,
) -> Any:
    """Retain SDK lifecycle while selecting native output for the final decision only."""
    kwargs = dict(invocation_kwargs or {})
    model = getattr(agent, "model", None)
    if not isinstance(model, Model):
        # Compatibility for injected runners and SDK-compatible external agents.
        return agent(prompt, structured_output_model=output_model, **kwargs)
    if investigate:
        original_system = agent.system_prompt
        agent.system_prompt = str(original_system or "") + INVESTIGATION_GUIDANCE
        try:
            result = agent(prompt, **kwargs)
        finally:
            agent.system_prompt = original_system
        if getattr(result, "stop_reason", None) in {
            "limit_turns", "interrupt", "checkpoint", "cancelled", "guardrail_intervened", "content_filtered",
        }:
            raise RuntimeError("Investigation stopped before structured finalization")
    if before_finalize is not None:
        before_finalize()
    final_prompt = FINALIZATION_GUIDANCE if investigate else prompt
    adapter = NativeFinalizer(model, output_model)
    registry = agent.tool_registry
    agent.tool_registry = ToolRegistry()
    agent.model = adapter
    previous_native = getattr(model, "_cyber_native_schema", False)
    model._cyber_native_schema = True
    try:
        return agent(final_prompt, structured_output_model=output_model, **kwargs)
    except (ValidationError, ValueError) as error:
        # Let the existing caller own correction counts. Carry evidence forward so its
        # next bounded attempt can close investigation instead of replaying tool calls.
        error.native_finalization_messages = deepcopy(agent.messages)
        raise
    finally:
        model._cyber_native_schema = previous_native
        agent.model = model
        agent.tool_registry = registry


def retained_finalization_prompt(original_prompt: str, error: BaseException) -> str | None:
    """Render exact retained investigation for an existing caller-owned correction cycle."""
    messages = getattr(error, "native_finalization_messages", None)
    if messages is None:
        return None
    sections = [original_prompt, FINALIZATION_GUIDANCE, "\n## Retained structured investigation"]
    for message in messages:
        if message.get("role") in {"assistant", "tool"}:
            sections.append(f"\n{message['role']}: {json.dumps(message.get('content', []), ensure_ascii=False)}")
    return "\n".join(sections)
