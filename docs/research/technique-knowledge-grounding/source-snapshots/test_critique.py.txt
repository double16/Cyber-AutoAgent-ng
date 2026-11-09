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
            Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Defect",
                issues=[issue(**updates)],
            ),
            "Boundary guidance",
            REQS,
            [URL],
        )


@pytest.mark.parametrize("approved,issues", [(True, [issue()]), (False, [])])
def test_approval_and_issue_consistency(approved, issues):
    with pytest.raises(ValueError, match="Approval requires"):
        RetainedSources(DOCS).validate(
            Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=approved,
                reason="Review",
                issues=issues,
            ),
            "Boundary guidance",
            REQS,
            [URL],
        )


def test_duplicate_issue_ids_and_unselected_attribution():
    lookup = RetainedSources(DOCS)
    critique = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[issue(), issue()],
    )
    with pytest.raises(ValueError, match="unique"):
        lookup.validate(critique, "Boundary guidance", REQS, [URL])
    with pytest.raises(ValueError, match="affected selected"):
        lookup.validate(critique, "Boundary guidance", REQS, [])


def test_exact_returned_passage_and_missing_content_are_valid():
    lookup = RetainedSources(DOCS)
    passage = lookup.lookup(URL, 0, 4000)
    passage.pop("text")
    critique = Critique(no_security_checks_reason="No separately enumerated security checks in this scripted draft",
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
    lookup.validate(
        Critique(
            no_security_checks_reason="No separately enumerated security checks in this scripted draft",
            approved=True,
            reason="Complete",
        ),
        "Boundary guidance",
        REQS,
        [URL],
    )


@pytest.mark.parametrize("valid_second", [True, False])
def test_semantically_vague_or_unsupported_critique_has_one_correction(scripted_service, valid_second):
    service = scripted_service.service
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_critic":
            return json.dumps(
                Critique(
                    no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                    approved=False,
                    reason="Review",
                    issues=[issue()],
                ).model_dump()
            )
        assert role == "technique_critique_assessor"
        return json.dumps(
            {"approved": valid_second and len(calls) == 4,
             "reason": "Correction does not identify a concrete defect",
             "findings": [] if valid_second and len(calls) == 4 else [{
                 "problem": "Unsupported defect", "requested_correction": "Withdraw or substantiate",
                 "issue_ids": ["boundary"]}]}
        )

    service.runner = runner
    if valid_second:
        assert not assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    else:
        with pytest.raises(ValueError, match="Critique assessment failed after one correction"):
            assess(service, "Review", "Boundary guidance", REQS, DOCS, [URL])
    assert len(calls) == 4
    assert "Critique disagreement" in calls[2][1]
    assert not service.repository.revisions()


def test_invalid_critique_correction_consumes_no_author_attempt(scripted_service):
    state = scripted_service
    original = state.service.runner
    critics = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            return json.dumps({"approved": len(critics) > 1, "reason": "No issues",
                               "no_security_checks_reason": "No separately enumerated checks in scripted draft"})
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
    state.grounding = "source_required"
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
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Review",
                issues=issues,
            ).model_dump_json()
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
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
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
    state.grounding = "source_required"
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
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Review",
                issues=[issue(category=category, draft_excerpt="")],
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
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
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
    with pytest.raises(ValueError, match="Publication review unavailable"):
        state.service.get("Boundary guidance")
    event = next(row for row in events if row.get("stage") == "publication_review")
    assert event["author_attempts"] == 1
    snapshot = json.loads((tmp_path / event["snapshot_path"]).read_text())
    assert "Check assumptions and controls" in snapshot["response"]
    assert not state.service.repository.revisions()


def test_invalid_excerpt_correction_requests_literal_draft_text(scripted_service):
    calls = []

    def runner(role, prompt, tools):
        if role == "technique_critique_assessor":
            return json.dumps({"approved": True, "reason": "Complete"})
        calls.append(prompt)
        return Critique(no_security_checks_reason="No separately enumerated security checks in this scripted draft",
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
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Review",
                issues=[issue()],
            ).model_dump_json()
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
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Review",
                issues=[issue()],
            ).model_dump_json()
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
    candidate = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[issue(draft_references=[key])],
    )
    with pytest.raises(ValueError, match="stale"):
        lookup.validate(candidate, "Different document", REQS, [URL])


def test_source_absence_requires_complete_inspection():
    lookup = RetainedSources(DOCS)
    candidate = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[
            issue(
                absence_scope="entire_retained_source",
                absence_of_support="Claim absent",
            )
        ],
    )
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
    candidate = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[
            issue(
                draft_references=list(snapshot.values),
                passage_ids=[result["passage_id"]],
            )
        ],
    )
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
    candidate = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[
            issue(
                absence_scope="entire_retained_source",
                absence_of_support="Disputed claim absent",
            )
        ],
    )
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
    candidate = Critique(
        no_security_checks_reason="No separately enumerated security checks in this scripted draft",
        approved=False,
        reason="Defect",
        issues=[
            issue(
                category="research_coverage",
                source_urls=[],
                absence_scope="entire_retained_source",
            )
        ],
    )
    with pytest.raises(ValueError, match="affected source URLs"):
        RetainedSources(DOCS).validate(candidate, "Boundary guidance", REQS, [])


