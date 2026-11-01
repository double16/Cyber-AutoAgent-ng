"""Incremental source decisions and exact requirement context survive research rounds."""

import json
from types import SimpleNamespace

import pytest

from modules.techniques.coverage import (
    EvidenceExcerpt,
    EvidenceSupport,
    Requirement,
    SourceAssessment,
    review_coverage,
    select_sources,
)
from modules.techniques.events import TechniqueEvents

REQUIREMENTS = [Requirement(id="method", description="Discriminate the mechanism")]


def document(index, text=None):
    return {"url": f"https://vendor.example/{index}", "text": text or f"Evidence {index}"}


def harness():
    state = SimpleNamespace(rejected=set(), evidence=None, invalid=False, contradiction=False, calls=[], events=[])
    events = TechniqueEvents()
    events.callback = state.events.append

    def generate(role, prompt, schema, tools=None):
        state.calls.append((role, prompt))
        if schema is SourceAssessment:
            url = json.loads(prompt.split("Current document URL: ", 1)[1].split("\n", 1)[0])["url"]
            accepted = url not in state.rejected
            return schema(selected_urls=[url] if accepted else [],
                          attributions=[{"url": url, "reason": "Technical authority"}] if accepted else [],
                          reason="Technical authority" if accepted else "Unrelated content",
                          authoritative_sufficient=accepted)
        assert schema is EvidenceSupport
        source = json.loads(prompt.split("Validated source assessment: ", 1)[1].split("\n", 1)[0])
        urls = source["selected_urls"]
        return schema(requirement_id="unknown" if state.invalid else "method", source_urls=urls,
                      explanation="Evidence remains conditional" if state.contradiction else "Combined factual basis",
                      gaps=["Unresolved contradiction"] if state.contradiction or not urls else [],
                      evidence=state.evidence or [])

    state.service = SimpleNamespace(_json=generate, events=events, _safe_feedback=str,
                                    _repair_prompt=lambda prompt, error: prompt + "\nCorrection: " + str(error))
    return state


@pytest.mark.parametrize("subject", ["HTTP request controls", "TCP protocol mapping", "filesystem source review"])
@pytest.mark.parametrize("order", [[1, 2], [2, 1]])
def test_complementary_sources_survive_rounds_and_review(subject, order):
    state = harness()
    first = document(order[0], 'def check(a, b):\n    return a, b  # \\ Unicode λ')
    prior = select_sources(state.service, subject, REQUIREMENTS, [first])
    state.contradiction = True
    result = select_sources(state.service, subject, REQUIREMENTS, [document(order[1])], prior, "Resolve gap")
    assert set(result.selected_urls) == {document(i)["url"] for i in order}
    assert not result.sufficient and result.coverage[0].gaps
    assert prior.sufficient and len(prior.selected_urls) == 1
    reviewed = review_coverage(state.service, subject, REQUIREMENTS, result)
    assert reviewed._decisions == result._decisions
    assert reviewed._evidence == result._evidence
    assert not reviewed.sufficient
    assert any(row.text == first["text"] for row in reviewed._evidence["method"])
    assert "Resolve gap" in state.calls[-2][1]
    assert "_decisions" not in result.model_dump_json() and '"evidence"' not in result.model_dump_json()


def test_rejection_is_recorded_without_erasing_prior_evidence():
    state = harness()
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    state.rejected.add(document(2)["url"])
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(2)], prior)
    assert result.sufficient and result.selected_urls == prior.selected_urls
    assert result._decisions[document(2)["url"]].selected_urls == []
    event = state.events[-1]
    assert event["source_decision"] == "rejected" and event["reason"] == "Unrelated content"
    assert event["source_url"] == document(2)["url"]
    assert "Evidence 2" not in state.calls[-1][1].split('"passages": ', 1)[1].split("\n", 1)[0]


def test_explicit_reassessment_replaces_same_url_and_authority():
    state = harness()
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    state.rejected.add(document(1)["url"])
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(1, "Unrelated")], prior)
    assert not result.sufficient and not result.authoritative_sufficient and not result.selected_urls
    assert prior.sufficient
    assert not result._evidence["method"]


