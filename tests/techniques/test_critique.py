"""Critique provenance, semantic correction and attribution preservation regressions."""

import json

import pytest
from conftest import assessment_output, critique_result

from modules.techniques.coverage import Attribution, Requirement, SourceSelection, Support
from modules.techniques.critique import (
    AttributionReplacement,
    Critique,
    Issue,
    RetainedSources,
    assess,
    repair_attribution,
)

URL = "https://vendor.example/advisory"
DOCS = [{"url": URL, "title": "Primary", "text": "A protected boundary is required.", "retrieved_at": "2026-10-27"}]
REQS = [Requirement(id="method", description="Describe boundary controls")]


def issue(**updates):
    return Issue.model_validate(
        {
            "id": "boundary",
            "category": "source_attribution",
            "problem": "Attribution overstates scope",
            "requested_correction": "State that this source covers protected boundaries only",
            "source_urls": [URL],
            "requirement_ids": ["method"],
            "draft_excerpt": "Boundary guidance",
            "absence_of_support": "Source does not establish unconditional disclosure",
            **updates,
        }
    )


def selection():
    result = SourceSelection(
        selected_urls=[URL, "https://other.example/reference"],
        attributions=[
            Attribution(url=URL, reason="Original"),
            Attribution(url="https://other.example/reference", reason="Unchanged", limitations=["Version"]),
        ],
        coverage=[Support(requirement_id="method", source_urls=[URL], explanation="Supported", gaps=[])],
        reason="Authority assessment",
        authoritative_sufficient=True,
    )
    result.validate_references([*DOCS, {"url": "https://other.example/reference"}], REQS)
    return result


@pytest.mark.parametrize("start,length", [(-1, 2), (0, 0), (0, 4001), (500, 1), (True, 1), (0, True)])
def test_lookup_rejects_invalid_bounds(start, length):
    lookup = RetainedSources(DOCS)
    with pytest.raises(ValueError):
        lookup.lookup(URL, start, length)
    assert not lookup.references


def test_lookup_returns_controller_offsets_and_never_fetches():
    lookup = RetainedSources(DOCS)
    with pytest.raises(ValueError, match="Unknown"):
        lookup.lookup("https://unknown.example")
    assert lookup.lookup(URL, 2, 4000) == {
        "url": URL,
        "start": 2,
        "end": len(DOCS[0]["text"]),
        "text": DOCS[0]["text"][2:],
    }
    assert lookup.tool()(url=URL, start=0, length=1)["text"] == "A"
    assert len(lookup.references) == 2


@pytest.mark.parametrize(
    "updates,message",
    [
        ({"requirement_ids": ["unknown"]}, "unknown requirement"),
        ({"source_urls": ["https://unknown.example"]}, "unknown source"),
        ({"draft_excerpt": "Invented text"}, "not exact"),
        ({"draft_excerpt": "    Boundary guidance"}, "not exact"),
        ({"draft_excerpt": "", "requirement_ids": []}, "identify requirements"),
        ({"source_urls": []}, "affected selected"),
        ({"absence_of_support": ""}, "needs passages"),
        ({"passages": [{"url": URL, "start": 0, "end": 4}]}, "not returned"),
    ],
)
def test_invalid_issue_references(updates, message):
    lookup = RetainedSources(DOCS)
    with pytest.raises(ValueError, match=message):
        lookup.validate(
            Critique(approved=False, reason="Defect", issues=[issue(**updates)]), "Boundary guidance", REQS, [URL]
        )


@pytest.mark.parametrize("approved,issues", [(True, [issue()]), (False, [])])
def test_approval_and_issue_consistency(approved, issues):
    with pytest.raises(ValueError, match="Approval requires"):
        RetainedSources(DOCS).validate(
            Critique(approved=approved, reason="Review", issues=issues), "Boundary guidance", REQS, [URL]
        )


def test_duplicate_issue_ids_and_unselected_attribution():
    lookup = RetainedSources(DOCS)
    critique = Critique(approved=False, reason="Defect", issues=[issue(), issue()])
    with pytest.raises(ValueError, match="unique"):
        lookup.validate(critique, "Boundary guidance", REQS, [URL])
    with pytest.raises(ValueError, match="affected selected"):
        lookup.validate(critique, "Boundary guidance", REQS, [])


