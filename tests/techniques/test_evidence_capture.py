"""Controller-owned tool evidence stays invocation-local and available through repair."""

import json
from types import SimpleNamespace

import pytest

from modules.techniques.evidence import ToolEvidenceCapture, current_tool_evidence_hook


def event(name="reader", content=None, **result):
    return SimpleNamespace(tool_use={"name": name, "toolUseId": "call-1"},
                           result={"content": content or [], **result})


def test_capture_scope_is_nested_and_restored_on_error():
    tools = [SimpleNamespace(tool_spec={"name": "reader"})]
    first, second = ToolEvidenceCapture({}), ToolEvidenceCapture({})
    assert current_tool_evidence_hook(tools) is None
    with first.scope():
        assert current_tool_evidence_hook(tools).capture is first
        with pytest.raises(ValueError), second.scope():
            assert current_tool_evidence_hook(tools).capture is second
            raise ValueError("Exit nested scope")
        assert current_tool_evidence_hook(tools).capture is first
    assert current_tool_evidence_hook(tools) is None


@pytest.mark.parametrize("status", ["success", "error", None])
def test_exact_text_json_and_provenance_are_controller_referenced(status):
    references = {}
    capture = ToolEvidenceCapture(references)
    hook = capture.hook([SimpleNamespace(tool_spec={"name": "reader"})])
    callbacks = []
    hook.register_hooks(SimpleNamespace(add_callback=lambda kind, callback: callbacks.append((kind, callback))))
    text = 'Exact "λ" observation\nSecond line\u2028Final line'
    payload = {"nested": [True, None, "λ,;"], "count": 2}
    observed = event(content=[{"text": text}, {"json": payload}, {"image": {"data": "binary"}}],
                     **({"status": status} if status else {}))
    callbacks[0][1](observed)
    assert len(references) == 4
    assert list(references.values()) == [*text.splitlines(keepends=True), json.dumps(payload, ensure_ascii=False)]
    assert all(key.startswith("result:") for key in references)
    assert all(call["status"] == (status or "unknown") for call in capture.tool_calls.values())
    assert all(call["tool_call_id"] == "call-1" for call in capture.tool_calls.values())
    returned = json.loads(observed.result["content"][-1]["text"].split("\n", 1)[1])
    assert returned["result_references"] == references
    assert text.splitlines(keepends=True)[0] in [value["text"] for value in json.loads(capture.render())[
        "retained_tool_evidence"]["references"].values()]


def test_schema_tool_and_empty_or_binary_results_are_not_task_text_evidence():
    capture = ToolEvidenceCapture({})
    hook = capture.hook([SimpleNamespace(tool_spec={"name": "reader"})])
    ignored = event("schema_output", [{"text": "A model's own decision is not task evidence"}])
    hook.after_tool(ignored)
    hook.after_tool(event(content=[{"image": {}}, {"text": ""}, {"text": 3}]))
    assert not capture.references and not capture.origins
    assert len(ignored.result["content"]) == 1


def test_tool_inputs_are_frozen_with_result_provenance():
    capture = ToolEvidenceCapture({})
    hook = capture.hook([SimpleNamespace(tool_spec={"name": "reader"})])
    observed = event(content=[{"text": "Result text"}])
    observed.tool_use["input"] = {"path": "source.txt", "nested": [1, None]}
    hook.after_tool(observed)
    observed.tool_use["input"]["nested"].append(2)
    assert next(iter(capture.tool_calls.values()))["input"] == {"path": "source.txt", "nested": [1, None]}


def test_root_role_consumes_capture_so_nested_helper_roles_cannot_supply_task_evidence():
    capture = ToolEvidenceCapture({})
    tools = [SimpleNamespace(tool_spec={"name": "reader"})]
    with capture.scope():
        hook = current_tool_evidence_hook(tools, consume=True)
        assert hook.capture is capture
        assert current_tool_evidence_hook(tools) is None
        hook.after_tool(event(content=[{"text": "Root role tool result"}]))
    assert list(capture.references.values()) == ["Root role tool result"]
