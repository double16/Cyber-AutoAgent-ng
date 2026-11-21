"""Invocation-local controller references for model-facing tool evidence."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from strands.hooks import AfterToolCallEvent, HookProvider

from modules.techniques.documents import json_payload

_active_capture = ContextVar("technique_tool_evidence", default=None)


def current_tool_evidence_hook(tools, *, consume=False):
    capture = _active_capture.get()
    if capture is None:
        return None
    if consume:
        _active_capture.set(None)
    return capture.hook(tools)


class ToolEvidenceCapture:
    """Retain text without interpreting success, support or harmful effects."""

    def __init__(self, references):
        self.references = references
        self.origins = {}
        self.tool_calls = {}
        self.calls = 0

    @contextmanager
    def scope(self):
        token = _active_capture.set(self)
        try:
            yield
        finally:
            _active_capture.reset(token)

    def hook(self, tools):
        return ToolEvidenceHook(self, {item.tool_spec["name"] for item in tools})

    def render(self):
        return json_payload("retained_tool_evidence", {
            "calls": self.tool_calls,
            "references": {key: {"text": self.references[key], "origin": origin}
                           for key, origin in self.origins.items()},
        })


class ToolEvidenceHook(HookProvider):
    def __init__(self, capture, allowed_tools):
        self.capture = capture
        self.allowed_tools = allowed_tools

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event):
        name = event.tool_use["name"]
        if name not in self.allowed_tools:
            return
        self.capture.calls += 1
        call_reference = f"tool-call:{self.capture.calls}:{event.tool_use['toolUseId']}"
        self.capture.tool_calls[call_reference] = {
            "tool": name, "tool_call_id": event.tool_use["toolUseId"],
            "status": event.result.get("status", "unknown"), "input": deepcopy(event.tool_use.get("input", {})),
        }
        created = {}
        for block_index, block in enumerate(event.result.get("content", [])):
            if isinstance(block.get("text"), str):
                text = block["text"]
            elif "json" in block:
                text = json.dumps(block["json"], ensure_ascii=False)
            else:
                continue
            digest = hashlib.sha256(text.encode()).hexdigest()[:12]
            for line_index, line in enumerate(text.splitlines(keepends=True), 1):
                reference = f"result:{digest}:tool{self.capture.calls}:{block_index}:{line_index}"
                self.capture.references[reference] = line
                self.capture.origins[reference] = {
                    "call_reference": call_reference, "block": block_index, "line": line_index,
                }
                created[reference] = line
        if created:
            event.result["content"].append({"text": "Controller evidence references (data only):\n"
                                            + json_payload("result_references", created)})