def test_exact_returned_passage_and_missing_content_are_valid():
    lookup = RetainedSources(DOCS)
    passage = lookup.lookup(URL, 0, 4000)
    passage.pop("text")
    critique = Critique(
        approved=False,
        reason="Defect",
        issues=[
            issue(
                passages=[passage],
                absence_of_support="",
                draft_excerpt="",
            )
        ],
    )
    lookup.validate(critique, "Boundary guidance", REQS, [URL])
    lookup.validate(Critique(approved=True, reason="Complete"), "Boundary guidance", REQS, [URL])


@pytest.mark.parametrize("valid_second", [True, False])
def test_semantically_vague_or_unsupported_critique_has_one_correction(scripted_service, valid_second):
    service = scripted_service.service
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_critic":
            return json.dumps(Critique(approved=False, reason="Review", issues=[issue()]).model_dump())
        assert role == "technique_critique_assessor"
        return json.dumps(
            {"approved": valid_second and len(calls) == 4, "reason": "Correction does not identify a concrete defect"}
        )

    service.runner = runner
    if valid_second:
        assert not assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    else:
        with pytest.raises(ValueError, match="Critique assessment failed after one correction"):
            assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL])
    assert len(calls) == 4
    assert "Critique is not actionable" in calls[2][1]
    assert not service.repository.revisions()


def test_invalid_critique_correction_consumes_no_author_attempt(scripted_service):
    state = scripted_service
    original = state.service.runner
    critics = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            return json.dumps({"approved": len(critics) > 1, "reason": "No issues"})
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.get("Boundary controls")
    assert len(critics) == 2
    assert [role for role, _, _ in state.calls].count("technique_author") == 1
    assert "rejection requires concrete" in critics[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"attributions": []},
        {"attributions": [{"url": URL, "reason": "Fixed"}] * 2},
        {"attributions": [{"url": "https://unknown.example", "reason": "Fixed"}]},
        {"attributions": [{"url": URL, "reason": "Fixed"}], "coverage": []},
    ],
)
def test_attribution_contract_correction_exhaustion_preserves_assessment(scripted_service, payload):
    service = scripted_service.service
    prior = selection()
    snapshot = prior.model_dump_json()
    calls = []

    def runner(role, prompt, tools):
        calls.append(prompt)
        assert role == "technique_attribution_repair" and tools == []
        return json.dumps(payload)

    service.runner = runner
    with pytest.raises(ValueError, match="Attribution repair invalid after one correction"):
        repair_attribution(service, prior, DOCS, [issue()], REQS)
    assert len(calls) == 2
    assert prior.model_dump_json() == snapshot


def test_attribution_repair_preserves_all_nonowning_fields(scripted_service):
    prior = selection()
    snapshot = prior.model_dump()
    service = scripted_service.service
    service.runner = lambda *_: json.dumps({"attributions": [{"url": URL, "reason": "Protected boundaries only"}]})
    result = repair_attribution(service, prior, DOCS, [issue()], REQS)
    assert result.attributions[0].reason == "Protected boundaries only"
    assert prior.model_dump() == snapshot
    assert result.coverage is prior.coverage
    assert result.attributions[1] is prior.attributions[1]
    assert result.selected_urls is prior.selected_urls
    assert result.reason == prior.reason
    assert result.authoritative_sufficient is prior.authoritative_sufficient
    assert DOCS[0]["retrieved_at"] == "2026-10-27"
    with pytest.raises(ValueError, match="omitted"):
        repair_attribution(service, prior, DOCS, [issue(source_urls=["https://unknown.example"])], REQS)


def test_attribution_contract_correction_then_success(scripted_service):
    replies = iter(
        [{"attributions": [{"url": "unknown", "reason": "Fix"}]}, {"attributions": [{"url": URL, "reason": "Correct"}]}]
    )
    scripted_service.service.runner = lambda *_: json.dumps(next(replies))
    assert (
        repair_attribution(scripted_service.service, selection(), DOCS, [issue()], REQS).attributions[0].reason
        == "Correct"
    )


