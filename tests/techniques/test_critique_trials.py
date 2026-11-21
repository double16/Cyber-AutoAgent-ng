"""Factorial arms retain provenance, uncertainty and current correction ownership."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from modules.techniques.critique import Critique, IssueAssessment
from modules.techniques.critique_experiment import Variant
from modules.techniques.critique_trials import (
    ExperimentRunner,
    Procedure,
    ProcedureInventory,
    ProcedureReview,
    rated_schema,
)
from modules.techniques.roles import DeferredReviewPrompt


class Runner:
    def __init__(self, score=5, bad=False, empty=False):
        self.calls = []
        self.score = score
        self.bad = bad
        self.empty = empty

    def structured(self, role, prompt, schema, tools):
        self.calls.append((role, prompt, schema, tools))
        if schema is ProcedureInventory:
            return ProcedureInventory(procedures=[] if self.empty else [Procedure(
                references=["bad" if self.bad else "d" + hashlib.sha256(b"Procedure").hexdigest()[:12]
                            + ":b1"], context_references=[])], no_procedures_reason="")
        if schema is ProcedureReview:
            return ProcedureReview(prerequisites="Known denied rights", observation="Read succeeds",
                claimed_boundary="Entitlement", policy_establishment="Known baseline",
                control_and_what_it_establishes="Compare allowed and denied", permitted_scenario="None",
                matches_all_conditions=False, reasoning="Conditions exclude permitted access")
        if schema is Critique:
            return Critique(approved=True, reason="Complete", no_security_checks_reason="No checks")
        if schema is IssueAssessment:
            return IssueAssessment(critic_supported=True, reason="Supported")
        values = {"score": self.score, "reason": "Evidence reviewed"}
        if schema.__name__ == "RatedCritique":
            values.update(issues=[], interpretations=[], no_security_checks_reason="No checks")
        else:
            values.update(findings=[], interpretation_reviews=[])
        return schema.model_validate(values)


@pytest.mark.parametrize("variant", Variant)
@pytest.mark.parametrize("schema", [Critique, IssueAssessment])
def test_variants_and_deferred_comparison(variant, schema):
    runner = Runner()
    adapter = ExperimentRunner(runner, variant, "Procedure")
    prompt = DeferredReviewPrompt("Independent subject", "Withheld comparison")
    result = adapter.structured("technique_critique_assessor", prompt, schema, [])
    assert result.approved
    final = runner.calls[-1][1]
    assert isinstance(final, DeferredReviewPrompt)
    assert "Withheld comparison" not in final.investigation
    if variant in (Variant.B, Variant.D):
        assert "score instead" in final.comparison
        assert "approved" not in rated_schema(Critique).model_fields
        assert next(iter(rated_schema(Critique).model_fields)) == "score"
        assert {"score", "reason", "interpretations", "no_security_checks_reason"} <= set(
            rated_schema(Critique).model_json_schema()["required"])
        assert {"score", "reason", "findings", "interpretation_reviews"} <= set(
            rated_schema(IssueAssessment).model_json_schema()["required"])
    if variant in (Variant.C, Variant.D):
        assert "COMPLETE draft" in final.investigation
        assert adapter._focus("technique_critique_assessor", []) == adapter.focus_cache["technique_critique_assessor"]
        assert sum(call[2] is ProcedureInventory for call in runner.calls) == 1
        assert "Include future checks" in runner.calls[0][1]


@pytest.mark.parametrize("score,approved", [(1, False), (2, False), (4, True), (5, True)])
def test_rating_normalization(score, approved):
    adapter = ExperimentRunner(Runner(score), Variant.B, "Procedure")
    result = adapter.structured("technique_critic", "Review", Critique, [])
    assert result.approved is approved
    assert adapter.records[-1]["output"]["score"] == score
    assert "approved" not in adapter.records[-1]["output"]


def test_uncertainty_retains_response_without_invented_findings():
    with pytest.raises(ValueError, match="uncertainty") as failure:
        ExperimentRunner(Runner(3), Variant.B, "Procedure").structured("technique_critic", "Review", Critique, [])
    assert json.loads(failure.value.rejected_response)["issues"] == []


@pytest.mark.parametrize("bad,empty", [(True, False), (False, True)])
def test_focused_inventory_validation(bad, empty):
    with pytest.raises(ValueError):
        ExperimentRunner(Runner(bad=bad, empty=empty), Variant.C, "Procedure").structured(
            "technique_critic", "Review", Critique, [])


def test_plain_focused_prompt_and_other_schema_passthrough():
    runner = Runner()
    adapter = ExperimentRunner(runner, Variant.D, "Procedure")
    adapter.structured("technique_critic", "Review", Critique, [])
    assert "COMPLETE draft" in runner.calls[-1][1]
    assert "score instead" in runner.calls[-1][1]
    schema = SimpleNamespace()
    runner.structured = lambda *args: "passthrough"
    assert adapter.structured("technique_critic", "Review", schema, []) == "passthrough"


@pytest.mark.parametrize("schema", [Critique, IssueAssessment])
def test_high_rating_does_not_override_blocking_findings(schema):
    from modules.techniques.critique import AssessmentFinding, Issue

    if schema is Critique:
        values = Critique(approved=False, reason="Incorrect boundary", issues=[Issue(
            id="boundary", category="authored_content", problem="Permitted access labeled violation",
            requested_correction="Establish a denied baseline", requirement_ids=[], source_urls=[],
            draft_references=["ref"],
        )]).model_dump()
        values.pop("approved")
    else:
        values = IssueAssessment(critic_supported=False, reason="Omitted defect", findings=[AssessmentFinding(
            problem="Omitted boundary defect", requested_correction="Reassess actual condition",
            draft_references=["ref"],
        )]).model_dump()
        values.pop("critic_supported")
    values["score"] = 5
    runner = SimpleNamespace(structured=lambda role, prompt, output_schema, tools: output_schema.model_validate(values))
    assert not ExperimentRunner(runner, Variant.B, "Procedure").structured(
        "technique_critic", "Review", schema, []).approved


@pytest.mark.parametrize("documents", [[], [{"url": "https://reference.example/contract", "text": "Policy contract"}]])
def test_focused_inputs_expose_source_availability_separately_from_draft(documents):
    runner = Runner()
    adapter = ExperimentRunner(runner, Variant.C, "Procedure", documents)
    adapter.structured("technique_critic", "Review", Critique, [])
    for _, prompt, schema, _ in runner.calls:
        if schema in (ProcedureInventory, ProcedureReview):
            assert "Draft reference IDs are not source URLs" in prompt
            assert "retained_sources" in prompt
            assert "Procedure" in prompt
            if documents:
                assert documents[0]["url"] in prompt
            else:
                assert "Empty source catalogs" in prompt