@pytest.mark.parametrize("native_failure", [False, True])
def test_controller_correction_retains_lookup_without_reopening_tools(scripted_service, native_failure):
    calls = []

    def runner(role, prompt, tools):
        if role == "technique_critique_assessor":
            return json.dumps({"approved": True, "reason": "Complete"})
        calls.append((prompt, tools))
        if len(calls) == 1:
            tools[0](url=URL, length=4000)
            if native_failure:
                error = ValueError("Invalid structured grounding")
                error.native_finalization_messages = []
                raise error
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Defect",
                issues=[
                    issue(
                        draft_references=["unknown"],
                    )
                ],
            ).model_dump_json()
        assert tools == []
        assert DOCS[0]["text"] in prompt
        assert "retained_lookup_passages" in prompt
        return Critique(
            no_security_checks_reason="No separately enumerated security checks in this scripted draft",
            approved=True,
            reason="No demonstrated defect",
        ).model_dump_json()

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
                return json.dumps({"approved": True, "reason": "No grounded defects", "issues": [],
                                   "no_security_checks_reason": "No security checks in scripted draft"})
            return json.dumps({"approved": False, "reason": "Attribution overstates support", "issues": [
                issue(id="source-disclaimer", draft_excerpt="", absence_of_support="").model_dump(),
            ]})
        return original(role, prompt, tools)

    state.service.runner = runner
    if persistent:
        with pytest.raises(ValueError, match="needs inspected source passages"):
            state.service.get("Boundary controls")
        assert not state.service.repository.revisions()
    else:
        state.service.get("Boundary controls")
    assert len(critics) == 2 and "needs inspected source passages" in critics[1]
    assert [role for role, _, _ in state.calls].count("technique_author") == 1
    assert not any(role == "technique_attribution_repair" for role, _, _ in state.calls)
    assert len(state.research_calls) == 1
    if persistent:
        failed = next(row for row in events if row.get("stage") == "publication_review")
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
            assert "reserved example domains" in prompt
            assert "expected legitimate behavior" in prompt
            if len(critics) == 1:
                return Critique(
                    no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                    approved=False,
                    reason="Incidental target detail",
                    issues=[
                        issue(
                            category="authored_content",
                            source_urls=[],
                            draft_excerpt=marker,
                            problem="Incidental operation host appears in reusable guidance",
                            requested_correction="Replace the incidental host with a generic subject",
                        )
                    ],
                ).model_dump_json()
            assert marker not in prompt
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=True,
                reason="Reusable guidance",
            ).model_dump_json()
        return original(role, prompt, tools)

    state.service.runner = runner
    revision, _ = state.service.get("JWT handling")
    assert marker not in state.service.store.render(revision)
    assert len(authors) == len(critics) == 2
    assert len(state.research_calls) == 1
    assert "https://vendor.example/advisory" in state.service.store.render(revision)


@pytest.mark.parametrize("text,query,terms", [
    ("A session token may use proprietary HTTP headers.", "header Authorization token", ["header", "token"]),
    ("A TCP service returns a challenge.", "tls TCP nonce", ["TCP"]),
    ('def read(path):\n    return "λ,;"\n', "PATH filename source", ["PATH"]),
    ("axb comes before literal a.b", "A.B missing", ["A.B"]),
])
def test_lookup_term_fallback_preserves_exact_passages_and_controller_references(text, query, terms):
    lookup = RetainedSources([{"url": URL, "text": text}])
    tool = lookup.tool()
    result = tool(url=URL, query=query, length=100)
    assert result["matched"] is False  # The literal phrase was absent.
    assert result["match_kind"] == "individual_terms"
    assert [row["term"] for row in result["term_matches"]] == terms
    assert sum(row["end"] - row["start"] for row in result["term_matches"]) <= 100
    for row in result["term_matches"]:
        assert row["text"] == text[row["start"]:row["end"]]
        assert (URL, row["start"], row["end"]) in lookup.references
        assert row["passage_id"] in lookup.passage_ids
    assert "never establish semantic support or absence" in tool.tool_spec["description"]


@pytest.mark.parametrize("length", [1, 4, 40])
def test_lookup_term_fallback_bounds_number_and_total_length(length):
    text = "one two three four five six seven eight nine"
    lookup = RetainedSources([{"url": URL, "text": text}])
    result = lookup.tool()(url=URL, query="ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE", length=length)
    assert len(result["term_matches"]) == min(4, length)
    assert sum(len(row["text"]) for row in result["term_matches"]) <= length


def test_lookup_miss_and_offset_search_do_not_invent_passages():
    lookup = RetainedSources([{"url": URL, "text": "token then other text"}])
    for query, start in [("absent missing", 0), ("TOKEN missing", 6),
                         ("absent1 absent2 absent3 absent4 absent5 absent6 absent7 absent8 token", 0)]:
        assert lookup.tool()(url=URL, query=query, start=start) == {"matched": False, "characters": 21}
    assert not lookup.references
    exact = lookup.tool()(url=URL, query="token", length=5)
    assert exact["text"] == "token" and "term_matches" not in exact


