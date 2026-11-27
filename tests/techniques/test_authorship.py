"""Production authorship accepts author judgement after one advisory review."""

import json

import pytest
from pydantic import ValidationError

from modules.techniques.publication_review import AuthorshipAdvice
from modules.techniques.roles import SYSTEM, RolePrompt


@pytest.mark.parametrize("subject", ["HTTP response comparison", "TCP service inspection", "Filesystem source review"])
@pytest.mark.parametrize("recommendations", [[], ["Clarify the interpretation of the observations"]])
def test_advice_is_optional_and_never_a_veto(scripted_service, subject, recommendations):
    state = scripted_service
    original = state.service.runner
    calls = []
    author_prompts = []
    critic_prompts = []

    def runner(role, prompt, tools):
        if role == "technique_author":
            calls.append(role)
            author_prompts.append(prompt)
            assert isinstance(prompt, RolePrompt)
            payload = json.loads(original(role, prompt, tools))
            payload["body"] = f"# {subject}\n" + ("Improved interpretation." if len(author_prompts) > 1 else "Initial guidance.")
            return json.dumps(payload)
        if role == "technique_critic":
            calls.append(role)
            critic_prompts.append(prompt)
            assert isinstance(prompt, RolePrompt)
            assert tools
            return json.dumps({"summary": "I would reject this document", "recommendations": recommendations})
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, document = state.service.get(subject)
    assert state.service.repository.get(revision)["active"]
    assert calls == (["technique_author", "technique_critic", "technique_author"] if recommendations
                     else ["technique_author", "technique_critic"])
    assert ("Improved interpretation" in document) is bool(recommendations)
    assert len(state.research_calls) == 1
    critic = critic_prompts[0]
    assert "recommendations" in SYSTEM + critic.instructions
    assert "publication blockers" not in critic.instructions + critic.data
    assert '"existing_examples"' not in critic.data
    assert "authoring" not in critic.instructions.lower()
    if recommendations:
        assert "recommendations[1]" in author_prompts[-1].data
        assert recommendations[0] in author_prompts[-1].data
        assert "Initial guidance" in author_prompts[-1].data


@pytest.mark.parametrize("failure", ["malformed", "transport", "blank"])
def test_unavailable_or_empty_advice_accepts_initial(scripted_service, failure):
    state = scripted_service
    original = state.service.runner
    calls = []

    def runner(role, prompt, tools):
        calls.append(role)
        if role == "technique_critic":
            if failure == "transport":
                raise RuntimeError("Provider unavailable")
            if failure == "malformed":
                return "not JSON"
            return '{"summary":"No useful changes","recommendations":[" "]}'
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, _ = state.service.get("JWT")
    assert state.service.repository.get(revision)["active"]
    assert calls.count("technique_author") == calls.count("technique_critic") == 1


@pytest.mark.parametrize("failure", ["json", "transport", "privacy", "phase", "inactive"])
def test_unavailable_revision_retains_valid_initial(scripted_service, failure):
    from modules.techniques.context import MethodologyContext

    state = scripted_service
    original = state.service.runner
    author_calls = []
    state.service.private_values = ("private-host.invalid",)

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return '{"summary":"Useful refinement","recommendations":["Clarify controls"]}'
        if role == "technique_author":
            author_calls.append(prompt)
            if len(author_calls) > 1:
                if failure == "transport":
                    raise RuntimeError("Provider unavailable")
                if failure == "json":
                    return "not JSON"
                payload = json.loads(original(role, prompt, tools))
                if failure == "privacy":
                    payload["body"] = "Contact private-host.invalid"
                elif failure == "inactive":
                    payload["metadata"]["active"] = False
                else:
                    payload["metadata"]["phases"] = ["analysis"]
                    payload["metadata"]["triggers"] = []
                return json.dumps(payload)
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, document = state.service.get("JWT", MethodologyContext(phase_contexts=["hypothesis_generation"]))
    assert state.service.repository.get(revision)["active"]
    assert "private-host.invalid" not in document
    assert len(author_calls) == (2 if failure == "transport" else 4)
    assert len(state.research_calls) == 1


def test_advice_schema_has_no_rejection_vote_and_legacy_is_advisory():
    assert set(AuthorshipAdvice.model_json_schema()["properties"]) == {"summary", "recommendations"}
    with pytest.raises(ValidationError):
        AuthorshipAdvice.model_validate({"summary": "Fine", "approved": False})
    value = AuthorshipAdvice.from_legacy({"approved": False, "reason": "Improve", "issues": [
        {"requested_correction": "Clarify controls"}], "suggestions": ["Short example"]})
    advice = AuthorshipAdvice.model_validate(value)
    assert advice.recommendations == ["Clarify controls", "Short example"]


@pytest.mark.parametrize("recommendations", [[], ["Clarify the controls"]])
def test_native_schema_path_preserves_author_critic_author_contract(scripted_service, monkeypatch, recommendations):
    from types import SimpleNamespace

    from modules.techniques.coverage import Requirement
    from modules.techniques.service import AuthoredOutput

    state = scripted_service
    requirements = [Requirement(id="method", description="JWT handling")]
    retained = state.service._research_coverage("JWT handling", requirements)
    monkeypatch.setattr(state.service, "_research_coverage", lambda *args, **kwargs: retained)
    original = state.service.runner
    calls = []

    def structured(role, prompt, schema, tools):
        calls.append((role, prompt, schema))
        if role == "technique_critic":
            assert schema is AuthorshipAdvice and isinstance(prompt, RolePrompt)
            return schema(summary="Advisory improvements", recommendations=recommendations)
        if role == "technique_author":
            assert schema is AuthoredOutput and isinstance(prompt, RolePrompt)
            return schema(metadata={"title": "JWT guidance", "purpose": "Explain token checks",
                                    "applicability": ["Token handling"], "phases": ["hypothesis_generation"]},
                          body=["# Guidance", "", "Establish a baseline and compare controls."])
        return schema.model_validate(json.loads(original(role, prompt, tools)))

    state.service.runner = SimpleNamespace(structured=structured)
    revision, body = state.service.get("JWT handling")
    assert state.service.repository.get(revision)["active"] and "# Guidance\n\nEstablish" in body
    sequence = [role for role, _, _ in calls if role in {"technique_author", "technique_critic"}]
    assert sequence == (["technique_author", "technique_critic", "technique_author"] if recommendations
                        else ["technique_author", "technique_critic"])
