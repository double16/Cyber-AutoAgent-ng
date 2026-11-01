"""Policy ownership and evidence-based scoring regressions."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.techniques.alignment import align_request
from modules.techniques.context import LookupContext
from modules.techniques.feedback import ContributionFeedback
from modules.techniques.policy import resolve_policy


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


@pytest.mark.parametrize("policy", [{"unknown": []}, {"mode": "bad"}, {"restrictions": [" "]},
                                    {"restrictions": ["x" * 2001]}, {"restrictions": [True]}])
def test_invalid_module_policy(policy):
    with pytest.raises(ValueError, match="Module web: invalid technique_policy"):
        resolve_policy(loader({"web": {"technique_policy": policy}}), "web")


@pytest.mark.parametrize("helpful,harmful,score", [(False, False, 0), (True, False, 1),
                                                   (False, True, -1), (True, True, -1)])
def test_contribution_score(helpful, harmful, score):
    findings = ContributionFeedback(methodological_purpose="Design controls", observed_use="Compared cases",
                                    helpful_contribution=helpful, contribution="Clarified controls" if helpful else "",
                                    misleading_or_harmful=harmful, harmful_effect="Wrong interpretation" if harmful else "",
                                    reason="Observed methodological effect")
    assert findings.score == score


@pytest.mark.parametrize("updates", [{"helpful_contribution": True}, {"contribution": "Unsupported benefit"},
                                     {"misleading_or_harmful": True}, {"harmful_effect": "Unsupported harm"},
                                     {"helpful_contribution": True, "contribution": "Benefit", "observed_use": ""}])
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
                    return json.dumps({"approved": False, "reason": "Citation alone",
                                       "findings": ["Concrete application is unestablished"]})
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
            return json.dumps({"approved": count > 1, "reason": "Check the control application", "findings": []
                               if count > 1 else ["Explain observed control contribution more precisely"]})
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
            return json.dumps({"approved": False, "reason": "Contribution disputed",
                               "findings": ["Guidance-to-behavior correspondence remains unestablished"]})
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
                return json.dumps({"approved": True, "reason": "Contradictory judgment",
                                   "findings": ["Specific disputed correspondence"]})
            assert "Rejected response" in prompt and "Specific disputed correspondence" in prompt
            assert "Return ContributionFeedback findings" not in prompt
            return json.dumps({"approved": True, "reason": "Consistency corrected", "findings": []})
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.evaluate("op", "task", "Observed application")
    assert len(reviews) == 2
    assert not state.service.repository.pending_uses("op", "task")


def test_feedback_schema_explains_empty_effect_fields_for_absent_contributions():
    fields = ContributionFeedback.model_json_schema()["properties"]
    for name in ("helpful_contribution", "contribution", "misleading_or_harmful", "harmful_effect"):
        assert "empty string" in fields[name]["description"]
    assert "explain neutrality in reason" in fields["contribution"]["description"]
    mapping = ContributionFeedback.model_json_schema()["$defs"]["EvidenceMapping"]["properties"]
    assert "Copy exact guidance:" in mapping["guidance_references"]["description"]
    assert "Artifact paths" in mapping["result_references"]["description"]


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
