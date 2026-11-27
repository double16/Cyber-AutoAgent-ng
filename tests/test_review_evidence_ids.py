"""Evidence selection is model-owned; exact receipt expansion is controller-owned."""

from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

from modules.agents.review_evidence_ids import bind_evidence_ids
from modules.agents.structured_outputs import NoHypothesisBasis


@pytest.fixture
def evidence():
    availability = {"artifact:long/source-result.json": "content_bearing"}
    journal = SimpleNamespace(inspection_availability=lambda: availability)
    return availability, bind_evidence_ids(NoHypothesisBasis, journal)


def payload(reference):
    return {"kind": "inspected_evidence", "inspection_refs": [reference],
            "no_relevant_behavior_reason": "Observed behavior assessed"}


def test_native_and_tool_schemas_offer_exact_ids_and_expand_without_mutating_raw(evidence):
    _, model = evidence
    for schema in (model.model_json_schema(), StructuredOutputTool(model).tool_spec["inputSchema"]["json"]):
        field = schema["properties"]["inspection_refs"]["items"]
        assert field["enum"] == ["e1"]
        assert Draft202012Validator(field).is_valid("e1")
        assert not Draft202012Validator(field).is_valid("e2")
        assert not Draft202012Validator(field).is_valid("artifact:invented")
    raw = payload("e1")
    assert model.model_validate(raw).inspection_refs == ["artifact:long/source-result.json"]
    assert raw["inspection_refs"] == ["e1"]
    assert model.model_validate(payload("artifact:long/source-result.json")).inspection_refs == [
        "artifact:long/source-result.json",
    ]


def test_evidence_added_during_investigation_is_available_at_finalization(evidence):
    availability, model = evidence
    availability["artifact:new-inspection.json"] = "metadata_only"
    schema = model.model_json_schema()
    assert schema["properties"]["inspection_refs"]["items"]["enum"] == ["e1", "e2"]
    assert model.model_validate(payload("e2")).inspection_refs == ["artifact:new-inspection.json"]
    availability["artifact:unreadable"] = "unavailable"
    assert model.model_json_schema()["properties"]["inspection_refs"]["items"]["enum"] == ["e1", "e2"]


@pytest.mark.parametrize("value", ["e999", "memory:1", "invented"])
def test_unknown_ids_are_rejected(evidence, value):
    _, model = evidence
    with pytest.raises(ValueError):
        model.model_validate(payload(value))


def test_empty_journal_does_not_advertise_any_selectable_receipt():
    model = bind_evidence_ids(NoHypothesisBasis, SimpleNamespace(inspection_availability=dict))
    assert model.model_json_schema()["properties"]["inspection_refs"]["maxItems"] == 0
    assert model.model_validate({"kind": "metadata_only"}).inspection_refs == []


def test_real_native_finalization_refreshes_ids_after_investigation(evidence):
    from strands import Agent

    from modules.agents.native_structured import invoke_structured
    from tests.test_native_structured import Provider

    availability, output_model = evidence

    class EvidenceProvider(Provider):
        async def structured_output(self, selected_model, prompt, system_prompt=None, **kwargs):
            schema = selected_model.model_json_schema()
            assert schema["properties"]["inspection_refs"]["items"]["enum"] == ["e1", "e2"]
            yield {"output": payload("e2")}

    provider = EvidenceProvider()
    provider.config["model_id"] = "evidence-binding-fixture"
    agent = Agent(model=provider, tools=[], callback_handler=None, load_tools_from_directory=False)
    result = invoke_structured(
        agent, "Review supplied evidence", output_model, investigate=True,
        before_finalize=lambda: availability.update({"artifact:new-result.json": "content_bearing"}),
    )
    assert result.structured_output.inspection_refs == ["artifact:new-result.json"]


def test_real_tool_compatible_finalization_expands_selected_id(evidence):
    import json

    from strands import Agent

    from modules.agents.native_structured import invoke_structured
    from tests.test_native_structured import Provider

    _, output_model = evidence

    class ToolProvider(Provider):
        async def stream(self, messages, tool_specs=None, **kwargs):
            selected = tool_specs[0]
            assert selected["inputSchema"]["json"]["properties"]["inspection_refs"]["items"]["enum"] == ["e1"]
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {"toolUse": {
                "toolUseId": "decision", "name": selected["name"],
            }}, "contentBlockIndex": 0}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(payload("e1"))}},
                                        "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    provider = ToolProvider("bedrock")
    provider.config["model_id"] = "evidence-tool-fixture"
    agent = Agent(model=provider, tools=[], callback_handler=None, load_tools_from_directory=False)
    result = invoke_structured(agent, "Review supplied evidence", output_model)
    assert result.structured_output.inspection_refs == ["artifact:long/source-result.json"]


def test_legacy_case_only_keys_still_expand_ids(evidence):
    _, model = evidence
    raw = {"KIND": "inspected_evidence", "INSPECTION_REFS": ["e1"],
           "NO_RELEVANT_BEHAVIOR_REASON": "Observed behavior assessed"}
    assert model.model_validate(raw).inspection_refs == ["artifact:long/source-result.json"]
    assert raw["INSPECTION_REFS"] == ["e1"]
