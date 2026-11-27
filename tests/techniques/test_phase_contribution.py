"""Frozen continuation feedback, with independent review and opt-in provider calibration."""

import json
from pathlib import Path

import pytest

from modules.techniques.feedback import evaluate_feedback
from modules.techniques.feedback_activity import FeedbackTaskContext
from modules.techniques.roles import structured_system_prompt

FIXTURE = json.loads((Path(__file__).parent / "fixtures/phase_contribution.json").read_text())


@pytest.mark.parametrize("provider", [False, pytest.param(True, marks=pytest.mark.ollama)])
@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_conditional_hypothesis_contribution(scripted_service, monkeypatch, request, provider, case):
    service = scripted_service.service
    if provider:
        client, model = request.getfixturevalue("ollama_taxonomy_client")
        def reviewer(role, prompt, schema, tools=None):
            result = client.chat(
                model=model,
                messages=[{"role": "system", "content": structured_system_prompt(schema)},
                          {"role": "user", "content": str(prompt)}],
                format=schema.model_json_schema(), think="low",
                options={"temperature": 0, "num_ctx": 48000, "num_predict": 8192},
            )
            print("PHASE_CALIBRATION", case["id"], role, result.message.content)
            return schema.model_validate_json(result.message.content)
        monkeypatch.setattr(service, "_json", reviewer)
    use = {"revision_id": "frozen", "use_context": json.dumps({
        "delivered_documents": [FIXTURE["guidance"]],
    })}
    context = ("Guidance was delivered before producing this recorded hypothesis. "
               "No proposed target test was executed.\n" + case["result"])
    result = evaluate_feedback(service, use, context, [], task_context=FeedbackTaskContext(
        profile="hypothesis_generation", provenance="task_contract",
    ))
    assert result.score == case["reference_score"]
    assert case["historical_score"] == 0