@pytest.mark.parametrize("defect", ["text", "url", "requirement"])
def test_failed_correction_rolls_back_all_private_state(defect):
    state = harness()
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    frozen = prior.model_copy(deep=True)
    state.evidence = [{"url": document(2)["url"], "text": "Fabricated, quote"}]
    if defect == "url":
        state.evidence[0] = {"url": "https://unknown.example", "text": "Evidence 2"}
    elif defect == "requirement":
        state.evidence = []
        state.invalid = True
    with pytest.raises(ValueError, match="after one correction"):
        select_sources(state.service, "Method", REQUIREMENTS, [document(2)], prior)
    assert prior == frozen and prior._documents == frozen._documents
    assert prior._decisions == frozen._decisions and prior._evidence == frozen._evidence
    assert len([role for role, _ in state.calls if role == "technique_requirement_assessor"]) == 3


def test_exact_excerpts_and_legacy_context_are_retained():
    state = harness()
    text = 'Header\n  return a, b  # λ\\n\nFooter'
    state.evidence = [{"url": document(1)["url"], "text": '  return a, b  # λ\\n'}]
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(1, text)])
    assert result._evidence["method"][0].text == state.evidence[0]["text"]
    state.evidence = None
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(2)], result)
    assert result._evidence["method"][0].text == '  return a, b  # λ\\n'
    assert result._evidence["method"][1].text == "Evidence 2"


def test_source_limit_fails_without_discarding_prior_state():
    state = harness()
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(i) for i in range(18)])
    assert len(result.selected_urls) == 18
    with pytest.raises(ValueError, match="limit exceeded"):
        select_sources(state.service, "Method", REQUIREMENTS, [document(18)], result)
    assert len(result.selected_urls) == len(result._decisions) == 18


def test_review_invalid_evidence_preserves_state():
    state = harness()
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    state.evidence = [{"url": document(1)["url"], "text": "Fabricated"}]
    assert review_coverage(state.service, "Method", REQUIREMENTS, prior) is prior


def test_new_source_assessor_cannot_reselect_an_older_url():
    state = harness()
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    generate = state.service._json

    def stale(role, prompt, schema, tools=None):
        if schema is SourceAssessment:
            return prior._decisions[document(1)["url"]]
        return generate(role, prompt, schema)

    state.service._json = stale
    with pytest.raises(ValueError, match="not fetched"):
        select_sources(state.service, "Method", REQUIREMENTS, [document(2)], prior)


def test_research_review_cache_keeps_new_source_state(scripted_service, monkeypatch):
    import modules.techniques.service as service_module

    state = harness()
    state.contradiction = True
    state.rejected = {document(2)["url"], document(3)["url"]}
    service = scripted_service.service
    service._json = state.service._json
    batches = iter([[document(i)] for i in [1, 2, 3]])
    service.research = lambda request: next(batches)
    reviewed = []
    original = service_module.review_coverage

    def review(*args, **kwargs):
        result = original(*args, **kwargs)
        reviewed.append(result)
        return result

    monkeypatch.setattr(service_module, "review_coverage", review)
    with pytest.raises(ValueError, match="exhausted"):
        service._research_coverage("Method", REQUIREMENTS)
    assert [len(row._decisions) for row in reviewed] == [1, 2, 3]
    assert set(reviewed[-1]._documents) == {document(i)["url"] for i in [1, 2, 3]}


def test_source_decisions_reach_cli_diagnostics_without_evidence_text():
    import io

    from modules.techniques.diagnostics import DiagnosticSession

    stream = io.StringIO()
    session = DiagnosticSession(stderr=stream)
    session.lifecycle({"type": "technique_build_progress", "stage": "source_selection",
                       "status": "source_assessed", "source_url": document(1)["url"],
                       "source_decision": "rejected", "reason": "Unrelated content"})
    output = stream.getvalue()
    assert "source_decision=rejected" in output and document(1)["url"] in output
    assert "Unrelated content" in output


def test_complementary_source_can_close_a_prior_gap():
    state = harness()
    state.contradiction = True
    prior = select_sources(state.service, "Method", REQUIREMENTS, [document(1)])
    assert not prior.sufficient
    state.contradiction = False
    result = select_sources(state.service, "Method", REQUIREMENTS, [document(2)], prior)
    assert result.sufficient and len(result.coverage[0].source_urls) == 2
    assert prior.coverage[0].gaps and not result.coverage[0].gaps