def test_mixed_issues_replace_stale_author_and_attribution_requests(scripted_service):
    state = scripted_service
    original = state.service.runner
    critics = []
    events = []
    state.service.events.callback = events.append

    def research(_):
        url = f"https://vendor.example/source-{len(state.research_calls)}"
        state.research_calls.append(url)
        state.source_urls = [url]
        return [{"url": url, "title": "Primary", "text": "Boundary support"}]

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            if len(critics) > 1:
                return critique_result(True)
            issues = [
                issue(category=category, id=category, source_urls=state.source_urls, draft_excerpt="")
                for category in ["authored_content", "source_attribution", "research_coverage"]
            ]
            return Critique(approved=False, reason="Review", issues=issues).model_dump_json()
        return original(role, prompt, tools)

    state.service.research = research
    state.service.runner = runner
    state.service.get("Boundary controls")
    roles = [role for role, _, _ in state.calls]
    assert roles.count("technique_author") == 1
    assert "technique_attribution_repair" not in roles
    assert len(state.research_calls) == 2
    event = next(row for row in events if row.get("owning_stage"))
    assert event["owning_stage"] == "research_coverage"
    assert event["author_attempts"] == 1 and event["research_batches"] == 1
    assert event["source_selection_round"] == 1 and event["attribution_repairs"] == 0


def test_native_attribution_contract_schema_is_narrow():
    schema = AttributionReplacement.model_json_schema()
    assert set(schema["properties"]) == {"attributions"}
    assert schema["properties"]["attributions"]["maxItems"] == 1


def test_seven_requirements_three_drafts_and_incomplete_attribution_response_preserve_work(scripted_service):
    state = scripted_service
    original = state.service.runner
    requirements = [Requirement(id=f"r{index}", description=f"Boundary requirement {index}") for index in range(7)]
    authors, critics, repairs = [], [], []

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            result = json.loads(original(role, prompt, tools))
            result["coverage"] = [
                {"requirement_id": row.id, "source_urls": [URL], "explanation": "Primary boundary support", "gaps": []}
                for row in requirements
            ]
            return assessment_output(role, prompt, result)
        if role == "technique_author":
            authors.append(prompt)
        if role == "technique_critic":
            critics.append(prompt)
            if len(critics) == 4:
                return critique_result(True)
            category = "source_attribution" if len(critics) == 3 else "authored_content"
            return Critique(
                approved=False,
                reason="Boundary control",
                issues=[
                    issue(
                        category=category,
                        requirement_ids=["r0"],
                        draft_excerpt="",
                    )
                ],
            ).model_dump_json()
        if role == "technique_attribution_repair":
            repairs.append(prompt)
            if len(repairs) == 1:
                return json.dumps(
                    {"attributions": [{"url": URL, "reason": "Bounded"}], "coverage": [{"requirement_id": "r0"}]}
                )
        return original(role, prompt, tools)

    state.service.runner = runner
    _, markdown = state.service.get("Boundary guidance", requirements=requirements)
    assert len(authors) == 3 and len(critics) == 4 and len(repairs) == 2
    assert len(state.research_calls) == 1
    assert all(f"r{index}" in markdown for index in range(7))
    # The final assembled draft changes provenance only; authored Markdown remains byte-identical.
    assert [line.split(",body.line[", 1)[1] for line in critics[-1].splitlines() if ",body.line[" in line] == [
        line.split(",body.line[", 1)[1] for line in critics[-2].splitlines() if ",body.line[" in line
    ]
    assert "Primary method support" not in markdown


@pytest.mark.parametrize("changed", [True, False, "attribution_only"])
def test_research_only_resets_attribution_allowance_on_changed_validated_assessment(
    scripted_service, monkeypatch, changed
):
    state = scripted_service
    original = state.service.runner
    critics, repairs = [], []
    original_research = state.service._research_coverage

    def research(*args, **kwargs):
        if len(args) < 3:
            return original_research(*args, **kwargs)
        documents, retained, batch = args[2:5]
        if changed == "attribution_only":
            retained = retained.model_copy(
                update={
                    "attributions": [
                        Attribution(url=row.url, reason="Research changed attribution wording only")
                        for row in retained.attributions
                    ]
                }
            )
        elif changed:
            retained = retained.model_copy(update={"reason": "New validated research assessment"})
        return documents, retained, batch + 1

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            count = len(critics)
            if count > 3:
                return critique_result(True)
            category = "research_coverage" if count == 2 else "source_attribution"
            return Critique(
                approved=False, reason="Review", issues=[issue(category=category, draft_excerpt="")]
            ).model_dump_json()
        if role == "technique_attribution_repair":
            repairs.append(prompt)
        return original(role, prompt, tools)

    monkeypatch.setattr(state.service, "_research_coverage", research)
    state.service.runner = runner
    if changed is True:
        state.service.get("Boundary guidance")
        assert len(repairs) == 2
    else:
        with pytest.raises(ValueError, match="Source attribution unresolved after one repair"):
            state.service.get("Boundary guidance")
        assert len(repairs) == 1
    assert [role for role, _, _ in state.calls].count("technique_author") == 1