@pytest.mark.parametrize("text", [
    "Changing the object header and receiving the selected object proves IDOR.",
    "JavaScript reading browser storage proves an authentication vulnerability.",
    "Reusing a currently valid token must be rejected.",
    "Deliverable checklist is missing.",
])
@pytest.mark.parametrize("persistent", [False, True])
def test_approval_independently_challenged_and_exact_correction_reviewed(scripted_service, text, persistent):
    from modules.techniques.critique import ReviewSnapshot

    reference = next(iter(ReviewSnapshot(text).values))
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_critic":
            if len(calls) == 1 or persistent:
                return Critique(
                    no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                    approved=True,
                    reason="No defects",
                ).model_dump_json()
            assert "Clarify policy and controls" in prompt
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=False,
                reason="Unconditional claim",
                issues=[
                    issue(
                        category="authored_content",
                        draft_excerpt="",
                        draft_references=[reference],
                        requested_correction="Clarify policy and controls",
                    )
                ],
            ).model_dump_json()
        agreed = len(calls) == 4 and not persistent
        assert text in prompt and "critic_assessment" in prompt
        return json.dumps({"approved": agreed, "reason": "Independent semantic review", "findings": [] if agreed else [{
            "problem": "Unconditional interpretation or missing requested deliverable",
            "requested_correction": "Clarify policy and controls", "draft_references": [reference],
        }]})

    scripted_service.service.runner = runner
    if persistent:
        with pytest.raises(ValueError, match="after one correction"):
            assess(scripted_service.service, "Review", text, REQS, DOCS, [URL])
    else:
        assert not assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved
    assert len(calls) == 4


def test_interpretation_rejects_stale_references():
    from modules.techniques.critique import Interpretation

    candidate = Critique(approved=True, reason="Conditional policy", interpretations=[Interpretation(
        draft_references=["stale"], prerequisite_or_policy="Determine intended authorization",
        legitimate_outcome="Authorized object returned", suspected_violation="Unauthorized object returned",
        distinguishing_control="Compare authorized and unauthorized identities",
    )])
    with pytest.raises(ValueError, match="interpretation draft reference"):
        RetainedSources(DOCS).validate(candidate, "Conditional method", REQS, [URL])


@pytest.mark.parametrize("stale", [False, True])
def test_assessment_invalid_references_or_consistency_blocks_approval(scripted_service, stale):
    def runner(role, prompt, tools):
        if role == "technique_critic":
            return Critique(
                no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                approved=True,
                reason="No defects",
            ).model_dump_json()
        return json.dumps({"approved": False, "reason": "Disagreed", "findings": [{
            "problem": "Missing control", "requested_correction": "Add control",
            "draft_references": ["stale"] if stale else [],
        }] if stale else []})

    scripted_service.service.runner = runner
    with pytest.raises(ValueError, match="after one correction"):
        assess(scripted_service.service, "Review", "Conditional method", REQS, DOCS, [URL])


def test_unsupported_defect_withdrawn_is_independently_reviewed(scripted_service):
    original = scripted_service.service.runner
    critics = []
    reviews = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            return (
                Critique(
                    no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                    approved=False,
                    reason="Unsupported",
                    issues=[issue()],
                )
                if len(critics) == 1
                else Critique(
                    no_security_checks_reason="No separately enumerated security checks in this scripted draft",
                    approved=True,
                    reason="Withdrawn",
                )
            ).model_dump_json()
        if role == "technique_critique_assessor":
            reviews.append(prompt)
            return json.dumps({"approved": len(reviews) > 1, "reason": "Assess support", "findings": []
                               if len(reviews) > 1 else [{"problem": "Claim lacks evidence",
                                  "requested_correction": "Withdraw defect", "issue_ids": ["boundary"]}]})
        return original(role, prompt, tools)

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    assert len(critics) == len(reviews) == 2


@pytest.mark.parametrize("method,legitimate,violation", [
    ("header object selection", "An entitled identity receives its selected public object",
     "An identity receives an object the established ownership policy denies"),
    ("browser storage access", "Application JavaScript reads its own origin's storage",
     "An independently demonstrated injected script reads a protected token across the intended script boundary"),
    ("token reuse", "A reusable, unexpired bearer token succeeds again",
     "A token succeeds after established revocation or contrary to a documented single-use policy"),
])
@pytest.mark.parametrize("case", ["legitimate", "violation", "conditional", "distant_caveat"])
def test_interpretation_policy_and_controls_survive_independent_review(
    scripted_service, method, legitimate, violation, case,
):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    policy = "Establish the applicable authorization, origin or token lifecycle policy before interpreting results"
    text = f"Method: {method}. Legitimate: {legitimate}. Violation: {violation}. Policy: {policy}."
    if case == "conditional":
        text += " If policy is unknown, compare authorized controls and establish it before deciding."
    if case == "distant_caveat":
        text += " Procedure: legitimate behavior always proves violation. Later caveat: policy may vary."
    reference = next(iter(ReviewSnapshot(text).values))
    calls = []

    def runner(role, prompt, tools):
        calls.append(role)
        assert text in prompt
        interpretation = Interpretation(
            draft_references=[reference], prerequisite_or_policy=policy,
            legitimate_outcome=legitimate, suspected_violation=violation,
            distinguishing_control="Compare an entitled positive baseline with an otherwise equivalent denied case",
        )
        if role == "technique_critic":
            issues = [issue(category="authored_content", draft_excerpt="", draft_references=[reference],
                            problem="Procedure unconditionally labels legitimate behavior a violation",
                            requested_correction="Make interpretation conditional on the established policy")]
            return Critique(approved=case != "distant_caveat" or len(calls) == 1, reason="Interpret policy",
                            interpretations=[interpretation],
                            issues=issues if case == "distant_caveat" and len(calls) > 1 else []).model_dump_json()
        agreed = case != "distant_caveat" or len(calls) > 2
        return json.dumps({"approved": agreed, "reason": "Compare procedure and stated policy",
                           "interpretation_reviews": [{"interpretation_index": 0,
                               "draft_references": [reference], "permitted_counterexample": legitimate,
                               "actual_discriminator": text,
                               "counterexample_matches_check": case == "distant_caveat",
                               "control_excludes_counterexample": case != "distant_caveat",
                               "critic_correct": agreed}],
                           "findings": [] if agreed else [{"problem": "Distant caveat cannot fix the procedure",
                           "requested_correction": "Correct the unconditional procedure claim",
                           "draft_references": [reference]}]})

    scripted_service.service.runner = runner
    result = assess(scripted_service.service, "Review", text, REQS, DOCS, [URL])
    assert result.approved == (case != "distant_caveat")
    assert len(calls) == (4 if case == "distant_caveat" else 2)
    assert result.interpretations[0].legitimate_outcome == legitimate
    if result.issues:
        assert result.issues[0].category == "authored_content"