def test_assessor_can_inspect_beyond_initial_excerpt():
    state = harness()
    original = state.service._json
    text = "x" * 6500 + "Exact late technical evidence"
    source = document(1, text)

    def generate(role, prompt, schema, tools=None):
        result = original(role, prompt, schema, tools)
        if schema is EvidenceSupport:
            assert tools
            passage = tools[0](url=source["url"], start=6500, length=100)
            result.evidence = [EvidenceExcerpt(url=source["url"], text=passage["text"])]
        return result

    state.service._json = generate
    result = select_sources(state.service, "HTTP request controls", REQUIREMENTS, [source])
    assert result._documents[source["url"]]["text"] == text
    assert result._evidence["method"][0].text == "Exact late technical evidence"


@pytest.mark.parametrize("subject", ["HTTP upload controls", "TCP protocol mapping", "filesystem source review"])
def test_long_source_correction_retains_inspection_without_reopening_tools(subject):
    state = harness()
    original = state.service._json
    source = document(1, "x" * 6500 + "Late mechanism evidence")
    attempts = []

    def generate(role, prompt, schema, tools=None):
        result = original(role, prompt, schema, tools)
        if schema is EvidenceSupport:
            attempts.append(prompt)
            if len(attempts) == 1:
                passage = tools[0](url=source["url"], start=6500, length=100)
                assert passage["text"] == "Late mechanism evidence"
                result.requirement_id = "wrong"
            else:
                assert not tools
                assert "Late mechanism evidence" in prompt
                result.evidence = [EvidenceExcerpt(url=source["url"], text="Late mechanism evidence")]
        return result

    state.service._json = generate
    result = select_sources(state.service, subject, REQUIREMENTS, [source])
    assert len(attempts) == 2
    assert result.sufficient
    assert any(event.get("status") == "source_inspected" for event in state.events)


@pytest.mark.parametrize("duplicate", [True, False])
def test_multiple_exact_passages_from_one_source(duplicate):
    state = harness()
    source = document(1, "First fact. Second fact.")
    first = {"url": source["url"], "text": "First fact."}
    second = first if duplicate else {"url": source["url"], "text": "Second fact."}
    state.evidence = [first, second]
    result = select_sources(state.service, "Method", REQUIREMENTS, [source])
    assert [row.text for row in result._evidence["method"]] == (
        ["First fact."] if duplicate else ["First fact.", "Second fact."]
    )
    reviewed = review_coverage(state.service, "Method", REQUIREMENTS, result)
    assert reviewed._evidence == result._evidence


def test_rejected_source_correction_explains_empty_attribution_contract(scripted_service):
    import json

    from modules.techniques.coverage import Requirement, select_sources

    service = scripted_service.service
    url = "https://example.com/rejected"
    prompts = []
    original = service.runner

    def run(role, prompt, tools):
        if role == "technique_source_assessor":
            prompts.append(prompt)
            return json.dumps({
                "selected_urls": [], "attributions": [{"url": url, "reason": "Unrelated mechanism"}]
                if len(prompts) == 1 else [],
                "reason": "Unrelated mechanism", "authoritative_sufficient": False,
            })
        if role == "technique_requirement_assessor":
            return json.dumps({"requirement_id": "method", "source_urls": [],
                               "explanation": "Restriction preserved without factual source claims", "gaps": []})
        return original(role, prompt, tools)

    service.runner = run
    result = select_sources(service, "HTTP controls", [
        Requirement(id="method", description="Preserve restrictions", kind="constraint"),
    ], [{"url": url, "text": "Unrelated mechanism"}])
    assert result.selected_urls == result.attributions == []
    assert len(prompts) == 2
    assert "selected_urls=[] and attributions=[]" in prompts[0]
    assert "contributes at least one relevant" in prompts[0]
    assert "not exact request-word matching" in prompts[0]
    assert "A source can contribute one relevant technical fact" in prompts[0]
    assert "Expected exactly 0 attribution records" in prompts[1]
    assert "Rejected sources must have no attribution records" in prompts[1]
    assert url in prompts[1]
