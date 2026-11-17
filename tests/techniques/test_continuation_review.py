"""Recorded continuation regressions; scripted mode establishes contracts only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import review_response

from modules.techniques.roles import structured_system_prompt
from modules.techniques.triggers import RecommendationContext

FIXTURE = json.loads((Path(__file__).parent / "fixtures/continuation_review_connections.json").read_text())


@pytest.mark.parametrize("provider", [False, pytest.param(True, marks=pytest.mark.ollama)])
def test_recorded_continuation_reviews(scripted_service, monkeypatch, tmp_path, request, provider):
    service = scripted_service.service
    calls = []
    if provider:
        client, model = request.getfixturevalue("ollama_taxonomy_client")

    def reviewer(role, prompt, schema, tools=None):
        calls.append((role, prompt))
        if provider:
            result = client.chat(
                model=model,
                messages=[{"role": "system", "content": structured_system_prompt(schema)},
                          {"role": "user", "content": str(prompt)}],
                format=schema.model_json_schema(),
                think="low",
                options={"temperature": 0, "num_ctx": 48000, "num_predict": 8192},
            )
            print("CALIBRATION_DECISION", role, result.message.content)
            return schema.model_validate_json(result.message.content)
        if role == "technique_judge":
            return review_response(prompt)
        return schema(matches=[])

    monkeypatch.setattr(service, "_json", reviewer)
    revision = scripted_service.publish()
    monkeypatch.setattr(service, "_candidates", lambda *args: [{"revision_id": revision}])
    monkeypatch.setattr(service.store, "document", lambda *args: SimpleNamespace(
        markdown=FIXTURE["selection"]["procedure"], sha256="fixture",
        metadata=SimpleNamespace(model_dump=dict),
    ))
    assert service._select(FIXTURE["selection"]["request"]) == revision
    for case in FIXTURE["recommendations"]:
        context = RecommendationContext(service, ["hypothesis_generation"], [], tmp_path / (case["id"] + ".json"))
        trigger = {**context.triggers[0], "when": case["trigger_condition"]}
        context.triggers = [trigger]
        monkeypatch.setattr(service.store, "search_triggers", lambda *args, trigger=trigger: [trigger])
        context.lookup.subjects = [case["subject"]]
        context.observe(case["observation"], "artifact:shared-bundle", scope="shared")
        assert bool(context.recommended()) is case["expected_match"]
        assert not context.state["limitations"]
    assert len(calls) == 3