def test_assembled_assessor_requires_independent_outcome_derivation(scripted_service):
    original = scripted_service.service.runner
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_critique_assessor":
            prompts.append(prompt)
        return original(role, prompt, tools)

    scripted_service.service.runner = runner
    assess(scripted_service.service, "Review", "An unexpired reusable credential succeeds again", REQS, DOCS, [URL])
    assert len(prompts) == 1
    assert "First derive legitimate and prohibited outcomes" in prompts[0]
    assert "Calling a check a future hypothesis does not excuse misclassifying legitimate behavior" in prompts[0]
    assert "specify how to establish policy through documentation" in prompts[0]
    assert "a valid reusable credential can legitimately succeed repeatedly" in prompts[0]
    assert "reuse inside its valid window" in prompts[0]
    assert "freshness-limit event to have occurred" in prompts[0]


@pytest.mark.parametrize("recover", [False, True])
def test_missing_interpretation_assessment_requires_shared_correction(scripted_service, recover):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "An unexpired reusable credential succeeds again"
    reference = next(iter(ReviewSnapshot(text).values))
    calls = []

    def runner(role, prompt, tools):
        calls.append(role)
        if role == "technique_critic":
            interpretation = Interpretation(
                draft_references=[reference], prerequisite_or_policy="A valid reusable credential",
                legitimate_outcome="Repeated success", suspected_violation="No violation established",
                distinguishing_control="Test established expiry or revocation separately",
            )
            return Critique(approved=True, reason="Legitimate behavior", interpretations=[interpretation]
                            if recover and len(calls) > 1 else []).model_dump_json()
        return json.dumps({"approved": True, "reason": "Interpretation matches policy", "findings": [],
                           "interpretation_reviews": [{"interpretation_index": 0,
                               "draft_references": [reference],
                               "permitted_counterexample": "Unexpired reusable credential succeeds again",
                               "actual_discriminator": "",
                               "counterexample_matches_check": False, "control_excludes_counterexample": False,
                               "critic_correct": True}]})

    scripted_service.service.runner = runner
    if recover:
        assert assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved
        assert calls == ["technique_critic", "technique_critic", "technique_critique_assessor"]
    else:
        with pytest.raises(ValueError, match="after one correction"):
            assess(scripted_service.service, "Review", text, REQS, DOCS, [URL])
        assert calls == ["technique_critic", "technique_critic"]


def test_interpretation_scope_cannot_contradict_populated_checks(scripted_service):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    reference = next(iter(ReviewSnapshot("Check").values))
    candidate = Critique(approved=True, reason="Scope", no_security_checks_reason="No checks", interpretations=[
        Interpretation(draft_references=[reference], prerequisite_or_policy="Policy", legitimate_outcome="Allowed",
                       suspected_violation="Denied", distinguishing_control="Compare")])
    scripted_service.service.runner = lambda *args: candidate.model_dump_json()
    with pytest.raises(ValueError, match="mutually exclusive"):
        assess(scripted_service.service, "Review", "Check", REQS, DOCS, [URL])


def test_native_critique_schema_requires_interpretation_assessment():
    schema = Critique.model_json_schema()
    assert {"approved", "reason", "interpretations", "no_security_checks_reason"} <= set(schema["required"])
    assert "interpretations" in schema["properties"]
    assert "no_security_checks_reason" in schema["properties"]


