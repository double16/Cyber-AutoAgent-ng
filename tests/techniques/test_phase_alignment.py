"""Phase objectives govern guidance, without claiming target execution or dropping intent."""

import json

import pytest
from conftest import assessment_output

from modules.techniques.alignment import PHASE_OBJECTIVES, align_request, phase_guidance
from modules.techniques.context import LookupContext
from modules.techniques.coverage import Attribution, CoverageGap, Requirement, SourceSelection, Support, review_coverage
from modules.techniques.documents import CONTEXTS


@pytest.mark.parametrize("phase", CONTEXTS)
@pytest.mark.parametrize("subject", ["HTTP application", "TCP service", "source repository", "system configuration",
                                    "challenge", "emulation telemetry"])
def test_explicit_phase_alignment_is_shared_and_context_is_not_mutated(scripted_service, phase, subject):
    state = scripted_service
    original = state.service.runner
    calls = []
    request = f"Review {subject} prerequisites"
    adjusted = f"Describe {phase} guidance for {subject} prerequisites"

    def runner(role, prompt, tools):
        if role == "technique_request_aligner":
            calls.append(prompt)
            return json.dumps({"adjusted_request": adjusted, "reason": "Preserve subject and clarify guidance"})
        return original(role, prompt, tools)

    state.service.runner = runner
    context = LookupContext(phase_contexts=[phase], methodology_constraints=["Read only"])
    alignment, aligned_context = align_request(state.service, request, context)
    same, _ = align_request(state.service, adjusted, aligned_context)
    assert same == alignment and len(calls) == 1
    assert context._request_alignment is None and aligned_context is not context
    assert alignment.original_request == request and alignment.adjusted_request == adjusted
    assert alignment.objectives == {phase: PHASE_OBJECTIVES[phase]}
    assert "Read only" in calls[0] and phase_guidance(context) in calls[0]
    assert "task objectives and execution context are not supplied to this workflow" in calls[0]
    assert "Original request" in alignment.render()


def test_unknown_phase_is_preserved_without_alignment_call(scripted_service):
    alignment, context = align_request(scripted_service.service, "Validate applicability", None)
    assert alignment.original_request == alignment.adjusted_request == "Validate applicability"
    assert alignment.render() == "" and context is None
    assert not scripted_service.calls
    assert "Phase unknown" in phase_guidance(None)
    with pytest.raises(ValueError, match="Unknown technique phase"):
        phase_guidance(LookupContext(phase_contexts=["invented"]))


def test_multiple_phase_objectives_are_deduplicated_without_ordering(scripted_service):
    context = LookupContext(phase_contexts=["analysis", "hypothesis_generation", "analysis"])
    alignment, _ = align_request(scripted_service.service, "Review source", context)
    assert alignment.phase_contexts == ["analysis", "hypothesis_generation"]
    assert set(alignment.objectives) == {"analysis", "hypothesis_generation"}
    prompt = scripted_service.calls[0][1]
    assert "no inferred ordering or automatic splitting" in prompt


@pytest.mark.parametrize("bad", ["not JSON", json.dumps({"adjusted_request": "", "reason": "Invalid"})])
def test_alignment_repairs_once_before_research(scripted_service, bad):
    calls = []
    scripted_service.service.runner = lambda *args: calls.append(args) or bad
    with pytest.raises(ValueError, match="alignment failed after one correction"):
        scripted_service.service.get("Review source", LookupContext(phase_contexts=["analysis"]))
    assert len(calls) == 2 and "Validation failure" in calls[1][1]
    assert not scripted_service.research_calls


