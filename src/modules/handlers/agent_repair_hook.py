from __future__ import annotations

import re
from typing import Any

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import (
    AfterModelCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)

from modules.agents.patches import (
    _JSON_BARE_RE,
    _JSON_FENCE_RE,
    patch_ollama_model_json_toolcalls,
)
from modules.config.system.logger import get_logger
from modules.utils.tool_call_normalization import (
    normalize_tool_call_payload,
    repair_model_response_tool_input,
)

logger = get_logger("Handlers.AgentRepairHook")

_XML_TOOLCALL_RE = re.compile(r"<(?:function|parameter)=[^>]+>.*?</function>", re.DOTALL)

_TOOL_CALLS_RETRY_STATE_KEY = "force_openai_toolcalls_retry"
_JSON_TOOL_CALL_PATCH_ATTEMPT = False

class AgentRepairHook(HookProvider):
    """
    Case one:
    If a model prints XML-ish tool calls in content (common with qwen3-coder drift),
    retry once with an extra instruction to emit OpenAI-style tool_calls only.

    Case two:
    Ollama fails to parse tool_calls due to malformed JSON emitted by the model.
    Example: ollama._types.ResponseError: error parsing tool call: ... invalid character '}' after object key
    """

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterModelCallEvent, self.after_model_call_check)
        registry.add_callback(BeforeModelCallEvent, self.before_model_call_inject)
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call_repair)
        logger.debug("AgentRepairHook registered")

    def before_tool_call_repair(self, event: BeforeToolCallEvent) -> None:
        """Repair known response-envelope leakage before dispatching a tool call."""

        tool_use = event.tool_use
        repaired_input, repaired_fields = repair_model_response_tool_input(tool_use.get("input", {}))
        if repaired_fields:
            tool_use["input"] = repaired_input
            logger.warning(
                "Repaired trailing model tool envelope for %s field(s) on tool %s",
                ", ".join(repaired_fields),
                tool_use.get("name", "unknown"),
            )
        if tool_use.get("name") != "tool_use":
            return
        wrapper_input = tool_use.get("input", {})
        registry = getattr(getattr(event.agent, "tool_registry", None), "registry", {})
        try:
            normalized = normalize_tool_call_payload(
                {"name": "tool_use", **(wrapper_input if isinstance(wrapper_input, dict) else {})},
                registered_tool_names=registry.keys() if isinstance(registry, dict) else (),
            )
        except ValueError as error:
            available = ", ".join(sorted(registry)) if isinstance(registry, dict) else ""
            event.cancel_tool = (
                "Generic tool_use wrappers are invalid. Call a registered tool directly. "
                f"{error}. Available tools: {available or 'none'}."
            )
            return

        selected_tool = registry[normalized.name]
        event.selected_tool = selected_tool
        event.tool_use = {
            **tool_use,
            "name": normalized.name,
            "input": normalized.arguments,
        }
        logger.warning("Repaired generic tool_use wrapper to registered tool %s", normalized.name)

    def after_model_call_check(self, event: AfterModelCallEvent) -> None:
        """
        Runs after the model returns and before tools are processed.
        - If we detect XML-ish tool call markup, request a retry.

        Output-token exhaustion deliberately propagates to the workflow controller. The
        controller knows the role contract and can recover without trusting partial output.
        """
        global _JSON_TOOL_CALL_PATCH_ATTEMPT
        if event is None:
            return

        try:
            agent = event.agent
            callback_handler = getattr(agent, "callback_handler", None)

            # Ollama fails to parse tool_calls due to malformed JSON/XML emitted by the model.
            if event.exception is not None:
                if hasattr(event.exception, "status_code"):
                    status_code = getattr(event.exception, "status_code")
                else:
                    status_code = -1

                error_str = str(event.exception)
                error_str_l = error_str.lower()
                if (
                    "error parsing tool call" in error_str_l
                    or "invalid character" in error_str_l
                    or "parse tool call" in error_str_l
                    or "xml syntax error" in error_str_l
                    # FIXME: The status code check is for Ollama tool call errors. It should include a condition identifying the source as Ollama in the event.exception.
                    or status_code == 500
                ):
                    state = self._state_bag(event)
                    if not state.get(_TOOL_CALLS_RETRY_STATE_KEY):
                        state[_TOOL_CALLS_RETRY_STATE_KEY] = True
                        event.retry = True
                        record_efficiency = getattr(callback_handler, "record_efficiency_event", None)
                        if callable(record_efficiency):
                            record_efficiency("model_repair", agent=agent)
                        logger.warning(
                            "Detected tool-call parse error in step %s; retrying once with stricter tool_call instruction (%s)",
                            str(callback_handler.action_count) if callback_handler else "?",
                            error_str[:200].replace("\n", " "),
                        )
                    return

            if event.stop_response is None:
                return

            # Try to get assistant text in the most common ways.
            # Adjust these accessors if your event exposes different fields.
            for block in event.stop_response.message.get("content", []):
                if "text" in block:
                    assistant_text = block.get("text")
                else:
                    continue
                if not assistant_text:
                    continue

                # Look for tool call using json "name" and "arguments"/"parameters"
                if not _JSON_TOOL_CALL_PATCH_ATTEMPT:
                    json_tool_call_candidate = None
                    if json_m := _JSON_FENCE_RE.search(assistant_text):
                        json_tool_call_candidate = json_m.group(1)
                    elif json_m := _JSON_BARE_RE.search(assistant_text):
                        json_tool_call_candidate = json_m.group(1)
                    if json_tool_call_candidate is not None \
                            and '"name"' in json_tool_call_candidate \
                            and ('"arguments"' in json_tool_call_candidate or '"parameters"' in json_tool_call_candidate):
                        _JSON_TOOL_CALL_PATCH_ATTEMPT = True
                        if patch_ollama_model_json_toolcalls():
                            logger.info("Detected JSON style tool calls, patched model and retry")
                            event.retry = True
                            record_efficiency = getattr(callback_handler, "record_efficiency_event", None)
                            if callable(record_efficiency):
                                record_efficiency("model_repair", agent=agent)
                            return

                if _XML_TOOLCALL_RE.search(assistant_text):
                    # Mark for one retry and ask Strands to redo the model call
                    state = self._state_bag(event)
                    if state.get(_TOOL_CALLS_RETRY_STATE_KEY):
                        # already retried once; don't loop forever
                        return

                    state[_TOOL_CALLS_RETRY_STATE_KEY] = True
                    event.retry = True
                    record_efficiency = getattr(callback_handler, "record_efficiency_event", None)
                    if callable(record_efficiency):
                        record_efficiency("model_repair", agent=agent)
                    logger.warning(
                        "Detected XML-ish tool call markup in step %s; forcing model retry with corrective instruction",
                        str(callback_handler.action_count) if callback_handler else "?"
                    )
                    return
        except Exception as e:
            logger.debug("after_model_call_check error: %s", e)

    def before_model_call_inject(self, event: BeforeModelCallEvent) -> None:
        """
        Runs right before the model call.
        If the previous response triggered a retry, inject a short corrective instruction.
        """
        try:
            agent = event.agent
            messages = getattr(agent, "messages", None)
            if not isinstance(messages, list):
                return
            state = self._state_bag(event)

            if state.get(_TOOL_CALLS_RETRY_STATE_KEY):
                state.pop(_TOOL_CALLS_RETRY_STATE_KEY, None)

                messages.append({
                    "role": "system",
                    "content": [{"type": "text", "text": (
                        "IMPORTANT: Your previous output contained a malformed tool call that could not be parsed. "
                        "Tool calls must be emitted using OpenAI-style tool calling only "
                        "(tool_calls with JSON arguments). Do NOT output tool calls in XML/HTML/text "
                        "such as <function=...> or <parameter=...>, markdown code fences, or additional text. "
                        "For each tool call, the arguments MUST be strictly valid JSON (no trailing commas, no comments, "
                        "no extra braces, no partial objects, no stray characters). "
                        "Retry and emit ONLY valid OpenAI-style tool_calls. "
                    )}]
                })
                logger.warning("Injected tool-call format correction into retry model call")
                return

        except Exception as e:
            logger.debug("before_model_call_inject error: %s", e)

    def _state_bag(self, event: Any) -> dict:
        """
        Access a per-invocation mutable bag.
        Different Strands versions expose this differently.
        """
        for attr in ("invocation_state", "state", "context", "metadata"):
            bag = getattr(event, attr, None)
            if isinstance(bag, dict):
                return bag
        # Fallback: store on the agent instance (works when events don't carry state)
        agent = getattr(event, "agent", None)
        if agent is not None:
            bag = getattr(agent, "_hook_state", None)
            if not isinstance(bag, dict):
                bag = {}
                setattr(agent, "_hook_state", bag)
            return bag
        return {}