def test_assessor_contract_repair_retains_its_own_passages_and_rejected_assessment(scripted_service):
    original = scripted_service.service.runner
    assessor_calls = []
    staged_calls = []
    original_json = scripted_service.service._json

    def capture_stage(role, prompt, schema, tools=None):
        if role == "technique_critique_assessor":
            staged_calls.append(prompt)
        return original_json(role, prompt, schema, tools)

    scripted_service.service._json = capture_stage

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return Critique(approved=True, reason="No disputed claims",
                            no_security_checks_reason="No separately enumerated checks in scripted draft").model_dump_json()
        if role == "technique_critique_assessor":
            assessor_calls.append(prompt)
            if len(assessor_calls) == 1:
                returned = tools[0](url=URL, start=0, length=20)
                assert returned["text"] == DOCS[0]["text"][:20]
                return json.dumps({"approved": False, "reason": "Bad reference", "findings": [{
                    "problem": "Unresolved policy", "requested_correction": "Clarify it",
                    "draft_references": ["stale-reference"],
                }]})
            assert tools == []
            assert DOCS[0]["text"][:20] in prompt
            assert "retained_lookup_passages" in prompt and "stale-reference" in prompt
            assert "Rejected response" in prompt
            return json.dumps({"approved": True, "reason": "Reference repaired; no defect", "findings": []})
        return original(role, prompt, tools)

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, "Review", "Boundary guidance", REQS, DOCS, [URL]).approved
    assert len(assessor_calls) == 2
    from modules.techniques.roles import DeferredReviewPrompt

    assert len(staged_calls) == 2
    assert all(isinstance(prompt, DeferredReviewPrompt) for prompt in staged_calls)
    assert all("No disputed claims" not in prompt.investigation for prompt in staged_calls)
    assert all("critic_supported" not in prompt.investigation for prompt in staged_calls)
    assert all("interpretation_reviews" not in prompt.investigation for prompt in staged_calls)
    assert all("Then compare your findings" not in prompt.investigation for prompt in staged_calls)
    assert all("No disputed claims" in prompt.comparison for prompt in staged_calls)
    assert all("critic_supported" in prompt.comparison for prompt in staged_calls)
    assert "stale-reference" not in staged_calls[1].investigation
    assert "stale-reference" in staged_calls[1].comparison


@pytest.mark.parametrize("defect", [
    "missing", "duplicate", "stale", "invented", "repaired", "contradiction", "counterexample_conflict",
    "empty_control", "excluded_counterexample", "valid",
])
def test_independent_counterexample_review_validates_coverage_references_and_agreement(scripted_service, defect):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "A valid reusable token succeeds again. Compare separately with established revocation."
    reference = next(iter(ReviewSnapshot(text).values))
    assessor_calls = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return Critique(approved=True, reason="Normal reuse distinguished", interpretations=[Interpretation(
                draft_references=[reference], prerequisite_or_policy="Unexpired reusable bearer credential",
                legitimate_outcome="Repeated success", suspected_violation="Success after established revocation",
                distinguishing_control="Compare established revocation with valid-token baseline",
            )]).model_dump_json()
        assessor_calls.append(prompt)
        row = {"interpretation_index": 0, "draft_references": ["stale" if defect == "stale" else reference],
               "permitted_counterexample": "Valid reusable credential succeeds again",
               "actual_discriminator": "" if defect == "empty_control" else "Invented policy-establishment step"
               if defect == "invented" or (defect == "repaired" and len(assessor_calls) == 1) else text,
               "counterexample_matches_check": defect in {"counterexample_conflict", "excluded_counterexample"},
               "control_excludes_counterexample": defect != "counterexample_conflict",
               "critic_correct": defect != "contradiction"}
        if defect == "invented" or (defect == "repaired" and len(assessor_calls) == 1):
            row["control_references"] = ["invented-control-reference"]
        return json.dumps({"approved": True, "reason": "Reviewed actual procedure", "findings": [],
                           "interpretation_reviews": [] if defect == "missing" else
                           [row, row] if defect == "duplicate" else [row]})

    scripted_service.service.runner = runner
    if defect in {"valid", "repaired", "excluded_counterexample"}:
        assert assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved
        assert len(assessor_calls) == (2 if defect == "repaired" else 1)
        if defect == "repaired":
            assert "current procedure-body records" in assessor_calls[1]
            assert "Invented policy-establishment step" in assessor_calls[1]
    else:
        with pytest.raises(ValueError, match="after one correction"):
            assess(scripted_service.service, "Review", text, REQS, DOCS, [URL])
        assert len(assessor_calls) == 2
        assert "Rejected response" in assessor_calls[1]
    assert "Construct a concrete permitted counterexample" in assessor_calls[0]
    assert "If no permitted scenario can satisfy both, say so" in assessor_calls[0]
    assert "counterexample_matches_check=false" in assessor_calls[0]
    assert "mere lack of ownership" in assessor_calls[0]


def test_native_assessor_schema_requires_counterexample_reviews():
    from modules.techniques.critique import IssueAssessment

    assert {"critic_supported", "reason", "interpretation_reviews", "findings"} <= set(
        IssueAssessment.model_json_schema()["required"]
    )
    schema = IssueAssessment.model_json_schema()
    assert next(iter(schema["properties"])) == "interpretation_reviews"
    review = schema["$defs"]["InterpretationReview"]["properties"]
    assert review["control_references"]["maxItems"] == 32
    assert "faithful paraphrase" in review["actual_discriminator"]["description"]
    assert "cited body control steps" in review["control_excludes_counterexample"]["description"]
    assert {"counterexample_matches_check", "control_excludes_counterexample"} <= set(
        schema["$defs"]["InterpretationReview"]["required"]
    )


