from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from strands.types.exceptions import MaxTokensReachedException

from modules.config.models.generation_budget import GenerationRequest
from modules.config.models.invocation import invocation_output_tokens
from modules.techniques import structured_retry


class Decision(BaseModel):
    result: str


@pytest.mark.parametrize("outcome", ["success", "recovered", "exhausted", "capacity", "unknown", "invalid"])
def test_structured_recovery_is_bounded_and_scoped(monkeypatch, outcome):
    agent = SimpleNamespace(messages=[{"role": "user", "content": [{"text": "Original"}]}],
                            model=SimpleNamespace(_cyber_llm_role="technique_judge", config={"model_id": "fixture"}))
    calls = []
    def invoke(agent, prompt, schema, **kwargs):
        calls.append((prompt, kwargs, invocation_output_tokens("technique_judge")))
        agent.messages.append({"role": "assistant", "content": [{"text": "partial"}]})
        if outcome == "invalid":
            raise ValueError("Invalid contract")
        if outcome == "success" or (outcome == "recovered" and len(calls) == 2):
            return "complete"
        error = MaxTokensReachedException("Truncated")
        error.native_finalization_messages = list(agent.messages)
        raise error
    monkeypatch.setattr(structured_retry, "invoke_structured", invoke)
    monkeypatch.setattr(structured_retry, "schema_answer_tokens", lambda *args: 1024)
    monkeypatch.setattr(structured_retry, "safe_estimate_tokens", lambda agent: None if outcome == "unknown" else 100)
    monkeypatch.setattr(structured_retry, "model_generation_request", lambda model, tokens, **kwargs: GenerationRequest(
        context_tokens=48000, input_tokens=tokens, preferred_tokens=4096, answer_tokens=1024,
        output_ceiling=4096 if outcome == "capacity" else 16384,
    ))
    monkeypatch.setattr(structured_retry, "retained_finalization_prompt", lambda *args: "Retained evidence")
    if outcome in {"success", "recovered"}:
        assert structured_retry.invoke_technique_structured(agent, "Full input", Decision, investigate=True) == "complete"
    else:
        with pytest.raises(ValueError if outcome == "invalid" else RuntimeError):
            structured_retry.invoke_technique_structured(agent, "Full input", Decision, investigate=True)
    assert len(calls) == (2 if outcome in {"recovered", "exhausted"} else 1)
    if len(calls) == 2:
        assert calls[1][2] == 8192
        assert calls[1][1]["investigate"] is False
        assert "Retained evidence" in calls[1][0]
    assert invocation_output_tokens("technique_judge") is None


def test_retry_without_retained_messages_keeps_original_input(monkeypatch):
    agent = SimpleNamespace(messages=[], model=SimpleNamespace(_cyber_llm_role="judge", config={"model_id": "fixture"}))
    calls = []
    def invoke(agent, prompt, schema, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            raise MaxTokensReachedException("Truncated")
        return "complete"
    monkeypatch.setattr(structured_retry, "invoke_structured", invoke)
    monkeypatch.setattr(structured_retry, "schema_answer_tokens", lambda *args: 1024)
    monkeypatch.setattr(structured_retry, "safe_estimate_tokens", lambda agent: 100)
    monkeypatch.setattr(structured_retry, "model_generation_request", lambda *args, **kwargs: GenerationRequest(
        context_tokens=48000, input_tokens=100, preferred_tokens=4096, answer_tokens=1024,
    ))
    monkeypatch.setattr(structured_retry, "retained_finalization_prompt", lambda *args: None)
    assert structured_retry.invoke_technique_structured(agent, "Authoritative request", Decision,
                                                       finalization_context="Frozen comparison") == "complete"
    assert "Authoritative request" in calls[1]
    assert "Frozen comparison" in calls[1]


def test_investigation_truncation_does_not_finalize_without_evidence(monkeypatch):
    agent = SimpleNamespace(messages=[])
    def invoke(*args, **kwargs):
        raise MaxTokensReachedException("Investigation incomplete")
    monkeypatch.setattr(structured_retry, "invoke_structured", invoke)
    with pytest.raises(RuntimeError, match="evidence remains unresolved"):
        structured_retry.invoke_technique_structured(agent, "Complete inputs", Decision, investigate=True)
