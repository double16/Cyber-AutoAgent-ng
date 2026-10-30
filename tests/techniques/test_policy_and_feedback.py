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
