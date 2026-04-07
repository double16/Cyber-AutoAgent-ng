"""
These are monkey patches to handle inconsistencies in some providers.

We require a unique ID per tool call. There `toolUseId` and `id` properties are candidates. `toolUseId` is used by
some providers for the tool name and strands ignores the `id` property. We modify the flow such that before a tool is
processed we detect this case and replace `toolUseId` with a unique value. Before sending the result back to the model,
we revert this or the model will do strange things like think the unique ID is a tool name that can be called. The
important parts of the flow are:

1. prompt sent to model
2. streaming response starts  <-- we need to patch the ID here by modifying the events
3. SDK event received by our handler
4. BeforeToolCallEvent hooks processed
5. AfterToolCallEvent hooks processed  <-- we need to revert here because hooks are allowed to change response content
6. SDK event received by our handler  <-- additional property _toolUseId is accepted here because it doesn't interfere with the model
7. Tool results sent to model

"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional, Type
from uuid import uuid4
from strands.hooks.events import AfterToolCallEvent
from strands.hooks import HookProvider, HookRegistry

from modules.config.system import get_logger


logger = get_logger("Agents.CyberAutoAgent")


@dataclass
class _ToolUseIdStreamState:
    current_tool_use_id: Optional[str] = None
    where_set: Optional[str] = None

    def __call__(self, *, where: str, marker: str, id_factory: Optional[Callable[[str], str]]) -> str:
        assert where
        assert marker
        assert id_factory
        if self.where_set and self.where_set != where:
            if not self.current_tool_use_id:
                self.current_tool_use_id = id_factory(marker)
                self.where_set = where
        else:
            self.current_tool_use_id = id_factory(marker)
            self.where_set = where
        return self.current_tool_use_id


@dataclass
class _JsonToolcallStreamState:
    """Tracks streamed assistant text so we can detect JSON tool calls that arrive across chunks.

    If a JSON tool call is detected in streamed *text* (not toolUse blocks), we synthesize
    Bedrock/Strands-style toolUse events:
      1) contentBlockStart.start.toolUse (name/toolUseId)
      2) contentBlockDelta.delta.toolUse.input (arguments JSON string)
      3) messageStop (stopReason: "tool_use")

    This matches the event shapes returned by OllamaModel.format_chunk in Strands.
    """

    buffer: str = ""
    max_len: int = 65536
    rejected: str = ""  # buffered text that was determined NOT to be a tool call and must be emitted

    # Pending synthetic tool use to emit over subsequent chunks
    pending_name: Optional[str] = None
    pending_input_json: Optional[str] = None
    pending_stage: int = 0  # 0=none, 1=need start, 2=need delta input, 3=need messageStop, 4=done

    def reset(self) -> None:
        self.buffer = ""
        # Do not clear rejected here; it is drained via pop_rejected().

    def is_buffering(self) -> bool:
        return bool(self.buffer)

    def pop_rejected(self) -> str:
        text = self.rejected
        self.rejected = ""
        return text

    def reset_pending(self) -> None:
        self.pending_name = None
        self.pending_input_json = None
        self.pending_stage = 0

    def should_start(self, fragment: str) -> bool:
        s = fragment.lstrip()
        return ("```json" in fragment.lower()) or s.startswith("{") or s.startswith("```")

    def queue_tool_use(self, tc_obj: dict) -> None:
        name = tc_obj.get("name")
        args = tc_obj.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            return
        try:
            args_json = json.dumps(args)
        except Exception:
            args_json = json.dumps({"_raw": str(args)})

        self.pending_name = name
        self.pending_input_json = args_json
        self.pending_stage = 1

    def has_pending(self) -> bool:
        return bool(self.pending_name) and self.pending_stage in (1, 2, 3)

    def next_pending_kind(self) -> str | None:
        if not self.pending_name:
            return None
        if self.pending_stage == 1:
            return "start"
        if self.pending_stage == 2:
            return "delta"
        if self.pending_stage == 3:
            return "stop"
        return None

    def feed(self, fragment: str) -> dict | None:
        """Feed a streamed text fragment; return a parsed tool call dict when complete.

        This buffers fragments across chunks and only attempts parsing once the payload appears
        complete (balanced braces or closing ``` fence).
        """
        if not fragment:
            return None

        if not self.buffer:
            if not self.should_start(fragment):
                return None
            self.buffer = fragment
        else:
            self.buffer += fragment

        # Avoid unbounded memory growth.
        if len(self.buffer) > self.max_len:
            self.rejected += self.buffer
            self.buffer = ""
            return None

        # If we started buffering but don't see tool call keys reasonably early, abandon buffering
        # so normal JSON answers don't get suppressed until messageStop.
        if len(self.buffer) >= 512:
            if '"name"' in self.buffer and ('"arguments"' in self.buffer or '"parameters"' in self.buffer):
                pass
            else:
                # Not a tool call; preserve buffered text so it can be emitted upstream.
                self.rejected += self.buffer
                self.buffer = ""
                return None

        if _json_toolcall_complete(self.buffer):
            tc = _extract_json_toolcall(self.buffer)
            if tc:
                self.reset()
                return tc
            # If it looks complete but fails to parse, reset to avoid poisoning future chunks.
            self.reset()
            return None

        return None

    def flush_buffer(self) -> str:
        buf = self.buffer
        self.reset()
        return buf


def _brace_balance(text: str) -> int:
    """Return net brace balance, ignoring braces inside JSON strings.

    Positive means missing closing braces. Negative indicates malformed ordering.
    """
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


def _json_toolcall_complete(buf: str) -> bool:
    """Return True when buffer likely contains a complete JSON tool call payload."""
    if not buf:
        return False

    lower = buf.lower()
    if "```json" in lower:
        # Require a closing fence after the opening.
        start = lower.find("```json")
        end = lower.find("```", start + 6)
        if end != -1:
            # ensure at least two fences (open+close)
            end2 = lower.find("```", end + 3)
            return end2 != -1
        return False

    s = buf.strip()
    if s.startswith("{") and s.endswith("}") and _brace_balance(s) == 0:
        return True

    return False


def _extract_text_from_blocks(content: Any) -> str:
    """Extract concatenated text from Strands-style content blocks."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            t = block.get("text")
            if isinstance(t, str) and t:
                parts.append(t)
    return "".join(parts)


def _extract_text_from_event(out: Any) -> str:
    """Extract streamed text fragment from common Strands/Ollama event-shaped dicts."""
    if not isinstance(out, dict):
        return ""

    # Bedrock-ish / Strands: {'contentBlockDelta': {'delta': {'text': '...'}}}
    cbd = out.get("contentBlockDelta")
    if isinstance(cbd, dict):
        delta = cbd.get("delta")
        if isinstance(delta, dict):
            t = delta.get("text")
            if isinstance(t, str) and t:
                return t

    # Alternate: {'contentBlockStart': {'start': {'text': '...'}}}
    cbs = out.get("contentBlockStart")
    if isinstance(cbs, dict):
        start = cbs.get("start")
        if isinstance(start, dict):
            t = start.get("text")
            if isinstance(t, str) and t:
                return t

    # OpenAI-ish shapes already supported via content blocks
    content = out.get("content")
    frag = _extract_text_from_blocks(content)
    if frag:
        return frag

    msg = out.get("message")
    if isinstance(msg, dict):
        return _extract_text_from_blocks(msg.get("content"))

    delta2 = out.get("delta")
    if isinstance(delta2, dict):
        return _extract_text_from_blocks(delta2.get("content"))

    return ""


def _clear_text_in_event(out: Any) -> None:
    """Clear streamed text in-place in common Strands/Ollama event-shaped dicts."""
    if not isinstance(out, dict):
        return

    cbd = out.get("contentBlockDelta")
    if isinstance(cbd, dict):
        delta = cbd.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            delta["text"] = ""

    cbs = out.get("contentBlockStart")
    if isinstance(cbs, dict):
        start = cbs.get("start")
        if isinstance(start, dict) and isinstance(start.get("text"), str):
            start["text"] = ""

    content = out.get("content")
    _clear_text_blocks(content)

    msg = out.get("message")
    if isinstance(msg, dict):
        _clear_text_blocks(msg.get("content"))

    delta2 = out.get("delta")
    if isinstance(delta2, dict):
        _clear_text_blocks(delta2.get("content"))


def _clear_text_blocks(content: Any) -> None:
    """Clear text from Strands-style content blocks in-place."""
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and "text" in block:
            block["text"] = ""

def patch_model_class_tool_use_id(
        model_cls: Type[Any],
        *,
        id_factory: Optional[Callable[[str], str]] = None,
        attr_prefix: str = "_tooluseid_class_patch",
) -> Type[Any]:
    """
    Monkey-patch model_cls.stream at the *class* level so toolUseId is unique per invocation.

    Patches tool-use IDs in these event shapes (covers common Strands provider normalizations):
      - ev["contentBlockStart"]["start"]["toolUse"]   (Bedrock-ish)
      - ev["contentBlockDelta"]["delta"]["toolUse"]   (Bedrock-ish)
      - ev["current_tool_use"]                        (Strands callback convenience)

    Idempotent: safe to call multiple times.

    Returns:
      model_cls (patched)
    """
    enabled_attr = f"{attr_prefix}_enabled"
    orig_attr = f"{attr_prefix}_orig_stream"

    if getattr(model_cls, enabled_attr, False):
        return model_cls

    if not hasattr(model_cls, "stream"):
        if "stubs" in model_cls.__qualname__.lower():
            # unit test mock
            return model_cls
        raise TypeError(f"{model_cls.__name__} has no 'stream' method to patch")

    # inline function supports unit tests
    # marker: X - no toolUseId given, N - no tool_name given, E - toolUseId == tool_name, 'U' - unknown
    if id_factory is None:
        id_factory = lambda marker: f"tooluse_{marker or 'U'}-{uuid4().hex}"

    orig_stream = getattr(model_cls, "stream")
    setattr(model_cls, orig_attr, orig_stream)

    @functools.wraps(orig_stream)
    async def stream_patched(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        state = _ToolUseIdStreamState()

        def _patch_tool_use_id(event: dict[str, Any], where: str) -> None:
            name = event.get("name")
            tuid = event.get("toolUseId")
            if not name and not tuid:
                return
            if not tuid:
                marker = 'X'
            elif not name:
                marker = 'N'
            elif tuid == name:
                marker = 'E'
            else:
                return
            if not name:
                # in this case, toolUseId is the tool name, but no tool_name was given
                event["name"] = tuid
            tuid = state(where=where, marker=marker, id_factory=id_factory)
            event["_toolUseId"] = event["toolUseId"] = tuid

        async for ev in orig_stream(self, *args, **kwargs):
            # contentBlockStart and current_tool_use may come in any order, but we assume for a given provider the order is consistent

            # --- Pattern A: contentBlockStart -> toolUse ---
            cbs = ev.get("contentBlockStart")
            if isinstance(cbs, dict):
                start = cbs.get("start")
                if isinstance(start, dict):
                    tool_use = start.get("toolUse")
                    if isinstance(tool_use, dict):
                        _patch_tool_use_id(tool_use, "contentBlockStart")

            # --- Pattern B: contentBlockDelta -> toolUse (keep consistent) ---
            # Assumption: contentBlockDelta do not overlap with concurrent tool uses
            cbd = ev.get("contentBlockDelta")
            if isinstance(cbd, dict):
                delta = cbd.get("delta")
                if isinstance(delta, dict):
                    dtu = delta.get("toolUse")
                    if isinstance(dtu, dict):
                        name = dtu.get("name")
                        tuid = dtu.get("toolUseId")
                        if (name or tuid) and state.current_tool_use_id:
                            dtu["_toolUseId"] = dtu["toolUseId"] = state.current_tool_use_id

            # --- Pattern C: Strands convenience field current_tool_use ---
            ctu = ev.get("current_tool_use")
            if isinstance(ctu, dict):
                _patch_tool_use_id(ctu, "current_tool_use")

            yield ev

    setattr(model_cls, "stream", stream_patched)
    setattr(model_cls, enabled_attr, True)
    return model_cls


def unpatch_model_class_tool_use_id(
        model_cls: Type[Any],
        *,
        attr_prefix: str = "_tooluseid_class_patch",
) -> Type[Any]:
    """Restore the original model_cls.stream if it was patched by patch_model_class_tool_use_id()."""
    enabled_attr = f"{attr_prefix}_enabled"
    orig_attr = f"{attr_prefix}_orig_stream"

    if getattr(model_cls, enabled_attr, False) and hasattr(model_cls, orig_attr):
        setattr(model_cls, "stream", getattr(model_cls, orig_attr))
        setattr(model_cls, enabled_attr, False)
    return model_cls


class ToolUseIdHook(HookProvider):
    def register_hooks(self, registry: "HookRegistry", **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.revert_tool_use_id)

    def revert_tool_use_id(self, event: AfterToolCallEvent):
        tool_use = getattr(event, "tool_use", {})
        tool_name = tool_use.get("name", "")
        tool_use_id = tool_use.get("toolUseId", "")

        tool_use_id_type = tool_use_id[:10]
        if tool_use_id_type in ["tooluse_N-", "tooluse_X-", "tooluse_E-", "tooluse_U-"] and tool_name:
            # reverse the patch that set a generated ID because some models use toolUseId as the tool name or no tool name at all !?!

            reverted_tool_name = tool_name
            if tool_use_id_type == "tooluse_E-":
                reverted_tool_use_id = tool_name
            elif tool_use_id_type == "tooluse_N-":
                reverted_tool_use_id = tool_name
                reverted_tool_name = ''
            else:
                reverted_tool_use_id = ''

            tool_use["_toolUseId"] = tool_use_id
            tool_use["toolUseId"] = reverted_tool_use_id
            tool_use["name"] = reverted_tool_name

            result = getattr(event, "result", None)
            if isinstance(result, dict) and "toolUseId" in result:
                # there is no tool_name in "result"
                result["_toolUseId"] = tool_use_id
                result["toolUseId"] = reverted_tool_use_id


_OLLAMA_MODEL_TOKEN_USAGE_PATCH_ATTR = "_caa_ollama_usage_patch_v1"

_OLLAMA_MODEL_JSON_TOOLCALL_PATCH_ATTR = "_caa_ollama_json_toolcall_patch_v1"

_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_JSON_BARE_RE = re.compile(r"^\s*(\{.*\})\s*$", re.DOTALL)


def _extract_json_toolcall(text: str, *, allow_missing_end_brace: bool = False) -> dict | None:
    """Parse JSON tool call objects that were emitted as assistant text.

    Accepts either a fenced ```json block or a bare JSON object.
    Expected shape: {"name": "...", "arguments": {...}}
    Also accepts: {"tool_call": {"name": "...", "arguments": {...}}}
    """
    if not text:
        return None

    m = _JSON_FENCE_RE.search(text)
    if m:
        blob = m.group(1)
    else:
        m2 = _JSON_BARE_RE.match(text)
        if not m2:
            return None
        blob = m2.group(1)

    try:
        obj = json.loads(blob)
    except Exception:
        # Streaming can occasionally drop the final closing brace. Only try this recovery when
        # explicitly allowed (typically on messageStop).
        if not allow_missing_end_brace:
            return None
        s = blob.strip()
        if s.startswith("{"):
            try:
                if _brace_balance(s) == 1:
                    obj = json.loads(s + "}")
                else:
                    return None
            except Exception:
                return None
        else:
            return None

    if not isinstance(obj, dict):
        return None

    if "name" in obj and "arguments" in obj:
        return obj
    if "name" in obj and "parameters" in obj:
        return {"name": obj["name"], "arguments": obj["parameters"]}

    if "tool_call" in obj and isinstance(obj["tool_call"], dict):
        tc = obj["tool_call"]
        if "name" in tc and "arguments" in tc:
            return tc
        if "name" in tc and "parameters" in tc:
            return {"name": tc["name"], "arguments": tc["parameters"]}

    return None


def _to_openai_tool_calls(toolcall_obj: dict, *, id_factory: Optional[Callable[[], str]] = None) -> list[dict]:
    """Convert {"name": ..., "arguments": ...} to OpenAI-style tool_calls."""
    name = toolcall_obj.get("name")
    args = toolcall_obj.get("arguments", {})

    if not isinstance(name, str) or not name.strip():
        return []

    try:
        args_json = json.dumps(args)
    except Exception:
        args_json = json.dumps({"_raw": str(args)})

    if id_factory is None:
        id_factory = lambda: f"call_{uuid4().hex}"

    return [
        {
            "id": id_factory(),
            "type": "function",
            "function": {
                "name": name,
                "arguments": args_json,
            },
        }
    ]


def _coerce_message_json_toolcall(message: Any) -> bool:
    """If message contains JSON-in-text tool call, rewrite message in-place.

    Returns True when a coercion was applied.
    """
    if not isinstance(message, dict):
        return False
    if message.get("tool_calls"):
        return False

    content = message.get("content")
    if not isinstance(content, list):
        return False

    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        tc_obj = _extract_json_toolcall(text)
        if not tc_obj:
            continue

        tool_calls = _to_openai_tool_calls(tc_obj)
        if not tool_calls:
            continue

        message["tool_calls"] = tool_calls
        message["content"] = []
        return True

    return False


def patch_ollama_model_token_usage(
        *,
        module_name: str = "strands.models.ollama",
        cls_name: str = "OllamaModel",
        validate: bool = True,
) -> None:
    """
    Monkey-patch OllamaModel.format_chunk to correct usage token mapping.

    Correct mapping for Ollama:
      - prompt_eval_count -> inputTokens (prompt)
      - eval_count        -> outputTokens (completion)

    Notes:
        - Idempotent: repeated calls do not stack patches.
    """
    mod = __import__(module_name, fromlist=[cls_name])
    OllamaModel: Type[Any] = getattr(mod, cls_name)

    if not hasattr(OllamaModel, "format_chunk"):
        logger.warning(f"{module_name}.{cls_name} has no format_chunk method")
        return

    # If already patched, do nothing (idempotent).
    existing = getattr(OllamaModel, _OLLAMA_MODEL_TOKEN_USAGE_PATCH_ATTR, None)
    if isinstance(existing, dict) and existing.get("is_patched") is True:
        return

    original_format_chunk = OllamaModel.format_chunk

    def patched_format_chunk(self: Any, event: dict[str, Any]) -> Any:
        out = original_format_chunk(self, event)

        # Only touch metadata chunks that contain usage dict.
        try:
            if isinstance(out, dict) and "metadata" in out:
                md = out.get("metadata") or {}
                usage = md.get("usage")

                if isinstance(usage, dict):
                    data = event.get("data")

                    # Ollama python client event has these fields on the event object.
                    prompt_eval_count = getattr(data, "prompt_eval_count", None)
                    eval_count = getattr(data, "eval_count", None)

                    # Only rewrite when we can confidently read both values.
                    if prompt_eval_count is not None and eval_count is not None:
                        usage["inputTokens"] = int(prompt_eval_count)
                        usage["outputTokens"] = int(eval_count)
                        usage["totalTokens"] = int(prompt_eval_count) + int(eval_count)
        except Exception:
            # Never break streaming due to patch logic.
            return out

        return out

    # Apply patch and record original for safe restoration.
    OllamaModel.format_chunk = patched_format_chunk  # type: ignore[method-assign]
    setattr(
        OllamaModel,
        _OLLAMA_MODEL_TOKEN_USAGE_PATCH_ATTR,
        {
            "is_patched": True,
            "original": original_format_chunk,
        },
    )

    if validate and OllamaModel.format_chunk is original_format_chunk:
        raise RuntimeError("Patch did not apply (method reference unchanged)")


def patch_ollama_model_json_toolcalls(
        *,
        module_name: str = "modules.config.models.ollama",
        cls_name: str = "OllamaModel",
        validate: bool = True,
) -> bool:
    """Monkey-patch OllamaModel so JSON tool calls emitted as assistant text become real tool calls.

    Some models/providers (e.g., qwen2.5-coder via Ollama) may emit tool calls as literal JSON text.
    Strands captures stop_reason before AfterModelCall hooks, so the reliable fix is to normalize
    inside the provider/model implementation.

    This patch wraps OllamaModel's response formatting path and rewrites the message to include
    OpenAI-style `tool_calls`, and sets stop_reason to `tool_use` when possible.

    Returns:
        - True if patched applied, False if already patched or patch failed

    Notes:
      - Idempotent: repeated calls do not stack patches.
      - Safe: never raises from coercion logic.
    """
    mod = __import__(module_name, fromlist=[cls_name])
    OllamaModel: Type[Any] = getattr(mod, cls_name)

    existing = getattr(OllamaModel, _OLLAMA_MODEL_JSON_TOOLCALL_PATCH_ATTR, None)
    if isinstance(existing, dict) and existing.get("is_patched") is True:
        return False

    # Prefer patching a dedicated formatter if present; otherwise patch the main invoke path.
    formatter_name = None
    for cand in (
            "format_chunk",  # streaming normalization point (preferred)
            "format_response",
            "format_output",
            "format_message",
            "_format_message",
            "_format_response",
    ):
        if hasattr(OllamaModel, cand):
            formatter_name = cand
            break

    if formatter_name:
        original = getattr(OllamaModel, formatter_name)

        def _coerce_out_dict(out_dict: dict[str, Any]) -> bool:
            """Coerce JSON tool call text found in common streaming/response dict shapes."""
            # Shape A: OpenAI-ish message container
            msg = out_dict.get("message")
            if _coerce_message_json_toolcall(msg):
                out_dict["stop_reason"] = "tool_use"
                return True

            # Shape B: some providers emit content blocks at the top-level
            content = out_dict.get("content")
            if isinstance(content, list) and not out_dict.get("tool_calls"):
                tmp_msg = {"content": content}
                if _coerce_message_json_toolcall(tmp_msg):
                    out_dict["tool_calls"] = tmp_msg.get("tool_calls")
                    out_dict["content"] = []
                    out_dict["stop_reason"] = "tool_use"
                    return True

            # Shape C: streaming delta content
            delta = out_dict.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), list) and not out_dict.get("tool_calls"):
                tmp_msg = {"content": delta.get("content")}
                if _coerce_message_json_toolcall(tmp_msg):
                    out_dict["tool_calls"] = tmp_msg.get("tool_calls")
                    # clear delta content so it isn't treated as assistant text
                    delta["content"] = []
                    out_dict["stop_reason"] = "tool_use"
                    return True

            return False

        # Special-case format_chunk signature: (self, event) -> dict
        if formatter_name == "format_chunk":

            def patched(self: Any, event: dict[str, Any]) -> Any:
                out = original(self, event)
                try:
                    if not isinstance(out, dict):
                        return out

                    # Per-instance stream state (so concurrent models don't interfere).
                    state: _JsonToolcallStreamState = getattr(self, "_caa_json_toolcall_stream_state", None)
                    if state is None:
                        state = _JsonToolcallStreamState()
                        setattr(self, "_caa_json_toolcall_stream_state", state)

                    # If we previously detected a JSON tool call in streamed text, emit the pending
                    # synthetic events (start -> delta input -> messageStop) before processing the
                    # provider's current chunk.
                    kind = state.next_pending_kind() if state.has_pending() else None

                    # If we are buffering suspected JSON tool call text and the provider indicates the
                    # message is stopping, flush buffered text *only if* we never detected a tool call.
                    if "messageStop" in out and not state.has_pending():
                        if state.is_buffering():
                            buffered = state.flush_buffer()

                            # Final chance: the last closing brace may be missing. Only attempt this
                            # recovery at messageStop.
                            tc_final = _extract_json_toolcall(buffered, allow_missing_end_brace=True)
                            if tc_final:
                                state.queue_tool_use(tc_final)
                                out.clear()
                                out.update(
                                    {
                                        "contentBlockStart": {
                                            "start": {
                                                "toolUse": {
                                                    "name": state.pending_name,
                                                    "toolUseId": state.pending_name,
                                                }
                                            }
                                        },
                                        "contentBlockDelta": {
                                            "delta": {
                                                "toolUse": {
                                                    "input": state.pending_input_json or "{}",
                                                }
                                            }
                                        },
                                        "messageStop": {"stopReason": "tool_use"},
                                    }
                                )
                                state.reset_pending()
                                return out

                            # Not a tool call; emit buffered text as a single final delta alongside messageStop.
                            out.clear()
                            out.update(
                                {
                                    "contentBlockDelta": {"delta": {"text": buffered}},
                                    "messageStop": out.get("messageStop"),
                                }
                            )
                            return out

                    # If we previously detected a JSON tool call in streamed text, emit the pending
                    # synthetic events (start -> delta input -> messageStop) before processing the
                    # provider's current chunk.
                    if "messageStop" in out and state.has_pending():
                        # Drop the provider's stop and replace with our synthetic tool_use stop.
                        out.clear()
                        out.update({"messageStop": {"stopReason": "tool_use"}})
                        state.reset_pending()
                        return out

                    if kind == "start":
                        out.clear()
                        out.update(
                            {
                                "contentBlockStart": {
                                    "start": {
                                        "toolUse": {
                                            "name": state.pending_name,
                                            "toolUseId": state.pending_name,
                                        }
                                    }
                                }
                            }
                        )
                        state.pending_stage = 2
                        return out

                    if kind == "delta":
                        out.clear()
                        out.update(
                            {
                                "contentBlockDelta": {
                                    "delta": {
                                        "toolUse": {
                                            "input": state.pending_input_json or "{}",
                                        }
                                    }
                                }
                            }
                        )
                        state.pending_stage = 3
                        return out

                    # Feed any streamed text fragment (may arrive across many chunks).
                    frag = _extract_text_from_event(out)
                    tc_obj = state.feed(frag)

                    rejected = state.pop_rejected()
                    if rejected:
                        # Emit previously buffered (but non-tool call) text as a single delta.
                        out.clear()
                        out.update({"contentBlockDelta": {"delta": {"text": rejected}}})
                        return out

                    # If we're buffering potential JSON tool call content and we haven't completed a
                    # tool call yet, suppress emitting the fragment to avoid leaking tool JSON into
                    # assistant text during streaming.
                    if state.is_buffering() and not tc_obj and frag:
                        _clear_text_in_event(out)
                        return out

                    if tc_obj:
                        # Queue the synthetic toolUse; it will be emitted as start/delta/stop on
                        # subsequent calls.
                        state.queue_tool_use(tc_obj)
                        state.flush_buffer()
                        # Emit the first pending kind immediately by returning a start event.
                        out.clear()
                        out.update(
                            {
                                "contentBlockStart": {
                                    "start": {
                                        "toolUse": {
                                            "name": state.pending_name,
                                            "toolUseId": state.pending_name,
                                        }
                                    }
                                }
                            }
                        )
                        state.pending_stage = 2
                        return out

                    # Fall back to single-chunk coercion for already-complete payloads.
                    _coerce_out_dict(out)
                except Exception:
                    return out
                return out

        else:

            def patched(self: Any, *args: Any, **kwargs: Any) -> Any:
                out = original(self, *args, **kwargs)
                try:
                    # Common Strands shape: out has `.message` dict and `.stop_reason` attribute.
                    msg = getattr(out, "message", None)
                    coerced = _coerce_message_json_toolcall(msg)
                    if coerced:
                        try:
                            setattr(out, "stop_reason", "tool_use")
                        except Exception:
                            pass

                    # Some providers return dicts.
                    if isinstance(out, dict):
                        _coerce_out_dict(out)
                except Exception:
                    return out
                return out

        setattr(OllamaModel, formatter_name, patched)
        setattr(
            OllamaModel,
            _OLLAMA_MODEL_JSON_TOOLCALL_PATCH_ATTR,
            {"is_patched": True, "original": original, "where": formatter_name},
        )

        if validate and getattr(OllamaModel, formatter_name) is original:
            raise RuntimeError("Patch did not apply (method reference unchanged)")
        return True

    # Fallback: patch invoke/chat/generate method and post-process its return.
    invoke_name = None
    for cand in ("invoke", "chat", "__call__", "complete", "generate"):
        if hasattr(OllamaModel, cand):
            invoke_name = cand
            break

    if not invoke_name:
        logger.warning(f"{module_name}.{cls_name} has no known method to patch for JSON tool calls")
        return False

    original_invoke = getattr(OllamaModel, invoke_name)

    def patched_invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        out = original_invoke(self, *args, **kwargs)
        try:
            msg = getattr(out, "message", None)
            coerced = _coerce_message_json_toolcall(msg)
            if coerced:
                try:
                    setattr(out, "stop_reason", "tool_use")
                except Exception:
                    pass

            if isinstance(out, dict):
                # Mirror the same coercion logic used for formatter patches.
                msg_dict = out.get("message")
                if _coerce_message_json_toolcall(msg_dict):
                    out["stop_reason"] = "tool_use"
                else:
                    content = out.get("content")
                    if isinstance(content, list) and not out.get("tool_calls"):
                        tmp_msg = {"content": content}
                        if _coerce_message_json_toolcall(tmp_msg):
                            out["tool_calls"] = tmp_msg.get("tool_calls")
                            out["content"] = []
                            out["stop_reason"] = "tool_use"
        except Exception:
            return out
        return out

    setattr(OllamaModel, invoke_name, patched_invoke)
    setattr(
        OllamaModel,
        _OLLAMA_MODEL_JSON_TOOLCALL_PATCH_ATTR,
        {"is_patched": True, "original": original_invoke, "where": invoke_name},
    )

    if validate and getattr(OllamaModel, invoke_name) is original_invoke:
        raise RuntimeError("Patch did not apply (method reference unchanged)")
    return True
