"""Policy ownership and evidence-based scoring regressions."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.techniques.alignment import align_request
from modules.techniques.context import LookupContext
from modules.techniques.feedback import ContributionFeedback
from modules.techniques.policy import resolve_policy


def independent_review(original, prompt, tools, error=None):
    data = json.loads(original("technique_feedback_reviewer", prompt, tools))
    if error:
        import re

        reference = re.search(r"result:[a-f0-9]+:[^\s\"]+", prompt)[0]
        data["material_errors"] = [{"evaluator_field": "contribution", "references": [reference],
                                   "explanation": error}]
    return json.dumps(data)


def loader(rows):
    return SimpleNamespace(_find_module_dir=lambda n: n if n in rows else None,
                           _get_extend_list=lambda n: rows[n].get("extend", []),
                           _read_module_yaml=lambda n: rows[n])


def test_policy_branch_inheritance_and_origins():
    rows = {"root": {"technique_policy": {"restrictions": ["Explain controls"]}},
            "a": {"extend": ["root"], "technique_policy": {"mode": "replace", "restrictions": []}},
            "b": {"extend": ["root"]}, "child": {"extend": ["a", "b"]}}
    expected = [{"module": "root", "restriction": "Explain controls"}]
    assert resolve_policy(loader(rows), "child") == expected
    assert resolve_policy(loader(rows), "missing") == []
    rows["root"]["extend"] = ["child"]
    assert resolve_policy(loader(rows), "child") == expected
    rows["b"]["technique_policy"] = {"restrictions": ["Explain controls", "Explain controls"]}
    assert resolve_policy(loader(rows), "b")[-1]["module"] == "b"


@pytest.mark.parametrize(
    "policy",
    [
        {"unknown": []},
        {"mode": "bad"},
        {"restrictions": [" "]},
        {"restrictions": ["x" * 2001]},
        {"restrictions": [True]},
    ],
)
def test_invalid_module_policy(policy):
    with pytest.raises(ValueError, match="Module web: invalid technique_policy"):
        resolve_policy(loader({"web": {"technique_policy": policy}}), "web")


@pytest.mark.parametrize(
    "helpful,harmful,score", [(False, False, 0), (True, False, 1), (False, True, -1), (True, True, -1)]
)
def test_contribution_score(helpful, harmful, score):
    findings = ContributionFeedback(methodological_purpose="Design controls", observed_use="Compared cases",
                                    helpful_contribution=helpful, contribution="Clarified controls" if helpful else "",
                                    misleading_or_harmful=harmful, harmful_effect="Wrong interpretation" if harmful else "",
                                    reason="Observed methodological effect")
    assert findings.score == score


@pytest.mark.parametrize(
    "updates",
    [
        {"helpful_contribution": True},
        {"contribution": "Unsupported benefit"},
        {"misleading_or_harmful": True},
        {"harmful_effect": "Unsupported harm"},
        {"helpful_contribution": True, "contribution": "Benefit", "observed_use": ""},
    ],
)
def test_feedback_rejects_inconsistent_findings(updates):
    fields = dict(methodological_purpose="Design checks", observed_use="", helpful_contribution=False,
                  contribution="", misleading_or_harmful=False, harmful_effect="", reason="Contribution unknown")
    with pytest.raises(ValidationError):
        ContributionFeedback(**{**fields, **updates})


@pytest.mark.parametrize("subject", ["HTTP comparison", "TCP mapping", "filesystem review"])
def test_alignment_separates_operation_and_methodology_constraints(scripted_service, subject):
    service = scripted_service.service
    service.module_technique_policy = [{"module": "base", "restriction": "Explain cleanup"}]
    original = service.runner
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_request_aligner":
            prompts.append(prompt)
            return json.dumps({"adjusted_request": subject, "reason": "Preserve method"})
        return original(role, prompt, tools)

    service.runner = runner
    context = LookupContext(constraints=["Only assigned.internal port 1234"],
                            methodology_constraints=["Describe negative controls"])
    _, aligned = align_request(service, subject, context)
    assert "assigned.internal" not in prompts[0]
    assert "Explain cleanup" in prompts[0] and "Describe negative controls" in prompts[0]
    assert context.module_technique_policy == []
    assert not hasattr(aligned, "constraints")
    assert "assigned.internal" not in aligned.render()
    _, bare = align_request(service, subject, None)
    assert bare.module_technique_policy == service.module_technique_policy


def test_synchronization_loads_policy_without_modifying_general_module_rules(scripted_service):
    rows = {"base": {"technique_policy": {"restrictions": ["Explain cleanup"]}},
            "web": {"extend": ["base"], "constraints": ["Only assigned target"]}}
    source = loader(rows)
    source._inheritance_chain = lambda _: []
    service = scripted_service.service
    events = []
    service.events.callback = events.append
    service.synchronize_module(source, "web")
    assert service.module_technique_policy == [{"module": "base", "restriction": "Explain cleanup"}]
    assert next(e for e in events if e["type"] == "technique_policy")["module"] == "web"
    assert rows["web"]["constraints"] == ["Only assigned target"]


@pytest.mark.parametrize("valid_reference", [False, True])
def test_module_policy_conflict_is_explicit_and_references_supplied_policy(scripted_service, valid_reference):
    service = scripted_service.service
    service.module_technique_policy = [{"module": "base", "restriction": "Describe read-only methods"}]
    request = "Describe a write-validation method"
    calls = []

    def runner(role, prompt, tools):
        calls.append(prompt)
        return json.dumps({"adjusted_request": request, "reason": "Method conflicts with methodology restriction",
                           "conflict": {"requested_deliverable": "write-validation", "request_excerpt": request,
                                        "restriction_reference": "Describe read-only methods" if valid_reference
                                        else "Invented policy", "explanation": "Write validation requires writes"}})

    service.runner = runner
    with pytest.raises(ValueError, match="alignment conflict" if valid_reference else "after one correction"):
        align_request(service, request, None)
    assert len(calls) == (1 if valid_reference else 2)
    assert "Invented policy" not in service.module_technique_policy[0]["restriction"]


def test_methodology_constraints_are_separate_from_operation_constraints():
    from modules.techniques.context import LookupContext

    context = LookupContext(constraints=["No writes to assigned host"],
                            methodology_constraints=["Explain non-destructive controls"])
    assert context.constraints == ["No writes to assigned host"]
    assert context.methodology_constraints == ["Explain non-destructive controls"]
    assert LookupContext().methodology_constraints == []


def test_policy_diagnostics_redact_private_values_without_changing_internal_policy(scripted_service):
    service = scripted_service.service
    service.private_values = ("private-identifier",)
    source = loader({"web": {"technique_policy": {"restrictions": ["Avoid private-identifier"]}}})
    source._inheritance_chain = lambda _: []
    events = []
    service.events.callback = events.append
    service.synchronize_module(source, "web")
    event = next(row for row in events if row["type"] == "technique_policy")
    assert "private-identifier" not in str(event)
    assert service.module_technique_policy[0]["restriction"] == "Avoid private-identifier"


@pytest.mark.parametrize("subject", ["HTTP comparison", "network exclusion", "filesystem hypothesis"])
@pytest.mark.parametrize("score", [-1, 0, 1])
def test_every_feedback_score_has_independent_evidence_review(scripted_service, subject, score):
    state = scripted_service
    state.score = score
    state.service.tool("op", "task")(request="Generic methodology")
    state.service.evaluate("op", "task", subject + "; execution remains incomplete")
    reviews = [p for r, p, _ in state.calls if r == "technique_feedback_reviewer"]
    assert len(reviews) == 1
    assert subject in reviews[0] and "resolved_evidence" in reviews[0]
    assert not state.service.repository.pending_uses("op", "task")


@pytest.mark.parametrize("defect", ["reference", "neutral", "disagreement", "review_contract"])
@pytest.mark.parametrize("recover", [False, True])
def test_feedback_shared_correction_and_pending_on_exhaustion(scripted_service, defect, recover):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    counts = {"evaluate": 0, "review": 0}

    def runner(role, prompt, tools):
        if role == "technique_evaluator":
            counts["evaluate"] += 1
            if defect == "reference" and counts["evaluate"] == 2:
                assert "result:invented:1" in prompt
                assert "Copy exact result:" in prompt
                assert "do not use artifact paths or tool-call IDs" in prompt
            data = json.loads(original(role, prompt, tools))
            if not recover or counts["evaluate"] == 1:
                if defect == "reference":
                    data["evidence_mappings"][0]["result_references"] = ["result:invented:1"]
                if defect == "neutral":
                    data.update(helpful_contribution=False, contribution="", neutral_basis=None)
            return json.dumps(data)
        if role == "technique_feedback_reviewer":
            counts["review"] += 1
            if not recover or counts["review"] == 1:
                if defect == "review_contract":
                    return "{}"
                if defect == "disagreement":
                    return independent_review(original, prompt, tools, "Concrete application is unestablished")
        return original(role, prompt, tools)

    state.service.runner = runner
    if recover:
        state.service.evaluate("op", "task", "Observed application")
        assert not state.service.repository.pending_uses("op", "task")
    else:
        with pytest.raises(ValueError, match="feedback remains pending"):
            state.service.evaluate("op", "task", "Observed application")
        assert state.service.repository.pending_uses("op", "task")
    assert counts["evaluate"] == (1 if defect == "review_contract" else 2)


def test_feedback_review_uses_shared_rubric_without_evaluator_output_instructions(scripted_service):
    from modules.techniques.feedback import USEFULNESS_RUBRIC

    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    state.service.evaluate("op", "task", "Concrete next step; execution incomplete")
    evaluator = next(p for r, p, _ in state.calls if r == "technique_evaluator")
    reviewer = next(p for r, p, _ in state.calls if r == "technique_feedback_reviewer")
    assert USEFULNESS_RUBRIC in evaluator and USEFULNESS_RUBRIC in reviewer
    assert "Return ContributionFeedback findings" in evaluator
    assert "Return ContributionFeedback findings" not in reviewer
    assert "Concrete next step; execution incomplete" in reviewer


def test_feedback_uses_frozen_delivery_after_lifecycle_changes(scripted_service, monkeypatch):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    receipt = state.service.repository.pending_uses("op", "task")[0]
    frozen = json.loads(receipt["use_context"])["delivered_documents"]
    assert frozen
    monkeypatch.setattr(state.service.store, "render", lambda *a, **kw: "Later lifecycle state")
    state.service.evaluate("op", "task", "Observed controlled application")
    evaluator = next(p for r, p, _ in state.calls if r == "technique_evaluator")
    assert frozen[0] in evaluator
    assert "Later lifecycle state" not in evaluator
    assert "Delivery snapshot: recorded" in evaluator


@pytest.mark.parametrize("snapshots", [[], "wrong", [1], [""]])
def test_corrupt_controller_snapshot_leaves_feedback_pending(scripted_service, snapshots):
    state = scripted_service
    revision = state.publish()
    state.service.repository.use("op", "task", revision, {"delivered_documents": snapshots})
    with pytest.raises(ValueError, match="feedback remains pending"):
        state.service.evaluate("op", "task", "Results")
    assert state.service.repository.pending_uses("op", "task")


def test_legacy_feedback_context_explicitly_unknown(scripted_service):
    state = scripted_service
    state.service.repository.use("op", "task", state.publish())
    state.service.evaluate("op", "task", "Results")
    evaluator = next(p for r, p, _ in state.calls if r == "technique_evaluator")
    assert "Delivery snapshot: unknown (legacy context)" in evaluator


@pytest.mark.parametrize("subject", ["HTTP control", "network exclusion", "filesystem source comparison"])
def test_feedback_retains_read_evidence_for_reviewer_and_correction_without_rereading(scripted_service, subject):
    from strands import tool

    from modules.techniques.evidence import current_tool_evidence_hook

    @tool
    def inspect_evidence() -> str:
        """Read the task artifact."""
        return subject

    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    calls, reads, reference = [], [], []

    def runner(role, prompt, tools):
        calls.append((role, prompt, tools))
        if role == "technique_evaluator":
            if not reference:
                assert tools == [inspect_evidence]
                observed = SimpleNamespace(tool_use={"name": "inspect_evidence", "toolUseId": "read-1"},
                                           result={"status": "success", "content": [{"text": inspect_evidence()}]})
                hook = current_tool_evidence_hook(tools)
                hook.after_tool(observed)
                reference.extend(hook.capture.origins)
                reads.append(observed)
            else:
                assert tools == []
                assert subject in prompt and reference[0] in prompt
            data = json.loads(original(role, prompt, tools))
            data["evidence_mappings"][0]["result_references"] = [reference[0]]
            return json.dumps(data)
        if role == "technique_feedback_reviewer":
            assert subject in prompt and reference[0] in prompt
            assert "retained_tool_evidence" in prompt
            count = sum(selected == role for selected, _, _ in calls)
            assert tools == ([inspect_evidence] if count == 1 else [])
            return independent_review(original, prompt, tools,
                                      "Explain observed control contribution more precisely" if count == 1 else None)
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.evaluate("op", "task", "Artifact available; execution incomplete", [inspect_evidence])
    assert len(reads) == 1
    assert not state.service.repository.pending_uses("op", "task")
    assert len(calls) == 4


@pytest.mark.parametrize("contract_first", [False, True])
def test_feedback_reviewer_contract_and_disagreement_share_one_allowance(scripted_service, contract_first):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, prompt))
        if role == "technique_feedback_reviewer":
            count = sum(selected == role for selected, _ in calls)
            if (count == 1) == contract_first:
                return "{}"
            return independent_review(original, prompt, tools, "Correspondence remains unestablished")
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="remains pending"):
        state.service.evaluate("op", "task", "Observed results")
    assert sum(role == "technique_feedback_reviewer" for role, _ in calls) == 2
    assert sum(role == "technique_evaluator" for role, _ in calls) == (1 if contract_first else 2)
    assert state.service.repository.pending_uses("op", "task")
    if contract_first:
        repair = [prompt for role, prompt in calls if role == "technique_feedback_reviewer"][1]
        assert "Validation failure" in repair and "Independently check purpose" in repair
        assert "Return ContributionFeedback findings" not in repair


def test_reviewer_consistency_repair_preserves_rejected_review_in_its_own_role(scripted_service):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    reviews = []

    def runner(role, prompt, tools):
        if role == "technique_feedback_reviewer":
            reviews.append(prompt)
            if len(reviews) == 1:
                data = json.loads(independent_review(original, prompt, tools, "Specific disputed correspondence"))
                data["material_errors"][0]["evaluator_field"] = "invented"
                return json.dumps(data)
            assert "Rejected response" in prompt and "Specific disputed correspondence" in prompt
            assert "Return ContributionFeedback findings" not in prompt
            return independent_review(original, prompt, tools)
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.evaluate("op", "task", "Observed application")
    assert len(reviews) == 2
    assert not state.service.repository.pending_uses("op", "task")


def test_feedback_schema_explains_empty_effect_fields_for_absent_contributions():
    fields = ContributionFeedback.model_json_schema()["properties"]
    assert "output specificity alone" in fields["purpose_mismatch"]["description"]
    assert "not a requirement" in fields["methodological_purpose"]["description"]
    for name in ("helpful_contribution", "contribution", "misleading_or_harmful", "harmful_effect"):
        assert "empty string" in fields[name]["description"]
    assert "explain neutrality in reason" in fields["contribution"]["description"]
    mapping = ContributionFeedback.model_json_schema()["$defs"]["EvidenceMapping"]["properties"]
    assert "Copy exact guidance:" in mapping["guidance_references"]["description"]
    assert "Artifact paths" in mapping["result_references"]["description"]


@pytest.mark.parametrize(
    "subject,score",
    [
        ("HTTP: authentication role comparison applied to a product route", 1),
        ("Network: reachability control excluded an unavailable service", 1),
        ("Filesystem: ownership comparison supplied a next step for one file", 1),
        ("HTTP: authentication hypothesis only; injection contribution unestablished", 0),
        ("Network: document cited without demonstrated use", 0),
        ("Filesystem: observed misleading permission interpretation despite useful controls", -1),
    ],
)
def test_application_scope_rules_survive_feedback_disagreement(scripted_service, subject, score):
    """Exercise assembled initial/correction/review inputs, without simulating semantic judgment."""
    from modules.techniques.feedback import USEFULNESS_RUBRIC

    state = scripted_service
    state.score = score
    state.service.tool("op", "task")(request="Produce reusable generic methodology")
    original = state.service.runner
    seen = []

    def runner(role, prompt, tools):
        if role in {"technique_evaluator", "technique_feedback_reviewer"}:
            seen.append((role, prompt))
            assert USEFULNESS_RUBRIC in prompt
            assert subject in prompt
            assert "does not require the consuming task to repeat" in prompt
            assert "not limited to login or token-issuance routes" in prompt
            assert "does not by itself demonstrate use of injection guidance" in prompt
        if role == "technique_feedback_reviewer" and len(seen) == 2:
            return independent_review(original, prompt, tools, "Identify the observed methodological correspondence")
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.evaluate("op", "task", subject)
    assert [role for role, _ in seen] == ["technique_evaluator", "technique_feedback_reviewer"] * 2
    assert "Identify the observed methodological correspondence" in seen[2][1]
    assert not state.service.repository.pending_uses("op", "task")


def test_feedback_system_contract_scopes_target_independence_to_authored_guidance():
    from modules.techniques.feedback import IndependentFeedbackReview
    from modules.techniques.roles import structured_system_prompt

    for schema in (ContributionFeedback, IndependentFeedbackReview):
        prompt = structured_system_prompt(schema)
        assert "Reusable guidance you author" in prompt
        assert "evaluation may be target-specific" in prompt
        assert "All guidance must be independent" not in prompt
        assert schema.__name__ in prompt


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_internal_review_decisions_name_the_subject_and_normalize_legacy_approval(legacy, supported):
    from modules.techniques.critique import IssueAssessment
    from modules.techniques.feedback import FeedbackReview

    for schema, field in ((IssueAssessment, "critic_supported"), (FeedbackReview, "feedback_supported")):
        decision = schema.model_validate({"approved" if legacy else field: supported, "reason": "Evidence review"})
        assert decision.approved == supported
        assert decision.model_dump()[field] == supported
        assert "approved" not in decision.model_dump()
        assert field in schema.model_json_schema()["properties"]
        assert "approved" not in schema.model_json_schema()["properties"]


@pytest.mark.parametrize("conflict", [False, True])
def test_internal_review_decision_rejects_unknown_or_conflicting_fields(conflict):
    from modules.techniques.critique import IssueAssessment
    from modules.techniques.feedback import FeedbackReview

    for schema, field in ((IssueAssessment, "critic_supported"), (FeedbackReview, "feedback_supported")):
        data = {"reason": "Review", field: True, "approved": False} if conflict else {
            "reason": "Review", "supported": True,
        }
        with pytest.raises(ValueError):
            schema.model_validate(data)


@pytest.mark.parametrize("defect", ["score", "neutral", "mapping", "reference", "field", "error_reference"])
def test_independent_assessment_errors_never_persist_disputed_feedback(scripted_service, defect):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner

    def runner(role, prompt, tools):
        data = json.loads(original(role, prompt, tools))
        if role == "technique_feedback_reviewer":
            if defect in {"score", "neutral"}:
                data["assessment"].update(helpful_contribution=False, contribution="",
                                          neutral_basis="contribution_unestablished" if defect == "score" else None)
            elif defect == "mapping":
                data["assessment"]["evidence_mappings"] = []
            elif defect == "reference":
                data["assessment"]["evidence_mappings"][0]["guidance_references"] = ["guidance:missing:1"]
            else:
                data = json.loads(independent_review(original, prompt, tools, "Unsupported contribution"))
                if defect == "field":
                    data["material_errors"][0]["evaluator_field"] = "unknown"
                else:
                    data["material_errors"][0]["references"] = ["result:missing:1"]
        return json.dumps(data)

    state.service.runner = runner
    with pytest.raises(ValueError, match="remains pending"):
        state.service.evaluate("op", "task", "Observed application")
    assert state.service.repository.pending_uses("op", "task")
    assert not state.service.repository.scored_uses("op", "task")


def test_new_review_schema_exposes_independent_findings_without_agreement_verdict():
    from modules.techniques.feedback import IndependentFeedbackReview
    from modules.techniques.roles import structured_system_prompt

    schema = IndependentFeedbackReview.model_json_schema()
    assert schema["required"] == ["assessment"]
    assert set(schema["properties"]) == {"assessment", "material_errors"}
    prompt = structured_system_prompt(IndependentFeedbackReview)
    assert '"ContributionFeedback"' in prompt
    assert '"MaterialFeedbackError"' in prompt
    assert '"feedback_supported"' not in prompt and '"approved"' not in prompt


@pytest.mark.parametrize(
    "text,valid",
    [
        ("guidance:a:1 maps to result:b:1.", True),
        ("guidance:a:1 and guidance:a:1 with result:b:1", True),
        ("guidance:a:10 and result:b:1", False),
        ("guidance:a:1-extra and result:b:1", False),
        ("a:1 and result:b:1", False),
        ("guidance:a:1 and result:unknown:1", False),
        ("guidance:a:1 alone", False),
    ],
)
def test_prose_recovery_uses_complete_known_ids_without_pairing(scripted_service, text, valid):
    from modules.techniques.feedback import recover_prose_references

    data = json.loads(scripted_service.service.runner("technique_evaluator", "", []))
    data.update(reason=text, evidence_mappings=[])
    feedback = ContributionFeedback.model_validate(data)
    references = {"guidance:a:1": "Method", "result:b:1": "Behavior"}
    if valid:
        inventory = recover_prose_references(feedback, references)
        assert {row["reference"] for row in inventory} == set(references)
        assert all(row["field"] == "reason" and row["prose"] == text for row in inventory)
        assert feedback.evidence_mappings == []
    else:
        with pytest.raises(ValueError):
            recover_prose_references(feedback, references)


@pytest.mark.parametrize("review_valid", [True, False, "outside"])
def test_prose_inventory_requires_structured_independent_confirmation(scripted_service, review_valid):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    calls = []

    def runner(role, prompt, tools):
        calls.append(role)
        if role == "technique_evaluator":
            data = json.loads(original(role, prompt, tools))
            mapping = data["evidence_mappings"][0]
            data["reason"] += " " + " ".join(mapping["guidance_references"] + mapping["result_references"])
            data["evidence_mappings"] = []
            return json.dumps(data)
        if role == "technique_feedback_reviewer":
            assert "unpaired_prose_references" in prompt
            data = json.loads(original("technique_evaluator", prompt, tools))
            if review_valid is False:
                data["evidence_mappings"] = []
            elif review_valid == "outside":
                mapping = data["evidence_mappings"][0]
                reference = mapping["result_references"][0]
                mapping["result_references"] = [reference.rsplit(":", 1)[0] + ":2"]
            return json.dumps({"assessment": data, "material_errors": []})
        return original(role, prompt, tools)

    state.service.runner = runner
    if review_valid is True:
        state.service.evaluate("op", "task", "Observed control contribution\nUnrelated observation")
        assert not state.service.repository.pending_uses("op", "task")
        assert calls == ["technique_evaluator", "technique_feedback_reviewer"]
    else:
        with pytest.raises(ValueError, match="remains pending"):
            state.service.evaluate("op", "task", "Observed control contribution\nUnrelated observation")
        assert state.service.repository.pending_uses("op", "task")


def test_evidence_mapping_schema_follows_contribution_explanation():
    from modules.techniques.feedback import IndependentFeedbackReview

    fields = list(ContributionFeedback.model_json_schema()["properties"])
    assert fields[-1] == "evidence_mappings"
    assert fields.index("reason") < fields.index("evidence_mappings")
    nested = IndependentFeedbackReview.model_json_schema()["$defs"]["ContributionFeedback"]
    assert list(nested["properties"])[-1] == "evidence_mappings"
    assert nested["properties"]["evidence_mappings"]["type"] == "array"
    assert "anyOf" not in nested


@pytest.mark.parametrize(
    "reference,expected", [("result:known:1", "remains pending"), ("result:unknown:1", "known controller")]
)
def test_missing_necessary_evidence_never_becomes_a_neutral_score(reference, expected):
    from modules.techniques.feedback import resolve_feedback

    feedback = ContributionFeedback(
        neutral_basis="contribution_unestablished",
        methodological_purpose="Compare access policies",
        observed_use="Read was blocked",
        helpful_contribution=False,
        contribution="",
        misleading_or_harmful=False,
        harmful_effect="",
        reason="Necessary artifact remains unread",
        necessary_evidence_gaps=[{"references": [reference], "missing_input": "Recorded task comparison"}],
    )
    with pytest.raises(ValueError, match=expected):
        resolve_feedback(feedback, {"result:known:1": "Artifact read blocked by reader guard"})
