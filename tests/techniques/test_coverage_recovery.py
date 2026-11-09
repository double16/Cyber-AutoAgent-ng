from conftest import plan_payload

"""Source selection recovers contract errors without rejecting authored synthesis."""

import copy
import json

import pytest
from conftest import assessment_output
from pydantic import ValidationError

from modules.techniques.coverage import Requirement, SourceSelection, select_sources

URL = "https://vendor.example/advisory"
REQUIREMENTS = [Requirement(grounding="source_required", id="method", description="Verification methodology", kind="authored_deliverable")]
DOCUMENTS = [{"url": URL, "title": "Advisory", "text": "Mechanism, fixed versions and prerequisites"}]


def supported(synthesis=""):
    return {"selected_urls": [URL], "attributions": [{"url": URL, "reason": "Mechanism and version authority"}],
            "coverage": [{"requirement_id": "method", "source_urls": [URL],
                "explanation": "Documented versions and prerequisites support constructing version checks, "
                               "controls and future evidence criteria without executing target tests.",
                "synthesis": synthesis, "gaps": []}],
            "reason": "Enough factual basis to author the method", "authoritative_sufficient": True}


@pytest.mark.parametrize("synthesis", [None, "", "Derive checks from documented prerequisites"])
def test_canonical_explanation_accepts_optional_synthesis(scripted_service, synthesis):
    payload = supported(synthesis)
    if synthesis is None:
        del payload["coverage"][0]["synthesis"]
    scripted_service.service.runner = lambda role, prompt, tools: assessment_output(role, prompt, payload)
    selected = select_sources(scripted_service.service, "Method", REQUIREMENTS, DOCUMENTS)
    assert selected.sufficient
    assert "constructing version checks" in selected.coverage[0].explanation


@pytest.mark.parametrize("entrypoint", ["direct", "cli"])
@pytest.mark.parametrize("subject", ["HTTP CVE verification", "TCP protocol verification", "source/filesystem review"])
def test_latest_failure_reaches_authoring_after_one_batch(scripted_service, monkeypatch, capsys, entrypoint, subject):
    from modules.techniques import cli

    state = scripted_service
    original = state.service.runner

    def run(role, prompt, tools):
        if role == "technique_planner":
            return json.dumps(plan_payload({"components": [{"id": "method", "request": subject, "purpose": subject,
                "request_excerpt": subject, "requirements": [row.model_dump() for row in REQUIREMENTS]}]}))
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            state.calls.append((role, prompt, tools))
            payload = supported()
            if entrypoint == "cli":
                payload["coverage"][0]["requirement_id"] = "requirement-1"
            return assessment_output(role, prompt, payload)
        return original(role, prompt, tools)

    state.service.runner = run
    if entrypoint == "direct":
        _, rendered = state.service.get(subject, requirements=REQUIREMENTS)
    else:
        monkeypatch.setattr(cli, "build_service", lambda *_a, **_k: state.service)
        assert cli.main([subject, "--provider", "ollama"]) == 0
        rendered = capsys.readouterr().out
    assert "# Guidance" in rendered and len(state.research_calls) == 1
    assert any(role == "technique_author" for role, _, _ in state.calls)
    assert not any(role == "technique_requirement_reviewer" for role, _, _ in state.calls)
    for role in ("technique_requirement_assessor", "technique_author", "technique_critic"):
        prompt = next(prompt for selected, prompt, _ in state.calls if selected == role)
        assert "explanation" in prompt


def invalid_payload(kind):
    data = supported()
    if kind == "schema":
        data["coverage"][0]["explanation"] = " \n\t "
    elif kind == "long_explanation":
        data["coverage"][0]["explanation"] = "x" * 1501
    elif kind == "references":
        data["coverage"][0]["requirement_id"] = "unknown"
    elif kind == "url":
        data["coverage"][0]["source_urls"] = ["https://unknown.example"]
    elif kind == "attribution":
        data["attributions"] = []
    elif kind == "coverage":
        data["coverage"][0]["source_urls"] = []
    return data


@pytest.mark.parametrize("kind", ["schema", "long_explanation", "references", "url", "attribution", "coverage", "json"])
@pytest.mark.parametrize("recover", [True, False])
def test_error_specific_correction_retains_context_and_is_bounded(scripted_service, kind, recover):
    service = scripted_service.service
    previous = SourceSelection.model_validate(supported("Prior validated synthesis"))
    original = previous.model_dump_json()
    prompts = []
    target = "technique_source_assessor" if kind == "attribution" else "technique_requirement_assessor"

    def run(role, prompt, tools):
        if role != target:
            return assessment_output(role, prompt, supported())
        prompts.append(prompt)
        if recover and len(prompts) == 2:
            return assessment_output(role, prompt, supported())
        return "not JSON" if kind == "json" else assessment_output_invalid(role, invalid_payload(kind))

    service.runner = run
    if recover:
        assert select_sources(service, "Requested method", REQUIREMENTS, DOCUMENTS, previous).sufficient
    else:
        with pytest.raises(ValueError, match="Source-selection validation failed after one correction"):
            select_sources(service, "Requested method", REQUIREMENTS, DOCUMENTS, previous)
    assert len(prompts) == 2
    repair = prompts[-1]
    assert "Requested method" in repair
    if kind != "attribution":
        assert "Prior validated synthesis" in repair
    assert "Mechanism, fixed versions and prerequisites" in repair
    assert previous.model_dump_json() == original
    assert "Return only the current output schema" in repair
    assert "Validation failure" in repair
    assert "do not return a complete coverage array" in repair
    if kind == "long_explanation":
        assert "string_too_long" in repair and "1500-character schema limit" in repair
        assert "x" * 1501 in repair


def assessment_output_invalid(role, data):
    if role == "technique_source_assessor":
        return json.dumps({key: value for key, value in data.items() if key != "coverage"})
    return json.dumps(data["coverage"][0])


def test_explanation_schema_and_whitespace_validation_agree():
    data = supported()
    data["coverage"][0]["explanation"] = " \t "
    with pytest.raises(ValidationError, match="explanation"):
        SourceSelection.model_validate(data)
    schema = SourceSelection.model_json_schema()["$defs"]["Support"]
    assert "explanation" in schema["required"] and "synthesis" not in schema["required"]
    assert schema["properties"]["explanation"]["pattern"] == r"\S"
    assert "canonical" in schema["properties"]["synthesis"]["description"]
    legacy = copy.deepcopy(supported())
    del legacy["coverage"][0]["synthesis"]
    assert SourceSelection.model_validate(legacy).coverage[0].synthesis == ""


def test_later_source_cannot_remove_prior_source(scripted_service):
    previous = SourceSelection.model_validate(supported())
    previous._documents = {URL: {"url": URL, "text": "Previous factual basis"}}
    new_url = "https://vendor.example/config"
    payload = supported()
    payload["selected_urls"] = [new_url]
    payload["attributions"] = [{"url": new_url, "reason": "Configuration authority"}]
    payload["coverage"][0]["source_urls"] = [URL, new_url]
    scripted_service.service.runner = lambda role, prompt, tools: assessment_output(role, prompt, payload)
    result = select_sources(scripted_service.service, "Method", REQUIREMENTS,
                            [{"url": new_url, "text": "Configuration evidence"}], previous)
    assert result.selected_urls == [URL, new_url]
    assert previous.selected_urls == [URL]


@pytest.fixture(autouse=True)
def source_required_fixture_requests(request):
    # These tests intentionally exercise mandatory retrieved-evidence workflows.
    if "scripted_service" in request.fixturenames:
        request.getfixturevalue("scripted_service").grounding = "source_required"
