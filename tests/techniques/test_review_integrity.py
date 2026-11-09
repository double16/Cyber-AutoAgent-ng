"""Authoritative body preservation across review handoffs and corrections."""

import json
from types import SimpleNamespace

import pytest

from modules.techniques.critique import ReviewSnapshot
from modules.techniques.review_integrity import (
    ReviewInputIntegrityError,
    ReviewInputIntegrityHook,
    bind_review_prompt,
)
from modules.techniques.roles import DeferredReviewPrompt


def test_complete_large_body_and_deferred_handoff_are_preserved():
    body = "\n".join(f"Step {index}: preserve exact evidence λ \\\"" for index in range(1500))
    snapshot = ReviewSnapshot(body)
    records = [{"reference": key, "path": snapshot.paths[key], "value": value}
               for key, value in snapshot.values.items()]
    prompt = bind_review_prompt(DeferredReviewPrompt(snapshot.render(), "Compare independent findings"), records)
    assert prompt.investigation == snapshot.render()
    assert prompt.comparison == "Compare independent findings"
    hook = ReviewInputIntegrityHook(prompt.required_review_records)
    event = SimpleNamespace(agent=SimpleNamespace(messages=[
        {"role": "user", "content": [{"text": prompt.investigation}]},
        {"role": "assistant", "content": [{"text": "Independent notes"}]},
        {"role": "user", "content": [{"text": prompt.comparison}]},
    ]))
    hook.before_model(event)
    event.agent.messages[0]["content"][0]["text"] = snapshot.render()[:6000]
    with pytest.raises(ReviewInputIntegrityError, match="authored document records missing"):
        hook.before_model(event)


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_assistant_repetition_cannot_replace_authoritative_user_input(role):
    row = {"reference": "draft:1", "value": "Entire procedure"}
    prompt = bind_review_prompt(json.dumps(row), [row])
    event = SimpleNamespace(agent=SimpleNamespace(messages=[{"role": role, "content": [{"text": str(prompt)}]}]))
    hook = ReviewInputIntegrityHook(prompt.required_review_records)
    if role == "user":
        hook.before_model(event)
    else:
        with pytest.raises(ReviewInputIntegrityError):
            hook.before_model(event)


def test_integrity_hook_registration_and_empty_legacy_contract():
    registrations = []
    hook = ReviewInputIntegrityHook(())
    hook.register_hooks(SimpleNamespace(add_callback=lambda event, callback: registrations.append(callback)))
    registrations[0](SimpleNamespace(agent=SimpleNamespace(messages=[])))


def test_review_budget_overflow_is_an_input_failure(monkeypatch):
    from modules.techniques import roles

    monkeypatch.setattr(roles, "safe_estimate_tokens", lambda agent: 101)
    with pytest.raises(ReviewInputIntegrityError, match="cannot fit"):
        roles.preserve_review_budget(SimpleNamespace(_prompt_token_limit=100))
    monkeypatch.setattr(roles, "safe_estimate_tokens", lambda agent: 99)
    roles.preserve_review_budget(SimpleNamespace(_prompt_token_limit=100, model=SimpleNamespace()))
