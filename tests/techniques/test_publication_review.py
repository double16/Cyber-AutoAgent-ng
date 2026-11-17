from types import SimpleNamespace

import pytest

from modules.techniques.coverage import Requirement
from modules.techniques.publication_review import PublicationReview, ReviewUnavailable, assess


@pytest.mark.parametrize("invalid", [False, True])
def test_output_retry_and_no_assessor(scripted_service, invalid):
    calls = []

    def runner(role, prompt, schema, tools):
        calls.append(role)
        assert schema is PublicationReview
        if invalid or len(calls) == 1:
            raise ValueError("Invalid structured response")
        return PublicationReview(summary="Useful method", issues=[], suggestions=["Optional example"])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    args = (scripted_service.service, "Read comparison", "Read a file under established policy.",
            [Requirement(id="method", description="Read comparison")], [], [])
    if invalid:
        with pytest.raises(ReviewUnavailable):
            assess(*args)
    else:
        result = assess(*args)
        assert result.approved and result.suggestions
    assert calls == ["technique_critic"] * 2


@pytest.mark.parametrize("subject", [
    "HTTP object selector", "browser storage access", "reusable token",
    "network connectivity", "filesystem read access",
])
@pytest.mark.parametrize("defective", [False, True])
def test_material_issues_and_optional_suggestions(scripted_service, subject, defective):
    from modules.techniques.critique import ReviewSnapshot

    text = f"Establish policy before testing {subject}."
    reference = ReviewSnapshot(text).body_references[0]

    def runner(role, prompt, schema, tools):
        assert role == "technique_critic"
        assert text in str(prompt)
        return PublicationReview(summary="Concrete interpretation assessment", issues=[{
            "criterion_id": "core:interpretation",
            "problem": "Observation alone is labeled a violation", "material_impact": "Legitimate access is misclassified",
            "requested_correction": "Compare against established policy", "draft_references": [reference],
        }] if defective else [], suggestions=["Optional worked example"])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    result = assess(scripted_service.service, subject, text, [], [], [])
    assert result.approved is not defective
    if defective:
        assert result.issues[0].id == "issue:1"
        assert result.issues[0].draft_excerpt == text