@pytest.mark.parametrize("reference,excerpt,valid", [
    ("No execution", "Execute a probe", True),
    ("hypothesis_generation", "Execute a probe", True),
    ("invented restriction", "Execute a probe", False),
    ("No execution", "Not requested", False),
])
def test_explicit_deliverable_conflicts_are_referenced_and_not_rewritten(scripted_service, reference, excerpt, valid):
    calls = []
    output = {"adjusted_request": "Describe a probe", "reason": "Conflict", "conflict": {
        "requested_deliverable": "Execute a probe", "request_excerpt": excerpt,
        "restriction_reference": reference, "explanation": "Requested execution conflicts with no execution",
    }}
    scripted_service.service.runner = lambda *args: calls.append(args) or json.dumps(output)
    with pytest.raises(ValueError, match="alignment conflict" if valid else "alignment failed"):
        scripted_service.service.resolve_bundle("Execute a probe", LookupContext(
            phase_contexts=["hypothesis_generation"], methodology_constraints=["No execution"],
        ))
    assert len(calls) == (1 if valid else 2) and not scripted_service.research_calls


def coverage(gaps=None):
    return SourceSelection.model_validate({
        "selected_urls": ["https://vendor.example/advisory"],
        "attributions": [{"url": "https://vendor.example/advisory", "reason": "Mechanism and versions",
                          "limitations": ["No exhaustive deployment matrix"]}],
        "coverage": [{"requirement_id": "method", "source_urls": ["https://vendor.example/advisory"],
                      "explanation": "Mechanism and affected versions support hypotheses", "gaps": gaps or []}],
        "authoritative_sufficient": True, "reason": "Source-backed mechanism",
    })


@pytest.mark.parametrize("kind", ["missing_factual_support", "unresolved_contradiction"])
def test_typed_gaps_and_legacy_boundary(kind):
    typed = CoverageGap(kind=kind, description="Unresolved factual support", claim="Version support",
                        blocking_reason="Cannot define applicable versions", research_question="Which versions?")
    assert coverage([typed.model_dump()]).coverage[0].gaps == [typed]
    assert coverage(["Legacy gap"]).coverage[0].gaps[0].kind == "missing_factual_support"
    with pytest.raises(ValueError):
        CoverageGap(kind="target_not_tested", description="Not a factual gap")


def test_independent_review_reclassifies_future_checks_and_preserves_sources(scripted_service):
    selection = coverage(["Target version and exact payload not validated"])
    corrected = coverage()
    corrected.coverage[0].future_validation = ["Establish target version before testing"]
    scripted_service.service.runner = lambda role, prompt, tools: assessment_output(
        role, prompt, corrected.model_dump(),
    )
    result = review_coverage(scripted_service.service, "CVE hypothesis guidance",
                             [Requirement(id="method", description="Applicability hypotheses")], selection,
                             LookupContext(phase_contexts=["hypothesis_generation"]))
    assert result.sufficient and result.coverage[0].future_validation
    assert result.attributions == selection.attributions


@pytest.mark.parametrize("bad", ["invalid JSON", "changed_attribution", "unknown_url"])
def test_invalid_coverage_review_retains_valid_progress(scripted_service, bad):
    selection = coverage(["Missing mechanism support"])
    revised = coverage()
    if bad == "changed_attribution":
        revised.attributions[0].reason = "Invented replacement"
    elif bad == "unknown_url":
        revised.selected_urls = ["https://invented.example"]
    calls = []
    scripted_service.service.runner = lambda *args: calls.append(args) or (
        bad if bad == "invalid JSON" else revised.model_dump_json())
    assert review_coverage(scripted_service.service, "Method", [Requirement(id="method", description="Method")],
                           selection) is selection
    assert len(calls) == 2 and "Validation failure" in calls[1][1]