@pytest.mark.parametrize("literal", ["Check assumptions and controls.", "True"])
def test_control_excerpts_resolve_only_text_from_mixed_decoded_metadata(scripted_service, markdown, literal):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = markdown()
    snapshot = ReviewSnapshot(text)
    references = [ref for ref, value in snapshot.values.items()
                  if isinstance(value, (bool, int)) or value == "Check assumptions and controls."]

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return Critique(approved=True, reason="Check policy", interpretations=[Interpretation(
                draft_references=references, prerequisite_or_policy="Establish policy",
                legitimate_outcome="Allowed outcome", suspected_violation="Denied outcome",
                distinguishing_control="Check assumptions and controls.",
            )]).model_dump_json()
        return json.dumps({"approved": True, "reason": "Actual control", "findings": [],
                           "interpretation_reviews": [{"interpretation_index": 0,
                               "draft_references": references, "permitted_counterexample": "Allowed outcome",
                               "actual_discriminator": literal, "critic_correct": True,
                               "control_references": [ref for ref in references if ref not in snapshot.body_references]
                               if literal == "True" else [],
                               "counterexample_matches_check": False,
                               "control_excludes_counterexample": True}]})

    scripted_service.service.runner = runner
    if literal == "True":
        with pytest.raises(ValueError, match="procedure-body records"):
            assess(scripted_service.service, "Review", text, REQS, DOCS, [URL])
    else:
        assert assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved


def test_research_receipt_is_deferred_without_losing_decoded_draft_references(markdown):
    import yaml

    from modules.techniques.critique import ReviewSnapshot

    text = markdown()
    metadata, body = text.split("---", 2)[1:]
    fields = yaml.safe_load(metadata)
    fields["source_coverage"] = "ARCHIVED_RESEARCH_READY is permission to draft, not procedural approval"
    snapshot = ReviewSnapshot("---\n" + yaml.safe_dump(fields) + "---" + body)
    subject = json.loads(snapshot.render(research_notes=False))["draft_references"]
    receipt = json.loads(snapshot.render(research_notes=True))["draft_references"]
    full = json.loads(snapshot.render())["draft_references"]
    assert len(receipt) == 1
    assert receipt[0]["path"] == "metadata.source_coverage"
    assert "ARCHIVED_RESEARCH_READY" not in json.dumps(subject)
    assert "Check assumptions and controls." in json.dumps(subject)
    assert {row["reference"]: row for row in subject + receipt} == {row["reference"]: row for row in full}


def test_retained_lookup_runtime_schema_advertises_the_enforced_page_bounds():
    lookup = RetainedSources([{**DOCS[0], "text": "x" * 5000}]).tool()
    properties = lookup.tool_spec["inputSchema"]["json"]["properties"]
    assert properties["start"]["minimum"] == 0
    assert properties["length"]["minimum"] == 1
    assert properties["length"]["maximum"] == 4000
    assert len(lookup(url=URL, length=4000)["text"]) == 4000
    with pytest.raises(ValueError, match="1 to 4000"):
        lookup(url=URL, length=4001)
    with pytest.raises(ValueError, match="nonnegative"):
        lookup(url=URL, start=-1)


@pytest.mark.parametrize("excerpt,indices", [
    ("  - Compare baseline.", [2, 5]),
    ("  - Compare baseline.\n  - Compare denied.", [5, 6]),
    ("Compare baseline.\n\nSecond check", [2, 3, 4]),
    ("Compare baseline.\nSecond check", []),
    ("not in the document", []), ("", []), (" \n ", []),
])
def test_control_quotes_resolve_exact_body_spans_and_repeated_origins(excerpt, indices):
    from modules.techniques.critique import ReviewSnapshot

    snapshot = ReviewSnapshot(
        "First check\n  - Compare baseline.\n\nSecond check\n  - Compare baseline.\n  - Compare denied."
    )
    prefix = "d" + snapshot.identifier[:12]
    assert snapshot.control_references(excerpt) == [f"{prefix}:b{index}" for index in indices]


def test_control_quote_resolution_handles_overlaps_unicode_and_empty_body(markdown):
    import yaml

    from modules.techniques.critique import ReviewSnapshot

    overlapping = ReviewSnapshot("a\na\na")
    assert overlapping.control_references("a\na") == overlapping.body_references
    unicode_snapshot = ReviewSnapshot("π\nαβ\nγ")
    assert unicode_snapshot.control_references("β\nγ") == unicode_snapshot.body_references[1:]
    assert ReviewSnapshot("").control_references("absent") == []
    metadata, body = markdown().split("---", 2)[1:]
    fields = yaml.safe_load(metadata)
    fields["purpose"] = "METADATA_ONLY_CONTROL"
    snapshot = ReviewSnapshot("---\n" + yaml.safe_dump(fields) + "---" + body)
    assert snapshot.control_references("METADATA_ONLY_CONTROL") == []


