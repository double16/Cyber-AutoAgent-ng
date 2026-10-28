"""Atomic per-requirement source assessment contracts."""

from types import SimpleNamespace

import pytest

from modules.techniques.coverage import Requirement, SourceAssessment, Support, _assess_records
from modules.techniques.events import TechniqueEvents


@pytest.mark.parametrize("recover", [True, False])
def test_atomic_requirement_correction(recover):
    calls = []
    url = "https://vendor.example/advisory"
    requirements = [Requirement(id=f"r{i}", description=f"Requirement {i}") for i in range(7)]
    attempts = {}

    def generate(role, prompt, schema):
        calls.append((role, prompt))
        if schema is SourceAssessment:
            return schema(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                          reason="Primary", authoritative_sufficient=True)
        assert schema is Support
        identifier = next(r.id for r in requirements if f'"id":"{r.id}"' in prompt)
        attempts[identifier] = attempts.get(identifier, 0) + 1
        if identifier == "r6" and (not recover or attempts[identifier] == 1):
            identifier = "unknown"
        return schema(requirement_id=identifier, source_urls=[url], explanation="Documented basis", gaps=[])

    service = SimpleNamespace(_json=generate, events=TechniqueEvents(),
                              _safe_feedback=str,
                              _repair_prompt=lambda prompt, error: prompt + "\nCorrection: " + str(error))
    if recover:
        result = _assess_records(service, "Source text", requirements, [{"url": url}])
        assert [row.requirement_id for row in result.coverage] == [r.id for r in requirements]
    else:
        with pytest.raises(ValueError, match="after one correction"):
            _assess_records(service, "Source text", requirements, [{"url": url}])
    assert attempts == {**dict.fromkeys([f"r{i}" for i in range(6)], 1), "r6": 2}


@pytest.mark.parametrize("defect", ["unknown_url", "duplicate_url", "missing_attribution", "blank", "extra_coverage"])
@pytest.mark.parametrize("recover", [False, True])
def test_source_correction_is_bounded_and_preserves_prior_selection(scripted_service, defect, recover):
    from conftest import assessment_output

    from modules.techniques.coverage import SourceSelection, select_sources

    service = scripted_service.service
    url = scripted_service.source_urls[0]
    data = {"selected_urls": [url], "attributions": [{"url": url, "reason": "Prior authority"}],
            "reason": "Prior authority", "authoritative_sufficient": True,
            "coverage": [{"requirement_id": "method", "source_urls": [url],
                          "explanation": "Prior factual basis", "gaps": []}]}
    prior = SourceSelection.model_validate(data)
    frozen = prior.model_dump_json()
    calls = []
    events = []
    service.events.callback = events.append

    def generate(role, prompt, tools):
        if role != "technique_source_assessor":
            return assessment_output(role, prompt, data)
        calls.append(prompt)
        payload = {key: value for key, value in data.items() if key != "coverage"}
        if not recover or len(calls) == 1:
            if defect == "unknown_url":
                payload["selected_urls"] = ["https://unknown.example"]
            elif defect == "duplicate_url":
                payload["selected_urls"] = [url, url]
            elif defect == "missing_attribution":
                payload["attributions"] = []
            elif defect == "blank":
                payload["reason"] = ""
            else:
                payload["coverage"] = data["coverage"]
        import json
        return json.dumps(payload)

    service.runner = generate
    if recover:
        result = select_sources(service, "Method", [Requirement(id="method", description="Method")],
                                [{"url": url, "text": "Primary facts"}], prior)
        assert result.sufficient
    else:
        with pytest.raises(ValueError, match="after one correction"):
            select_sources(service, "Method", [Requirement(id="method", description="Method")],
                           [{"url": url, "text": "Primary facts"}], prior)
    assert prior.model_dump_json() == frozen and len(calls) == 2
    if not recover:
        warning = next(event for event in events if event["type"] == "technique_warning")
        assert warning["retained_assessment"] is True and warning["correction_attempt"] == 1
        assert warning["source_url"] == url and warning["assessment_substage"] == "technique_source_assessor"
    assert "Prior factual basis" in calls[1] and "Primary facts" in calls[1]


@pytest.mark.parametrize("review", [False, True])
def test_assessment_uses_only_assigned_prior_requirement_and_keeps_review_sources(scripted_service, review):
    import json

    from modules.techniques.coverage import SourceSelection

    url = scripted_service.source_urls[0]
    requirements = [Requirement(id="one", description="First"), Requirement(id="two", description="Second")]
    prior = SourceSelection(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                            reason="Authority retained", authoritative_sufficient=True,
                            coverage=[Support(requirement_id=r.id, source_urls=[url],
                                              explanation=f"Prior basis {r.id}", gaps=[]) for r in requirements])
    frozen = prior.model_dump_json()
    prompts = []

    def generate(role, prompt, tools):
        if role == "technique_source_assessor":
            # Drop the previous source: old coverage must be reassessed rather than copied.
            return json.dumps({"selected_urls": [], "attributions": [], "reason": "No applicable source",
                               "authoritative_sufficient": False})
        prompts.append(prompt)
        assert "one concise paragraph" in prompt and "1500-character schema limit" in prompt
        assert "do not inventory every source fact" in prompt
        requirement = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
        identifier = requirement["id"]
        assert f"Prior basis {identifier}" in prompt
        assert f"Prior basis {'two' if identifier == 'one' else 'one'}" not in prompt
        if review:
            return prior.coverage[len(prompts) - 1].model_dump_json()
        assert url in prompt.split("Removed source URLs: ", 1)[1]
        return json.dumps({"requirement_id": identifier, "source_urls": [], "explanation": "Support removed",
                           "gaps": ["No remaining support"]})

    scripted_service.service.runner = generate
    result = _assess_records(scripted_service.service, "Source text", requirements,
                             [{"url": url}], prior, review=review)
    assert prior.model_dump_json() == frozen and len(prompts) == 2
    if review:
        assert result.selected_urls == prior.selected_urls and result.attributions == prior.attributions
        assert result.reason == prior.reason and result.authoritative_sufficient == prior.authoritative_sufficient
    else:
        assert not result.sufficient and all(row.gaps and not row.source_urls for row in result.coverage)



def test_invalid_late_review_preserves_every_prior_record(scripted_service):
    import json

    from modules.techniques.coverage import SourceSelection, review_coverage

    service = scripted_service.service
    url = scripted_service.source_urls[0]
    requirements = [Requirement(id=f"r{i}", description=f"Requirement {i}") for i in range(7)]
    prior = SourceSelection(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                            reason="Primary authority", authoritative_sufficient=True,
                            coverage=[Support(requirement_id=r.id, source_urls=[url],
                                              explanation="Prior basis", gaps=["Missing facts"]) for r in requirements])
    frozen = prior.model_dump_json()
    calls = []

    def generate(role, prompt, tools):
        assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])["id"]
        calls.append(assigned)
        # Earlier reviews resolve gaps, but an invalid last review cannot commit any of them.
        return json.dumps({"requirement_id": "unknown" if assigned == "r6" else assigned,
                           "source_urls": [url], "explanation": "Reviewed factual basis", "gaps": []})

    service.runner = generate
    assert review_coverage(service, "Method", requirements, prior) is prior
    assert prior.model_dump_json() == frozen
    assert calls == [r.id for r in requirements] + ["r6"]
