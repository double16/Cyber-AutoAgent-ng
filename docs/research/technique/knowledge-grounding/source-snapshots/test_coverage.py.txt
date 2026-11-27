"""Coverage is semantic; source counts and successful fetches cannot replace it."""

import copy
import json

import pytest
from conftest import assessment_output

from modules.techniques.coverage import Requirement, SourceSelection, select_sources


def selection(urls, gaps=None):
    return {
        "selected_urls": urls,
        "attributions": [{"url": url, "reason": f"Supports method from {url}"} for url in urls],
        "coverage": [{"requirement_id": "method", "source_urls": urls,
                      "explanation": "Version-specific support", "gaps": gaps or []}],
        "reason": "Overall method coverage", "authoritative_sufficient": not gaps,
    }


def test_selection_supports_more_than_three_sources_and_retains_individual_attribution(scripted_service):
    state = scripted_service
    original = state.service.runner
    urls = [f"https://vendor.example/{number}" for number in range(4)]
    state.service.research = lambda request: [{"url": url, "title": url, "text": "support"} for url in urls]
    seen = []

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            if role == "technique_source_assessor":
                seen.append(prompt)
            return assessment_output(role, prompt, selection(urls[:len(seen)],
                                     ["Need other mechanisms"] if len(seen) < 4 else []))
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, _ = state.service.get("Composite mechanism")
    metadata = state.service.store.document(revision).metadata
    assert len(metadata.sources) == 4
    assert len({source.selection_reason for source in metadata.sources}) == 4
    assert "Overall method coverage" in metadata.source_coverage
    assert all(prompt.count(",support") <= 1 for prompt in seen)


@pytest.mark.parametrize("mutate,message", [
    (lambda row: row["selected_urls"].append(row["selected_urls"][0]), "Duplicate"),
    (lambda row: row["selected_urls"].append("https://unknown.example"), "not fetched"),
    (lambda row: row["attributions"].clear(), "own attribution"),
    (lambda row: row["coverage"].clear(), "every requirement"),
    (lambda row: row["coverage"][0].update(source_urls=["https://unknown.example"]), "unselected"),
    (lambda row: row["coverage"][0].update(source_urls=[]), "explicit gaps"),
])
def test_controller_validates_references_not_semantic_support(mutate, message):
    payload = selection(["https://vendor.example/doc"])
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        SourceSelection.model_validate(payload).validate_references(
            [{"url": "https://vendor.example/doc"}], [Requirement(grounding="source_required", id="method", description="Method")],
        )


def test_unsupported_requirements_with_gaps_are_valid_but_insufficient():
    row = SourceSelection.model_validate(selection([], ["No authoritative version support"]))
    row.validate_references([], [Requirement(grounding="source_required", id="method", description="Method")])
    assert not row.sufficient


def test_selection_repairs_invalid_references_once(scripted_service):
    state = scripted_service
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_source_assessor":
            prompts.append(prompt)
        url = "https://unknown.example" if len(prompts) == 1 else "https://vendor.example/doc"
        return assessment_output(role, prompt, selection([url]))

    state.service.runner = runner
    selected = select_sources(state.service, "Method", [Requirement(grounding="source_required", id="method", description="Method")],
                              [{"url": "https://vendor.example/doc", "text": "support"}])
    assert selected.sufficient
    assert "not fetched" in prompts[-1] and "current output schema" in prompts[-1]
    with pytest.raises(ValueError, match="any usable"):
        select_sources(state.service, "Method", [], [])


def test_targeted_research_follows_gaps_and_deduplicates_documents(scripted_service):
    state = scripted_service
    original = state.service.runner
    requests = []
    rounds = []
    state.service.events.callback = rounds.append

    class Research:
        def batch(self, request, excluded):
            requests.append((request, excluded))
            url = f"https://vendor.example/{len(requests)}"
            document = {"url": url, "title": "Version source", "text": "Version support"}
            return [document, copy.copy(document)]

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            urls = [f"https://vendor.example/{n}" for n in range(1, len(requests) + 1)]
            return assessment_output(role, prompt, selection(
                urls, ["Missing requested CVE version coverage"] if len(requests) == 1 else [],
            ))
        return original(role, prompt, tools)

    state.service.research = Research()
    state.service.runner = runner
    _, markdown = state.service.get("Requested CVE verification")
    assert len(requests) == 2
    assert "Missing requested CVE version coverage" in requests[1][0]
    assert requests[1][1] == {"https://vendor.example/1"}
    assert "Considered 2 fetched documents" in markdown
    assert {event["research_round"] for event in rounds if "research_round" in event} == {1, 2}


def test_contradictions_cannot_be_solved_by_three_documents(scripted_service):
    state = scripted_service
    original = state.service.runner

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            return assessment_output(role, prompt, selection(
                state.source_urls, ["Version sources contradict requested behavior"],
            ))
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="(?s)three batches.*contradict"):
        state.service.get("Version method")
    assert len(state.research_calls) == 3
    assert not any(role == "technique_author" for role, _, _ in state.calls)


