"""Atomic per-requirement source assessment contracts."""

from types import SimpleNamespace

import pytest
from conftest import support_record

from modules.techniques.coverage import AssessedSupport as EvidenceSupport
from modules.techniques.coverage import Requirement, SourceAssessment, Support, _assess_records
from modules.techniques.events import TechniqueEvents


@pytest.mark.parametrize("recover", [True, False])
def test_atomic_requirement_correction(recover):
    calls = []
    url = "https://vendor.example/advisory"
    requirements = [Requirement(grounding="source_required", id=f"r{i}", description=f"Requirement {i}") for i in range(7)]
    attempts = {}

    def generate(role, prompt, schema, tools=None):
        calls.append((role, prompt))
        if schema is SourceAssessment:
            return schema(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                          reason="Primary", authoritative_sufficient=True)
        assert schema is EvidenceSupport
        identifier = next(r.id for r in requirements if f'"id":"{r.id}"' in prompt)
        attempts[identifier] = attempts.get(identifier, 0) + 1
        if identifier == "r6" and (not recover or attempts[identifier] == 1):
            identifier = "unknown"
        return schema(
            **support_record(
                dict(requirement_id=identifier, source_urls=[url], explanation="Documented basis", gaps=[])
            )
        )

    service = SimpleNamespace(_json=generate, events=TechniqueEvents(),
                              _safe_feedback=str,
                              _repair_prompt=lambda prompt, error: prompt + "\nCorrection: " + str(error))
    if recover:
        result = _assess_records(service, "Source text", requirements, [{"url": url, "text": "Source text"}])
        assert [row.requirement_id for row in result.coverage] == [r.id for r in requirements]
    else:
        with pytest.raises(ValueError, match="after one correction"):
            _assess_records(service, "Source text", requirements, [{"url": url, "text": "Source text"}])
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

        return json.dumps(support_record(payload))

    service.runner = generate
    if recover:
        result = select_sources(service, "Method", [Requirement(grounding="source_required", id="method", description="Method")],
                                [{"url": url, "text": "Primary facts"}], prior)
        assert result.sufficient
    else:
        with pytest.raises(ValueError, match="after one correction"):
            select_sources(service, "Method", [Requirement(grounding="source_required", id="method", description="Method")],
                           [{"url": url, "text": "Primary facts"}], prior)
    assert prior.model_dump_json() == frozen and len(calls) == 2
    if not recover:
        warning = next(event for event in events if event["type"] == "technique_warning")
        assert warning["retained_assessment"] is True and warning["correction_attempt"] == 1
        assert warning["source_url"] == url and warning["assessment_substage"] == "technique_source_assessor"
    assert "Primary facts" in calls[1]
    if defect != "extra_coverage":
        assert "Prior factual basis" not in calls[1]


@pytest.mark.parametrize("review", [False, True])
def test_assessment_uses_only_assigned_prior_requirement_and_keeps_review_sources(scripted_service, review):
    import json

    from modules.techniques.coverage import SourceSelection

    url = scripted_service.source_urls[0]
    requirements = [Requirement(grounding="source_required", id="one", description="First"), Requirement(grounding="source_required", id="two", description="Second")]
    prior = SourceSelection(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                            reason="Authority retained", authoritative_sufficient=True,
                            coverage=[Support(requirement_id=r.id, source_urls=[url],
                                              explanation=f"Prior basis {r.id}", gaps=[]) for r in requirements])
    frozen = prior.model_dump_json()
    prompts = []

    def generate(role, prompt, tools):
        if role == "technique_source_assessor":
            # Drop the previous source: old coverage must be reassessed rather than copied.
            return json.dumps(
                support_record(
                    {
                        "selected_urls": [],
                        "attributions": [],
                        "reason": "No applicable source",
                        "authoritative_sufficient": False,
                    }
                )
            )
        prompts.append(prompt)
        assert "one concise paragraph" in prompt and "1500-character schema limit" in prompt
        assert "do not inventory every source fact" in prompt
        requirement = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
        identifier = requirement["id"]
        assert f"Prior basis {identifier}" in prompt
        assert f"Prior basis {'two' if identifier == 'one' else 'one'}" not in prompt
        if review:
            return json.dumps(support_record(prior.coverage[len(prompts) - 1].model_dump()))
        removed = json.loads(prompt.split("Removed source URLs: ", 1)[1].split("\n", 1)[0])
        assert removed == {"removed_source_urls": ["https://vendor.example/advisory"]}
        return json.dumps(
            support_record(
                {
                    "requirement_id": identifier,
                    "source_urls": [],
                    "explanation": "Support removed",
                    "gaps": ["No remaining support"],
                }
            )
        )

    scripted_service.service.runner = generate
    result = _assess_records(scripted_service.service, "Source text", requirements,
                             [{"url": url, "text": "Source text"}], prior, review=review)
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
    requirements = [Requirement(grounding="source_required", id=f"r{i}", description=f"Requirement {i}") for i in range(7)]
    prior = SourceSelection(selected_urls=[url], attributions=[{"url": url, "reason": "Primary"}],
                            reason="Primary authority", authoritative_sufficient=True,
                            coverage=[Support(requirement_id=r.id, source_urls=[url],
                                              explanation="Prior basis", gaps=["Missing facts"]) for r in requirements])
    prior._documents = {url: {"url": url, "text": "Source text"}}
    frozen = prior.model_dump_json()
    calls = []

    def generate(role, prompt, tools):
        assigned = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])["id"]
        calls.append(assigned)
        # Earlier reviews resolve gaps, but an invalid last review cannot commit any of them.
        return json.dumps(
            support_record(
                {
                    "requirement_id": "unknown" if assigned == "r6" else assigned,
                    "source_urls": [url],
                    "explanation": "Reviewed factual basis",
                    "gaps": [],
                }
            )
        )

    service.runner = generate
    assert review_coverage(service, "Method", requirements, prior) is prior
    assert prior.model_dump_json() == frozen
    assert calls == [r.id for r in requirements] + ["r6"]