@pytest.mark.parametrize("entrypoint", ["direct", "tool", "cli"])
def test_alignment_reaches_full_workflow_and_stays_out_of_published_document(
    scripted_service, entrypoint, monkeypatch, capsys, tmp_path,
):
    from modules.techniques import cli

    state = scripted_service
    original = state.service.runner
    request = "Validate prerequisites and applicability of CVE-2026-109618"
    adjusted = "Develop applicability hypotheses for CVE-2026-109618 with prerequisites and future checks"
    alignments = []

    def runner(role, prompt, tools):
        if role == "technique_request_aligner":
            alignments.append(prompt)
            return json.dumps({"adjusted_request": adjusted, "reason": "Clarify hypothesis guidance"})
        if role == "technique_planner":
            return json.dumps({"components": [{"id": "method", "request": adjusted,
                "purpose": "Applicability hypotheses", "request_excerpt": request,
                "requirements": [{"id": "method", "description": "Applicability hypotheses and future checks"}]}]})
        if role == "technique_judge" and "generic_request" in prompt:
            state.calls.append((role, prompt, tools))
            return json.dumps({"generic_request": adjusted, "applicability": "conditional",
                               "unmet_prerequisites": ["Establish target version later"],
                               "reason": "Guidance can help"})
        return original(role, prompt, tools)

    state.service.runner = runner
    context = LookupContext(phase_contexts=["hypothesis_generation"], constraints=["No live execution"])
    if entrypoint == "direct":
        revision, result = state.service.get(request, context)
    elif entrypoint == "tool":
        result = state.service.tool("op", "task", context)(request=request)
        receipt = json.loads(state.service.repository.pending_uses("op", "task")[0]["use_context"])
        assert receipt["request_alignment"]["original_request"] == request
        assert receipt["request_alignment"]["adjusted_request"] == adjusted
        revision = state.service.repository.pending_uses("op", "task")[0]["revision_id"]
    else:
        context = context.model_copy(update={"constraints": [], "methodology_constraints": ["No live execution"]})
        monkeypatch.setattr(cli, "build_service", lambda *_a, **_k: state.service)
        monkeypatch.setattr(state.service.store.qdrant, "close", lambda: None)
        assert cli.main([request, "--provider", "ollama", "--phase", "hypothesis_generation",
                         "--constraint", "No live execution", "--output-dir", str(tmp_path)]) == 0
        result = capsys.readouterr().out
        revision = state.service.repository.revisions()[0]["revision_id"]
    assert len(alignments) == 1 and "Phase-aligned request: " + adjusted in result
    assert "Request alignment (operation-local)" not in state.service.store.render(revision)
    for role in ("technique_author", "technique_critic"):
        prompt = next(prompt for selected, prompt, _ in state.calls if selected == role)
        assert phase_guidance(context) in prompt
        assert "Applicability hypotheses and future checks" in prompt if entrypoint != "direct" else adjusted in prompt
    assert phase_guidance(context) in state.research_calls[0]
    source_prompt = next(prompt for role, prompt, _ in state.calls
                         if role == "technique_requirement_assessor")
    assert "future_validation" in source_prompt and "ready-made checklist" in source_prompt