def test_attribution_contract_correction_allowance_is_shared_by_affected_sources(scripted_service):
    prior = selection()
    other = prior.selected_urls[1]
    replies = iter(
        [
            {"attributions": [{"url": "wrong", "reason": "Fix"}]},
            {"attributions": [{"url": URL, "reason": "Correct"}]},
            {"attributions": [{"url": "wrong-again", "reason": "Fix"}]},
        ]
    )
    calls = []

    def runner(role, prompt, tools):
        calls.append(prompt)
        return json.dumps(next(replies))

    scripted_service.service.runner = runner
    with pytest.raises(ValueError, match="after one correction"):
        repair_attribution(
            scripted_service.service,
            prior,
            [*DOCS, {"url": other, "text": "Other scope"}],
            [issue(source_urls=[URL, other])],
            REQS,
        )
    assert len(calls) == 3
    assert prior.attributions[0].reason == "Original"


def test_complete_validated_author_feedback_reaches_repair_without_diagnostic_truncation(scripted_service):
    state = scripted_service
    original = state.service.runner
    critics, authors = [], []
    correction = "Explain the protected boundary. " * 60 + "Use a negative control."

    def runner(role, prompt, tools):
        if role == "technique_author":
            authors.append(prompt)
        if role == "technique_critic":
            critics.append(prompt)
            if len(critics) > 1:
                return critique_result(True)
            return Critique(
                approved=False,
                reason="Review",
                issues=[
                    issue(
                        category="authored_content",
                        problem="Describe the trust boundary. " * 60,
                        requested_correction=correction,
                        draft_excerpt="",
                    )
                ],
            ).model_dump_json()
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.get("Boundary guidance")
    assert len(authors) == 2
    assert correction in authors[1]


def test_persistent_invalid_critique_retains_authored_draft_diagnostic(scripted_service, tmp_path):
    from modules.techniques.debug import TechniqueExports

    state = scripted_service
    original = state.service.runner
    state.service.exports = TechniqueExports(str(tmp_path))
    events = []
    state.service.events.callback = events.append
    state.service.runner = lambda role, prompt, tools: (
        json.dumps({"approved": False, "reason": "Vague"})
        if role == "technique_critic"
        else original(role, prompt, tools)
    )
    with pytest.raises(ValueError, match="Critique assessment failed"):
        state.service.get("Boundary guidance")
    event = next(row for row in events if row.get("stage") == "critique_assessment")
    assert event["author_attempts"] == 1
    snapshot = json.loads((tmp_path / event["snapshot_path"]).read_text())
    assert "Check assumptions and controls" in snapshot["response"]
    assert not state.service.repository.revisions()


def test_invalid_excerpt_correction_requests_literal_draft_text(scripted_service):
    calls = []

    def runner(role, prompt, tools):
        calls.append(prompt)
        return Critique(
            approved=len(calls) > 1,
            reason="Review",
            issues=[] if len(calls) > 1 else [issue(draft_excerpt="    Boundary guidance")],
        ).model_dump_json()

    scripted_service.service.runner = runner
    assert assess(
        scripted_service.service, "Review\nBoundary guidance", "Boundary guidance", REQS, DOCS, [URL]
    ).approved
    assert len(calls) == 2
    assert "draft_references" in calls[0]
    assert "quote literal Draft text with escapes" in calls[1]


@pytest.mark.parametrize("recover", [True, False])
def test_assessor_contract_correction_stays_with_assessor(scripted_service, recover):
    service = scripted_service.service
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt, tools))
        if role == "technique_critic":
            return Critique(approved=False, reason="Review", issues=[issue()]).model_dump_json()
        if len(calls) == 2 or not recover:
            return json.dumps({"auth-001": {"approved": True}})
        return json.dumps({"approved": True, "reason": "Concrete supported defect"})

    service.runner = runner
    if recover:
        assert not assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    else:
        with pytest.raises(ValueError, match="IssueAssessment contract failed"):
            assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL])
    assert [row[0] for row in calls] == [
        "technique_critic", "technique_critique_assessor", "technique_critique_assessor",
    ]
    assert not calls[-1][2]
    assert "Rejected response" in calls[-1][1]
    assert "Never use issue IDs as keys" in calls[-1][1]