@pytest.mark.parametrize("recover", [False, True])
def test_malformed_coverage_retry_has_parser_location_and_retains_previous_support(scripted_service, recover):
    """Reproduce the extra quote after gaps arrays in the 2026-10-27 CLI failure."""
    state = scripted_service
    urls = [f"https://vendor.example/{index}" for index in range(3)]
    first = selection(urls[:1], ["Need configuration prerequisites"])
    malformed = json.dumps(selection(urls[:2])["coverage"][0]).replace('"gaps": []}', '"gaps": []"}')
    prompts = []
    sources = []

    def run(role, prompt, tools):
        if role == "technique_source_assessor":
            sources.append(prompt)
            return assessment_output(role, prompt, selection(urls[:len(sources)]))
        if len(sources) == 2:
            prompts.append(prompt)
            if len(prompts) == 1 or not recover:
                return malformed
        return assessment_output(role, prompt, first if len(sources) == 1 else selection(urls[:len(sources)]))

    state.service.runner = run
    documents = [{"url": url, "text": "configuration prerequisites", "title": url} for url in urls]
    if recover:
        result = select_sources(state.service, "Prerequisite methodology",
                                [Requirement(grounding="source_required", id="method", description="Prerequisites")], documents)
        assert result.selected_urls == urls and len(sources) == 3
    else:
        with pytest.raises(ValueError, match="Source-selection validation failed.*Expecting ',' delimiter"):
            select_sources(state.service, "Prerequisite methodology",
                           [Requirement(grounding="source_required", id="method", description="Prerequisites")], documents)
        assert len(sources) == 2
    assert len(prompts) == 2 and malformed in prompts[1]
    assert "Expecting ',' delimiter" in prompts[1] and "column" in prompts[1]
    assert all("Need configuration prerequisites" in prompt for prompt in prompts)


@pytest.mark.parametrize("empty_round", [1, 2, 3])
def test_empty_batches_preserve_last_valid_assessment(scripted_service, empty_round):
    from modules.techniques.coverage import Requirement

    state = scripted_service
    original = state.service.runner
    batches = []
    assessments = []

    class Research:
        def batch(self, request, excluded):
            batches.append((request, excluded))
            if len(batches) == empty_round:
                return []
            return [{"url": f"https://vendor.example/{len(batches)}", "title": "Source", "text": "Support"}]

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            if role == "technique_source_assessor":
                assessments.append(prompt)
            urls = [f"https://vendor.example/{i}" for i in range(1, len(batches) + 1) if i != empty_round]
            return assessment_output(role, prompt, selection(urls, ["Missing configuration support"]))
        return original(role, prompt, tools)

    state.service.research = Research()
    state.service.runner = runner
    with pytest.raises(ValueError, match="three batches.*Required-source obligations ready",):
        state.service._research_coverage("Method", [Requirement(grounding="source_required", id="method", description="Method")])
    assert len(batches) == 3 and len(assessments) == 2
    if empty_round != 1:
        assert "Missing configuration support" in batches[-1][0]
        assert "https://vendor.example/1" in batches[-1][1]


def test_all_empty_batches_report_no_documents(scripted_service):
    from modules.techniques.coverage import Requirement

    state = scripted_service
    calls = []
    state.service.research = lambda request: calls.append(request) or []
    with pytest.raises(ValueError, match="any usable source documents after three batches"):
        state.service._research_coverage("Method", [Requirement(grounding="source_required", id="method", description="Method")])
    assert len(calls) == 3
    assert not state.calls


def test_complementary_sources_can_have_limitations_without_requirement_gaps():
    row = selection(["https://vendor.example/version", "https://vendor.example/config"])
    row["attributions"][0]["limitations"] = ["Does not describe configuration"]
    row["attributions"][1]["limitations"] = ["Does not enumerate versions"]
    assessed = SourceSelection.model_validate(row)
    assert assessed.sufficient
    assert "Required-source obligations ready: True" in assessed.summary()
    assessed.coverage[0].gaps = ["Unresolved contradiction about supported configuration"]
    assert not assessed.sufficient


def test_empty_source_batch_does_not_rejudge_previous_selection(scripted_service):
    previous = SourceSelection.model_validate(selection(["https://vendor.example/advisory"]))
    result = select_sources(scripted_service.service, "Method", [Requirement(grounding="source_required", id="method", description="Method")],
                            [], previous)
    assert result is previous and not scripted_service.calls


def test_complete_selection_reports_missing_unexpected_and_duplicate_identifiers():
    requirements = [Requirement(grounding="source_required", id=f"r{index}", description=f"Requirement {index}") for index in range(7)]
    payload = selection(["https://vendor.example/advisory"])
    payload["coverage"] = [dict(payload["coverage"][0], requirement_id=key) for key in ["r0", "r0", "unknown"]]
    with pytest.raises(ValueError) as caught:
        SourceSelection.model_validate(payload).validate_references(
            [{"url": "https://vendor.example/advisory"}], requirements,
        )
    assert "Missing IDs: ['r1', 'r2', 'r3', 'r4', 'r5', 'r6']" in str(caught.value)
    assert "unexpected IDs: ['unknown']" in str(caught.value)
    assert "duplicate IDs: ['r0']" in str(caught.value)


@pytest.fixture(autouse=True)
def source_required_fixture_requests(request):
    # These tests intentionally exercise mandatory retrieved-evidence workflows.
    if "scripted_service" in request.fixturenames:
        request.getfixturevalue("scripted_service").grounding = "source_required"