def test_controller_resolves_real_control_without_requiring_duplicate_model_ids(scripted_service):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "Observe selection.\nControl: compare known authorized and denied objects."
    snapshot = ReviewSnapshot(text)
    observation, control = snapshot.body_references
    events, calls = [], []
    scripted_service.service.events.callback = events.append

    def runner(role, prompt, tools):
        calls.append(role)
        if role == "technique_critic":
            return Critique(approved=True, reason="Conditional comparison", interpretations=[Interpretation(
                draft_references=[observation], prerequisite_or_policy="Known allowed and denied objects",
                legitimate_outcome="Allowed object selected", suspected_violation="Denied object data returned",
                distinguishing_control="Compare authorized and denied cases",
            )]).model_dump_json()
        return json.dumps({"critic_supported": True, "reason": "Actual excluding control", "findings": [],
                           "interpretation_reviews": [{"interpretation_index": 0,
                               "draft_references": [observation], "permitted_counterexample": "Allowed selection",
                               "actual_discriminator": snapshot.values[control], "critic_correct": True,
                               "counterexample_matches_check": True, "control_excludes_counterexample": True}]})

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved
    assert calls == ["technique_critic", "technique_critique_assessor"]
    resolved = next(event for event in events if event.get("status") == "control_references_resolved")
    assert resolved["provided_references"] == [observation]
    assert resolved["resolved_references"] == [control]
    final = next(event for event in events if event.get("status") == "assessed")
    assert final["assessment"]["interpretation_reviews"][0]["draft_references"] == [observation, control]


def test_quote_resolution_preserves_the_shared_allowance_for_semantic_critic_correction(scripted_service):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "Select an object.\nSuspected violation: selected object changes.\nControl: repeat the allowed selection."
    snapshot = ReviewSnapshot(text)
    heading, observation, control = snapshot.body_references
    calls, counts = [], {"critic": 0, "assessor": 0}

    def runner(role, prompt, tools):
        calls.append(role)
        if role == "technique_critic":
            counts["critic"] += 1
            corrected = counts["critic"] > 1
            if corrected:
                assert "Critique disagreement" in prompt
                assert "verbatim excerpt" not in prompt
            return Critique(approved=not corrected, reason="Interpret selection", interpretations=[Interpretation(
                draft_references=[heading, observation], prerequisite_or_policy="Allowed selector",
                legitimate_outcome="Allowed selection changes", suspected_violation="Selection changes",
                distinguishing_control="Repeats allowed access only",
            )], issues=[issue(category="authored_content", source_urls=[], requirement_ids=[], draft_excerpt="",
                              draft_references=[observation], problem="Normal selection is labeled a violation",
                              requested_correction="Require an established denial and compare denied object access")]
                            if corrected else []).model_dump_json()
        counts["assessor"] += 1
        corrected = counts["assessor"] > 1
        return json.dumps({"critic_supported": corrected, "reason": "Check the actual authorization distinction",
            "interpretation_reviews": [{"interpretation_index": 0, "draft_references": [observation],
                "permitted_counterexample": "An entitled caller changes its selected object",
                "counterexample_matches_check": True, "control_excludes_counterexample": False,
                "actual_discriminator": snapshot.values[control], "critic_correct": corrected}],
            "findings": [] if corrected else [{"problem": "Allowed selection satisfies the violation label",
                "requested_correction": "Require known unauthorized access, beyond selector influence",
                "draft_references": [observation]}]})

    scripted_service.service.runner = runner
    assert not assess(scripted_service.service, "Review", text, REQS, DOCS, [URL]).approved
    assert calls == ["technique_critic", "technique_critique_assessor"] * 2


@pytest.mark.parametrize("approved", [False, True])
def test_suggestions_do_not_change_blocking_decision(approved):
    review = Critique(approved=approved, reason="Reviewed material defects", issues=[] if approved else [
        issue(category="authored_content", source_urls=[])
    ], suggestions=[{"improvement": "An additional example could help", "requirement_ids": ["method"]}])
    RetainedSources([]).validate(review, "Boundary guidance", REQS, [])
    assert review.approved is approved
    assert len(review.suggestions) == 1


def test_unknown_suggestion_reference_is_rejected():
    review = Critique(approved=True, reason="No blockers", suggestions=[
        {"improvement": "Consider an example", "draft_references": ["stale:reference"]}
    ])
    with pytest.raises(ValueError, match="Suggestion references"):
        RetainedSources([]).validate(review, "Boundary guidance", REQS, [])


def test_new_findings_contract_derives_decision_without_a_model_vote():
    from modules.techniques.critique import CriticFindings

    schema = CriticFindings.model_json_schema()
    assert "approved" not in schema["properties"]
    findings = CriticFindings(reason="Only optional elaboration", suggestions=[{"improvement": "Add an example"}])
    assert findings.critique().approved
    findings.issues = [issue()]
    assert not findings.critique().approved
    assert len(findings.critique().suggestions) == 1


@pytest.mark.parametrize(
    "subject", ["HTTP access comparison", "Network access comparison", "Filesystem access comparison"],
)
def test_reference_correction_has_exact_catalog_and_preserves_defect(scripted_service, markdown, subject):
    from modules.techniques.critique import ReviewSnapshot

    document = markdown(title=subject)
    reference = next(iter(ReviewSnapshot(document).body_references))
    prompts = []
    original = scripted_service.service.runner

    def runner(role, prompt, tools):
        if role != "technique_critic":
            return original(role, prompt, tools)
        prompts.append(str(prompt))
        requirement_ids = ["method", "core:interpretation"] if len(prompts) == 1 else ["method"]
        return Critique(approved=False, reason="A material interpretation defect", issues=[issue(
            category="authored_content", source_urls=[], draft_references=[reference], draft_excerpt="",
            criterion_id="core:interpretation", requirement_ids=requirement_ids,
        )], no_security_checks_reason="No separately enumerated checks in this scripted example").model_dump_json()

    scripted_service.service.runner = runner
    result = assess(scripted_service.service, subject, document, REQS, DOCS, [URL])
    assert not result.approved
    assert result.issues[0].requirement_ids == ["method"]
    assert len(prompts) == 2
    assert '"allowed_values": ["method"]' in prompts[0]
    assert "Allowed requirement_ids values: ['method']" in prompts[1]
    assert "Acceptance-criterion IDs belong in criterion_id" in prompts[1]
    assert "requirement_ids=[]" in prompts[1]
    assert "Attribution overstates scope" in prompts[1]