@pytest.mark.parametrize("first_failure", ["critic_contract", "assessor_contract"])
def test_critique_and_assessor_share_one_correction(scripted_service, first_failure):
    service = scripted_service.service
    calls = []
    critic_calls = 0
    assessor_calls = 0

    def runner(role, prompt, tools):
        nonlocal critic_calls, assessor_calls
        calls.append(role)
        if role == "technique_critic":
            critic_calls += 1
            if first_failure == "critic_contract" and critic_calls == 1:
                return json.dumps({"decision": "reject"})
            return Critique(approved=False, reason="Review", issues=[issue()]).model_dump_json()
        assessor_calls += 1
        if first_failure == "critic_contract" or assessor_calls == 1:
            return json.dumps({"auth-001": {"approved": True}})
        return json.dumps({"approved": False, "reason": "Evidence does not establish the defect"})

    service.runner = runner
    with pytest.raises(ValueError, match="after one correction"):
        assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL])
    assert len(calls) == 3
    assert critic_calls == (2 if first_failure == "critic_contract" else 1)
    assert not service.repository.revisions()


def test_decoded_review_snapshot(markdown):
    from modules.techniques.critique import ReviewSnapshot

    snapshot = ReviewSnapshot(markdown().replace("title: JWT handling", "title: >\n  Multiple\n  lines"))
    key = next(key for key in snapshot.values if snapshot.paths[key] == "metadata.title")
    assert snapshot.values[key] == "Multiple lines"
    assert "title: >" not in snapshot.render()
    lookup = RetainedSources(DOCS)
    candidate = Critique(approved=False, reason="Defect", issues=[issue(draft_references=[key])])
    with pytest.raises(ValueError, match="stale"):
        lookup.validate(candidate, "Different document", REQS, [URL])


def test_source_absence_requires_complete_inspection():
    lookup = RetainedSources(DOCS)
    candidate = Critique(approved=False, reason="Defect", issues=[issue(
        absence_scope="entire_retained_source", absence_of_support="Claim absent",
    )])
    lookup.lookup(URL, 0, 5)
    with pytest.raises(ValueError, match="complete inspection"):
        lookup.validate(candidate, "Boundary guidance", REQS, [URL])
    lookup.lookup(URL, 5, 4000)
    lookup.validate(candidate, "Boundary guidance", REQS, [URL])


def test_reference_resolution_and_search():
    from modules.techniques.critique import ReviewSnapshot

    lookup = RetainedSources(DOCS)
    tool = lookup.tool()
    result = tool(url=URL, query="boundary", length=4000)
    assert result["characters"] == len(DOCS[0]["text"])
    assert result["next_start"] is None
    assert tool(url=URL, query="not present")["matched"] is False
    with pytest.raises(ValueError, match="Unknown"):
        tool(url="unknown", query="boundary")
    snapshot = ReviewSnapshot("Boundary guidance")
    candidate = Critique(approved=False, reason="Defect", issues=[issue(
        draft_references=list(snapshot.values), passage_ids=[result["passage_id"]],
    )])
    lookup.validate(candidate, "Boundary guidance", REQS, [URL])
    assert candidate.issues[0].draft_excerpt == "Boundary guidance"
    assert candidate.issues[0].passages[0].url == URL
    candidate.issues[0].passage_ids = ["unknown"]
    with pytest.raises(ValueError, match="Unknown source passage"):
        lookup.validate(candidate, "Boundary guidance", REQS, [URL])


@pytest.mark.parametrize("style,value", [
    ('"Escaped \\"quote\\""', 'Escaped "quote"'),
    ("|\n  First line\n  Second line", "First line\nSecond line"),
    (">\n  First line\n  Second line", "First line Second line"),
    ('"Unicode café"', "Unicode café"),
])
def test_snapshot_uses_existing_parser(markdown, style, value):
    from modules.techniques.critique import ReviewSnapshot

    document = markdown().replace("title: JWT handling", "title: " + style)
    snapshot = ReviewSnapshot(document)
    assert next(value for key, value in snapshot.values.items() if snapshot.paths[key] == "metadata.title") == value
    assert "---" not in snapshot.render()
    assert list(snapshot.values.values()).count("# Guidance") == 1


def test_full_inspection_rejects_gaps_and_accepts_overlaps():
    lookup = RetainedSources(DOCS)
    candidate = Critique(approved=False, reason="Defect", issues=[issue(
        absence_scope="entire_retained_source", absence_of_support="Disputed claim absent",
    )])
    lookup.lookup(URL, 0, 5)
    lookup.lookup(URL, 6, 4000)
    with pytest.raises(ValueError, match="complete inspection"):
        lookup.validate(candidate, "Boundary guidance", REQS, [URL])
    lookup.lookup(URL, 4, 4)
    lookup.validate(candidate, "Boundary guidance", REQS, [URL])