@pytest.mark.parametrize("method", ["HTTP controls", "TCP service review", "filesystem analysis"])
@pytest.mark.parametrize("supported", [True, False])
def test_research_uses_typed_scope_and_preserves_authored_obligations(scripted_service, method, supported):
    import json

    service = scripted_service.service
    url = scripted_service.source_urls[0]
    requirements = [
        Requirement(grounding="source_required", id="authored", description="Write illustrative examples and a complete checklist",
                    kind="authored_deliverable"),
        Requirement(grounding="knowledge_allowed", id="constraint", description="Keep unknown conditions as future checks", kind="constraint"),
        Requirement(grounding="source_required", id="facts", description="Establish mechanism prerequisites", kind="factual_support"),
    ]
    frozen = [r.model_dump_json() for r in requirements]
    assigned = []

    def run(role, prompt, tools):
        if role == "technique_source_assessor":
            return json.dumps(
                support_record(
                    {
                        "selected_urls": [url],
                        "attributions": [{"url": url, "reason": "Primary facts"}],
                        "reason": "Primary factual contribution",
                        "authoritative_sufficient": True,
                    }
                )
            )
        row = json.loads(prompt.split("Assigned requirement: ", 1)[1].split("\n", 1)[0])
        assigned.append(row)
        assert row["kind"] == ("authored_deliverable" if row["id"] == "authored" else "factual_support")
        if row["id"] == "authored":
            assert row["description"] == requirements[0].description
        else:
            assert row["description"] == requirements[2].description
        return json.dumps(
            support_record(
                {
                    "requirement_id": row["id"],
                    "source_urls": [url] if supported else [],
                    "explanation": "Mechanism facts support construction" if supported else "Missing mechanism",
                    "gaps": [] if supported else ["Underlying mechanism is unsupported"],
                }
            )
        )

    service.runner = run
    result = _assess_records(service, f"Request: {method}", requirements, [{"url": url, "text": "Mechanism facts"}])
    assert [r["id"] for r in assigned] == ["authored", "facts"]
    assert [r.model_dump_json() for r in requirements] == frozen
    assert result.sufficient is supported
    constraint = result.coverage[1]
    assert constraint.source_urls == constraint.gaps == []
    assert "not source-verified" in constraint.explanation
    if not supported:
        assert result.coverage[0].gaps and result.coverage[2].gaps


def test_authoring_passages_preserve_exact_evidence_and_bound_only_fallback_text():
    from modules.techniques.coverage import EvidenceExcerpt, SourceSelection

    url = "https://vendor.example/source"
    extra = "https://vendor.example/supplement"
    text = 'Unicode λ\n{"nested": ["code", {"punctuation": "a:b,c!"}]}\nprint("exact")'
    selected = SourceSelection(selected_urls=[url, extra], attributions=[], coverage=[],
                               reason="Selected", authoritative_sufficient=True)
    excerpt = EvidenceExcerpt(url=url, text=text)
    selected._evidence = {
        "r1": [excerpt, EvidenceExcerpt(url="https://rejected.example", text="Unselected")],
        "r2": [excerpt],
    }
    frozen = {key: [row.model_dump() for row in rows] for key, rows in selected._evidence.items()}
    documents = [{"url": url, "text": "Do not replace the exact evidence with the beginning"},
                 {"url": extra, "text": "x" * 7000}, {"url": extra, "text": ""},
                 {"url": "https://rejected.example", "text": "Excluded"}]
    passages = selected.authoring_passages(documents)
    assert passages == [{"url": url, "text": text}, {"url": extra, "text": "x" * 6000}]
    assert frozen == {key: [row.model_dump() for row in rows] for key, rows in selected._evidence.items()}


@pytest.fixture(autouse=True)
def source_required_fixture_requests(request):
    # These tests intentionally exercise mandatory retrieved-evidence workflows.
    if "scripted_service" in request.fixturenames:
        request.getfixturevalue("scripted_service").grounding = "source_required"