def test_requirement_catalog_validates_values_without_prefix_heuristics(markdown):
    for identifier in ("core:custom-request", "requirement:original"):
        candidate = Critique(approved=False, reason="Scripted defect", issues=[issue(requirement_ids=[identifier])])
        RetainedSources(DOCS).validate(
            candidate, markdown()+"Boundary guidance", [Requirement(id=identifier, description="Method")], [URL],
        )


@pytest.mark.parametrize("subject", ["HTTP comparison", "network comparison", "filesystem comparison"])
@pytest.mark.parametrize("explicit_controls", [False, True])
def test_grounded_paraphrase_does_not_consume_correction(scripted_service, subject, explicit_controls):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "Observe the result.\nEstablish independent policy and compare allowed and denied cases."
    snapshot = ReviewSnapshot(text)
    observation, control = snapshot.body_references
    events, calls = [], []
    scripted_service.service.events.callback = events.append

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_critic":
            return Critique(approved=True, reason="Conditional comparison", interpretations=[Interpretation(
                draft_references=[observation, control], prerequisite_or_policy="Independent access policy",
                legitimate_outcome="Allowed access", suspected_violation="Denied access succeeds",
                distinguishing_control="Use independent policy to compare two controls",
            )]).model_dump_json()
        assert "control_references" in str(prompt)
        assert "VERBATIM" not in str(prompt)
        return json.dumps({"critic_supported": True, "reason": "Actual cited comparison", "findings": [],
                           "interpretation_reviews": [{"interpretation_index": 0,
                               "draft_references": [observation, control],
                               "control_references": [control] if explicit_controls else [],
                               "permitted_counterexample": "No permitted scenario satisfies denied access",
                               "actual_discriminator": "Independent policy defines the allowed/denied comparison.",
                               "counterexample_matches_check": False, "control_excludes_counterexample": True,
                               "critic_correct": True}]})

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, subject, text, REQS, DOCS, [URL]).approved
    assert len(calls) == 2
    event = next(row for row in events if row.get("status") == "control_references_resolved")
    assert any(row["reference"] == control and row["value"] == snapshot.values[control]
               for row in event["resolved_records"])
    assessed = next(row for row in events if row.get("status") == "assessed")
    assert assessed["correction_number"] == 0


def test_control_grounding_validates_availability_without_semantic_scoring(markdown):
    from modules.techniques.critique import ReviewSnapshot

    snapshot = ReviewSnapshot(markdown())
    body = snapshot.body_references[-1]
    metadata = next(ref for ref in snapshot.values if ref not in snapshot.body_references)
    assert snapshot.resolve_control("Faithful paraphrase", [body], []) == [body]
    assert snapshot.resolve_control("", [body], []) == []
    assert snapshot.resolve_control("", [], [body, body]) == [body]
    # Available but irrelevant references still require semantic assessment by the model.
    assert snapshot.resolve_control("A claim the cited text does not establish", [body], [body]) == [body]
    for invalid in (metadata, "stale-reference"):
        with pytest.raises(ValueError, match="procedure-body records"):
            snapshot.resolve_control("Control summary", [body], [invalid])
    with pytest.raises(ValueError, match="requires current procedure-body references"):
        snapshot.resolve_control("Unreferenced paraphrase", [metadata], [])


@pytest.mark.parametrize("purpose", ["prerequisite establishment", "legitimate allowed observation"])
def test_nonviolation_checks_need_no_invented_counterexample(scripted_service, purpose):
    from modules.techniques.critique import Interpretation, ReviewSnapshot

    text = "Establish file read policy and record allowed read outcomes."
    reference = ReviewSnapshot(text).body_references[0]
    prompts = []

    def runner(role, prompt, tools):
        prompts.append(str(prompt))
        if role == "technique_critic":
            return Critique(approved=True, reason="Accurate nonviolation check", interpretations=[Interpretation(
                draft_references=[reference], prerequisite_or_policy="Establish intended policy",
                legitimate_outcome=purpose, suspected_violation="No violation claimed by this step",
                distinguishing_control="Not applicable to this nonviolation step",
            )]).model_dump_json()
        return json.dumps({"critic_supported": True, "reason": "No boundary claim to refute", "findings": [],
            "interpretation_reviews": [{"interpretation_index": 0, "draft_references": [reference],
                "permitted_counterexample": "Not applicable; this step does not claim a violation",
                "counterexample_matches_check": False, "control_excludes_counterexample": False,
                "actual_discriminator": "", "critic_correct": True}]})

    scripted_service.service.runner = runner
    assert assess(scripted_service.service, "Review future file method", text, REQS, DOCS, [URL]).approved
    assert len(prompts) == 2
    assert "For prerequisite or legitimate-behavior checks" in prompts[1]
    assert "For EACH procedure" not in prompts[1]
    assert "counterexample judgments null" in prompts[1]