def test_targeted_batches_report_repeated_queries_and_independent_review_can_finish(scripted_service):
    from modules.techniques.research import ResearchDocuments

    state = scripted_service
    original = state.service.runner
    requests = []
    events = []
    reviewed = []
    state.service.events.callback = events.append

    class Research:
        def batch(self, request, excluded):
            requests.append(request)
            return ResearchDocuments([{"url": f"https://vendor.example/{len(requests)}", "title": "Advisory",
                                       "text": "Mechanism and known versions"}], ["Broad CVE advisory"])

    def runner(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            row = coverage(["No ready-made checklist and target version unknown"]).model_dump()
            row["selected_urls"] = [f"https://vendor.example/{i}" for i in range(1, len(requests) + 1)]
            row["attributions"] = [{"url": url, "reason": "Known mechanism"} for url in row["selected_urls"]]
            row["coverage"][0]["source_urls"] = row["selected_urls"]
            return assessment_output(role, prompt, row)
        if role == "technique_requirement_reviewer":
            reviewed.append(prompt)
            row = json.loads(prompt.split("Prior requirement assessment (untrusted): ", 1)[1].split("\n", 1)[0])
            row["gaps"] = []
            row["future_validation"] = ["Establish target version before validation"]
            return json.dumps(row)
        return original(role, prompt, tools)

    state.service.research = Research()
    state.service.runner = runner
    documents, selected, rounds = state.service._research_coverage(
        "Develop CVE applicability hypotheses", [Requirement(id="method", description="Applicability hypotheses")],
        context=LookupContext(phase_contexts=["hypothesis_generation"]),
    )
    assert rounds == len(documents) == 1 and selected.sufficient
    assert len(reviewed) == 1 and "Do not invent support" in reviewed[0]
    assert "ready-made" in reviewed[0]
    assert not [event for event in events if event.get("repeated_queries")]


def test_independent_review_does_not_clear_unresolved_contradictions(scripted_service):
    state = scripted_service
    selected = coverage([{"kind": "unresolved_contradiction", "description": "Sources disagree on fixed version",
                          "claim": "Fixed version", "blocking_reason": "Cannot select version",
                          "research_question": "Which version fixes the defect?"}])
    state.service.runner = lambda *_: selected.model_dump_json()
    result = review_coverage(state.service, "Verify versions", [Requirement(id="method", description="Versions")],
                             selected, LookupContext(phase_contexts=["vulnerability_verification"]))
    assert not result.sufficient and result.coverage[0].gaps[0].kind == "unresolved_contradiction"


def test_alignment_correction_retains_original_and_validation_diagnostic(scripted_service):
    calls = []
    outputs = iter(["bad JSON", json.dumps({"adjusted_request": "Describe source analysis guidance",
                                           "reason": "Clarify guidance"}), json.dumps({
        "approved": True, "reason": "Preserved",
                               "references": ["original_request"], "subject_preserved": True,
        "mechanisms_preserved": True, "deliverables_preserved": True, "additions": [], "omissions": []})])
    scripted_service.service.runner = lambda *args: calls.append(args) or next(outputs)
    aligned, _ = align_request(scripted_service.service, "Analyze source", LookupContext(phase_contexts=["analysis"]))
    assert aligned.original_request == "Analyze source"
    assert "Validation failure" in calls[1][1] and "bad JSON" in calls[1][1]
    assert phase_guidance(LookupContext(phase_contexts=["analysis"])) in calls[1][1]


def test_alignment_diagnostics_and_feedback_keep_delivery_purpose(scripted_service):
    state = scripted_service
    events = []
    state.service.events.callback = events.append
    state.service.resolve_bundle("JWT handling", LookupContext(phase_contexts=["hypothesis_generation"]),
                                 operation="op", task="task")
    aligned = next(event for event in events if event.get("original_request"))
    assert aligned["operation_id"] == "op" and aligned["task_uid"] == "task" and aligned["activity_id"]
    state.service.evaluate("op", "task", "Guidance helped form an applicability hypothesis; validation is pending")
    feedback = next(prompt for role, prompt, _ in state.calls if role == "technique_evaluator")
    assert "recorded original and phase-aligned request" in feedback
    assert '"original_request": "JWT handling"' in feedback
    assert "Form plausible source-backed hypotheses" in feedback


@pytest.mark.parametrize("approved,subject,additions", [(True, False, []), (True, True, ["Invented"]),
                                                       (False, True, [])])
def test_preservation_findings_require_consistent_decision(approved, subject, additions):
    from modules.techniques.alignment import PreservationReview

    review = PreservationReview(approved=approved, reason="Assessment", references=["original_request"],
                                subject_preserved=subject,
                                mechanisms_preserved=True, deliverables_preserved=True,
                                additions=additions, omissions=[])
    with pytest.raises(ValueError, match="agree with all findings"):
        review.validate_decision()


@pytest.mark.parametrize("persistent", [False, True])
def test_alignment_instruction_leak_is_reviewed_before_planning(scripted_service, persistent):
    service = scripted_service.service
    original = service.runner
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_request_aligner":
            request = "Align methodology with phase instructions" if persistent or len(calls) == 1 else "Analyze source"
            return json.dumps({"adjusted_request": request, "reason": "Rewrite"})
        if role == "technique_request_reviewer":
            return json.dumps({"approved": False, "reason": "Instructions replaced methodology",
                               "references": ["original_request"], "subject_preserved": False,
                               "mechanisms_preserved": False,
                               "deliverables_preserved": False, "additions": ["Instructions"], "omissions": []})
        return original(role, prompt, tools)

    service.runner = runner
    if persistent:
        with pytest.raises(ValueError, match="after one correction"):
            align_request(service, "Analyze source", LookupContext(phase_contexts=["analysis"]))
    else:
        alignment, _ = align_request(service, "Analyze source", LookupContext(phase_contexts=["analysis"]))
        assert alignment.adjusted_request == alignment.original_request == "Analyze source"
    assert all(role != "technique_planner" for role, _ in calls)


def test_coverage_review_receives_bounded_selected_evidence(scripted_service):
    state = scripted_service
    selection = SourceSelection(
        selected_urls=["https://example.com/a"],
        attributions=[Attribution(url="https://example.com/a", reason="Technical basis")],
        authoritative_sufficient=True, reason="Review evidence",
        coverage=[Support(requirement_id="method", source_urls=["https://example.com/a"],
                          explanation="Technical facts support future checks", gaps=[])],
    )
    prompts = []
    original = state.service.runner

    def runner(role, prompt, tools):
        prompts.append(prompt)
        return original(role, prompt, tools)

    state.service.runner = runner
    result = review_coverage(
        state.service, "Method", [Requirement(id="method", description="Method")], selection, documents=[
        {"url": "https://example.com/a", "text": "selected evidence" + "x" * 7000},
        {"url": "https://example.com/b", "text": "unselected evidence"},
    ])
    assert result.sufficient and result is not selection
    assert "selected evidence" in prompts[0]
    assert "unselected evidence" not in prompts[0]
    assert "x" * 6001 not in prompts[0]
    assert "omitted text is unknown" in prompts[0]


@pytest.mark.parametrize("references", [["original_request", "original_request"]])
def test_preservation_rejects_invalid_input_references(references):
    from modules.techniques.alignment import PreservationReview

    review = PreservationReview(approved=True, reason="Preserved", subject_preserved=True,
                                mechanisms_preserved=True, deliverables_preserved=True,
                                additions=[], omissions=[], references=references)
    with pytest.raises(ValueError, match="unique supplied input fields"):
        review.validate_decision()


def test_preservation_events_redact_nested_findings(scripted_service):
    from modules.techniques.alignment import PreservationReview, emit_preservation_review

    service = scripted_service.service
    events = []
    service.events.callback = events.append
    service.private_values = ("private-name",)
    review = PreservationReview(approved=False, reason="Lost private-name", subject_preserved=False,
                                mechanisms_preserved=True, deliverables_preserved=True,
                                additions=["private-name"], omissions=["private-name"],
                                references=["original_request"])
    emit_preservation_review(service, review)
    assert "private-name" not in json.dumps(events)
    assert events[0]["findings"]["approved"] is False


def test_preservation_reference_enum_is_generation_constrained():
    from modules.techniques.alignment import PreservationReview

    schema = PreservationReview.model_json_schema()
    assert schema["properties"]["references"]["items"]["enum"] == [
        "original_request", "component_request", "requirements", "phase_contexts", "constraints",
    ]
    with pytest.raises(ValueError, match="Input should be"):
        PreservationReview(approved=True, reason="Preserved", subject_preserved=True,
                           mechanisms_preserved=True, deliverables_preserved=True,
                           additions=[], omissions=[], references=["phase_objectives[1]"])