@pytest.mark.parametrize("start,length", [(-1, 10), (True, 10), (0, 0), (0, 4001)])
def test_search_validates_bounds_even_on_miss(start, length):
    with pytest.raises(ValueError, match="nonnegative"):
        RetainedSources(DOCS).tool()(url=URL, start=start, length=length, query="absent")


def test_entire_source_absence_requires_urls():
    candidate = Critique(approved=False, reason="Defect", issues=[issue(
        category="research_coverage", source_urls=[], absence_scope="entire_retained_source",
    )])
    with pytest.raises(ValueError, match="affected source URLs"):
        RetainedSources(DOCS).validate(candidate, "Boundary guidance", REQS, [])


@pytest.mark.parametrize("native_failure", [False, True])
def test_controller_correction_retains_lookup_without_reopening_tools(scripted_service, native_failure):
    calls = []

    def runner(role, prompt, tools):
        calls.append((prompt, tools))
        if len(calls) == 1:
            tools[0](url=URL, length=4000)
            if native_failure:
                error = ValueError("Invalid structured grounding")
                error.native_finalization_messages = []
                raise error
            return Critique(approved=False, reason="Defect", issues=[issue(
                draft_references=["unknown"],
            )]).model_dump_json()
        assert tools == []
        assert DOCS[0]["text"] in prompt
        assert "retained_lookup_passages" in prompt
        return Critique(approved=True, reason="No demonstrated defect").model_dump_json()

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    assert len(calls) == 2


@pytest.mark.parametrize("persistent", [False, True])
def test_grounding_complaint_without_evidence_cannot_trigger_repair(scripted_service, persistent):
    state = scripted_service
    original = state.service.runner
    critics = []
    events = []
    state.service.events.callback = events.append

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            if len(critics) == 2 and not persistent:
                return json.dumps({"approved": True, "reason": "No grounded defects", "issues": []})
            return json.dumps({"approved": False, "reason": "Attribution overstates support", "issues": [
                issue(id="source-disclaimer", draft_excerpt="", absence_of_support="").model_dump(),
            ]})
        return original(role, prompt, tools)

    state.service.runner = runner
    if persistent:
        with pytest.raises(ValueError, match="needs passages or an explanation of absent support"):
            state.service.get("Boundary controls")
        assert not state.service.repository.revisions()
    else:
        state.service.get("Boundary controls")
    assert len(critics) == 2 and "needs passages" in critics[1]
    assert [role for role, _, _ in state.calls].count("technique_author") == 1
    assert not any(role == "technique_attribution_repair" for role, _, _ in state.calls)
    assert len(state.research_calls) == 1
    if persistent:
        failed = next(row for row in events if row.get("stage") == "critique_assessment")
        assert failed["author_attempts"] == 1


@pytest.mark.parametrize("location", ["metadata", "body", "example"])
def test_target_detail_issue_repairs_authorship_without_research_loss(scripted_service, location):
    state = scripted_service
    original = state.service.runner
    authors = []
    critics = []
    marker = "assigned.internal"

    def runner(role, prompt, tools):
        if role == "technique_author":
            authors.append(prompt)
            payload = json.loads(original(role, prompt, tools))
            if len(authors) == 1:
                if location == "metadata":
                    payload["metadata"]["purpose"] += " on " + marker
                else:
                    payload["body"] += ("\nExample: fetch('https://" + marker + "')" if location == "example"
                                        else "\nApply to " + marker)
            return json.dumps(payload)
        if role == "technique_critic":
            critics.append(prompt)
            assert "decoded values" in prompt and "incidental target identifiers" in prompt
            if len(critics) == 1:
                return Critique(approved=False, reason="Incidental target detail", issues=[issue(
                    category="authored_content", source_urls=[], draft_excerpt=marker,
                    problem="Incidental operation host appears in reusable guidance",
                    requested_correction="Replace the incidental host with a generic subject",
                )]).model_dump_json()
            assert marker not in prompt
            return Critique(approved=True, reason="Reusable guidance").model_dump_json()
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, _ = state.service.get("JWT handling")
    assert marker not in state.service.store.render(revision)
    assert len(authors) == len(critics) == 2
    assert len(state.research_calls) == 1
    assert "https://vendor.example/advisory" in state.service.store.render(revision)