@pytest.mark.parametrize("defect", ["criterion", "passage", "draft"])
def test_invalid_references_retry_without_approval(scripted_service, defect):
    count = 0

    def runner(role, prompt, schema, tools):
        nonlocal count
        count += 1
        return PublicationReview(summary="Invalid reference", issues=[{
                        "criterion_id": "unknown" if defect == "criterion" else "core:interpretation",
            "problem": "Invalid reference", "material_impact": "Cannot ground defect",
            "requested_correction": "Establish policy",
            "passage_ids": ["unknown"] if defect == "passage" else [],
            "draft_references": ["unknown"] if defect == "draft" else [],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    with pytest.raises(ReviewUnavailable):
        assess(scripted_service.service, "Read", "Read comparison", [], [], [])
    assert count == 2


def test_source_passages_and_requirement_ids_are_controller_owned(scripted_service):
    def runner(role, prompt, schema, tools):
        passage = tools[0](url="https://vendor.example/source", start=0, length=100)
        return PublicationReview(summary="Missing requested mechanism", issues=[{
            "criterion_id": "source:method",
            "problem": "Retained material does not support the asserted mechanism",
            "material_impact": "Cannot ground the advertised conclusion",
            "requested_correction": "Obtain factual support",
            "passage_ids": [passage["passage_id"]],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    result = assess(scripted_service.service, "Method", "Method body",
                    [Requirement(id="method", description="Requested mechanism", grounding="source_required")],
                    [{"url": "https://vendor.example/source", "text": "Bounded facts."}],
                    ["https://vendor.example/source"])
    assert not result.approved
    assert result.issues[0].requirement_ids == ["method"]
    assert result.issues[0].source_urls == ["https://vendor.example/source"]
    assert result.issues[0].passages[0].start == 0


def test_legacy_wire_response_is_not_accepted_in_production(scripted_service):
    class Runner:
        def structured(self, role, prompt, schema, tools):
            return schema.model_validate({"approved": True, "reason": "Legacy", "issues": []})

    scripted_service.service.runner = Runner()
    with pytest.raises(ReviewUnavailable):
        assess(scripted_service.service, "Method", "Body", [], [], [])


def test_short_draft_references_resolve_to_frozen_snapshot(scripted_service):
    from modules.techniques.critique import ReviewSnapshot

    text = "Unknown prerequisites need future checks."

    def runner(role, prompt, schema, tools):
        assert '"short_draft_references": {"r1":' in prompt
        return schema(summary="Contradictory interpretation", issues=[{
            "criterion_id": "core:interpretation", "problem": "Unknown treated as refuted",
            "material_impact": "Premature exclusion", "requested_correction": "Preserve the unknown state",
            "draft_references": ["r1"],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    review = assess(scripted_service.service, "Method", text, [], [], [])
    assert review.issues[0].draft_references == ReviewSnapshot(text).body_references
    assert review.issues[0].draft_excerpt == text


def test_visible_source_passages_have_controller_ids_without_a_tool_receipt(scripted_service):
    import json

    url = "https://vendor.example/source"
    text = "Concrete supported source facts. " * 200

    def runner(role, prompt, schema, tools):
        assert "cite source IDs in your issue.passage_ids" in str(prompt)
        assert "body placement is required only when explicitly requested" in str(prompt)
        passages = next(json.loads(line)["retained_source_passages"] for line in str(prompt).splitlines()
                        if line.startswith('{"retained_source_passages":'))
        assert len(passages) == 1
        passage = passages[0]
        assert passage["text"] == text[:2000]
        assert passage["start"] == 0 and passage["end"] == 2000
        return schema(summary="Attribution contradicts supplied source", issues=[{
            "criterion_id": "core:attribution", "problem": "Scope is overstated",
            "material_impact": "Unsupported attribution", "requested_correction": "Narrow attribution",
            "draft_references": ["r1"],
            "passage_ids": [passage["passage_id"]],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    result = assess(scripted_service.service, "Review attribution", "Method", [],
                    [{"url": url, "text": text}], [url])
    assert not result.approved
    assert result.issues[0].source_urls == [url]
    assert result.issues[0].passages[0].end == 2000


def test_production_publication_does_not_replay_authoring_context(scripted_service):
    state = scripted_service
    state.service.get("HTTP boundary comparison")
    author = next(p for role, p, _ in state.calls if role == "technique_author")
    critic = next(p for role, p, _ in state.calls if role == "technique_critic")
    assert '"existing_examples":' in author
    assert '"existing_examples":' not in critic
    assert '"source_passages":' not in critic
    assert '"retained_source_passages":' in critic
    assert critic.count("Authorship improvement goals") == 1


def test_publication_repair_keeps_visible_and_new_source_records_once(scripted_service):
    import json

    url = "https://vendor.example/source"
    text = "Prefix evidence. " + "a" * 2483 + "Later evidence. " + "b" * 4000
    calls = []

    def runner(role, prompt, schema, tools):
        calls.append(prompt)
        if len(calls) == 1:
            tools[0](url=url, start=2500, length=2000)
            raise ValueError("Invalid output contract")
        records = [json.loads(record) for record in prompt.required_review_records]
        sources = [row for row in records if "passage_id" in row]
        assert len(sources) == 2
        assert {row["start"] for row in sources} == {0, 2500}
        assert all(str(prompt).count(json.dumps(row, ensure_ascii=False)) == 1 for row in sources)
        return schema(summary="Comparison complete", issues=[])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    assert assess(scripted_service.service, "Review", "Method", [], [{"url": url, "text": text}], [url]).approved
    assert len(calls) == 2


@pytest.mark.parametrize("include_body", [False, True])
def test_repair_ownership_follows_frozen_source_fields(scripted_service, markdown, include_body):
    import json

    from modules.techniques.critique import ReviewSnapshot

    text = markdown()
    snapshot = ReviewSnapshot(text)
    reference = next(key for key, path in snapshot.paths.items()
                     if path == "metadata.sources[0].selection_reason")

    def runner(role, prompt, schema, tools):
        passages = next(json.loads(line)["retained_source_passages"] for line in str(prompt).splitlines()
                        if line.startswith('{"retained_source_passages":'))
        return schema(summary="Factual contradiction", issues=[{
            "criterion_id": "core:correctness", "problem": "Attribution overstates the retained source",
            "material_impact": "Misleading attribution", "requested_correction": "Correct source scope",
            "draft_references": [reference] + (snapshot.body_references[:1] if include_body else []),
            "passage_ids": [passages[0]["passage_id"]],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    url = "https://vendor.example/advisory"
    review = assess(scripted_service.service, "Method", text, [], [{"url": url, "text": "Source facts"}], [url])
    assert review.issues[0].category == ("authored_content" if include_body else "source_attribution")


def test_source_metadata_correctness_issue_does_not_reauthor_document(scripted_service, monkeypatch):
    import json

    service = scripted_service.service
    original = service._json
    calls = []

    def decide(role, prompt, schema, tools=None):
        calls.append(role)
        if role == "technique_critic" and calls.count(role) == 1:
            records = next(json.loads(line)["draft_references"] for line in str(prompt).splitlines()
                           if line.startswith('{"draft_references":'))
            reference = next(row["reference"] for row in records
                             if row["path"] == "metadata.sources[0].selection_reason")
            passages = next(json.loads(line)["retained_source_passages"] for line in str(prompt).splitlines()
                            if line.startswith('{"retained_source_passages":'))
            assert reference and passages
            return schema(summary="Clarify attribution", recommendations=["Explain source limits in the body"])
        return original(role, prompt, schema, tools)

    monkeypatch.setattr(service, "_json", decide)
    service.get("HTTP comparison methodology")
    assert calls.count("technique_author") == 2
    assert calls.count("technique_attribution_repair") == 0
    assert calls.count("technique_critic") == 1


@pytest.mark.parametrize("case_id", ["supported_attribution", "contradictory_attribution"])
def test_recorded_packet_decisions_preserve_source_repair_ownership(scripted_service, case_id):
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures/publication_source_packet_replay.json").read_text())
    case = next(row for row in data["cases"] if row["id"] == case_id)

    def runner(role, prompt, schema, tools):
        return schema.model_validate(case["review"])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    review = assess(scripted_service.service, "Compare a policy and configuration snapshot", case["markdown"], [],
                    case["documents"], case["selected_urls"])
    assert review.approved is case["approved"]
    if not case["approved"]:
        assert all(issue.category == "source_attribution" for issue in review.issues)
        assert all(issue.passages for issue in review.issues)
