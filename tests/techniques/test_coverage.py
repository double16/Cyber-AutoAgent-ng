"""Coverage is semantic; source counts and successful fetches cannot replace it."""

import copy
import json

import pytest

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
        if role == "technique_judge" and "selected_urls" in prompt:
            seen.append(prompt)
            return json.dumps(selection(urls[:len(seen)], ["Need other mechanisms"] if len(seen) < 4 else []))
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
            [{"url": "https://vendor.example/doc"}], [Requirement(id="method", description="Method")],
        )


def test_unsupported_requirements_with_gaps_are_valid_but_insufficient():
    row = SourceSelection.model_validate(selection([], ["No authoritative version support"]))
    row.validate_references([], [Requirement(id="method", description="Method")])
    assert not row.sufficient


def test_selection_repairs_invalid_references_once(scripted_service):
    state = scripted_service
    prompts = []

    def runner(role, prompt, tools):
        prompts.append(prompt)
        return json.dumps(selection(["https://unknown.example" if len(prompts) == 1 else "https://vendor.example/doc"]))

    state.service.runner = runner
    selected = select_sources(state.service, "Method", [Requirement(id="method", description="Method")],
                              [{"url": "https://vendor.example/doc", "text": "support"}])
    assert selected.sufficient
    assert "Repair invalid selection" in prompts[-1]
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
        if role == "technique_judge" and "selected_urls" in prompt:
            urls = [f"https://vendor.example/{n}" for n in range(1, len(requests) + 1)]
            return json.dumps(selection(urls, ["Missing requested CVE version coverage"] if len(requests) == 1 else []))
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
        if role == "technique_judge" and "selected_urls" in prompt:
            return json.dumps(selection(state.source_urls, ["Version sources contradict requested behavior"]))
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="(?s)three batches.*contradict"):
        state.service.get("Version method")
    assert len(state.research_calls) == 3
    assert not any(role == "technique_author" for role, _, _ in state.calls)
