import pytest
from pydantic import ValidationError

from modules.techniques.bundles import DECOMPOSITION_POLICY, BundlePlanReview


def test_advice_schema_has_no_approval():
    schema = BundlePlanReview.model_json_schema()
    assert "approved" not in schema["properties"]
    assert BundlePlanReview(reason="No improvements").issues == []
    assert BundlePlanReview.model_validate({"approved": False, "reason": "Legacy"}).issues == []


def test_unknown_advice_fields_rejected():
    with pytest.raises(ValidationError):
        BundlePlanReview(reason="Advice", refusal=True)


def test_policy_distinguishes_requirements_and_overlapping_methods():
    assert "requirements within a method" in DECOMPOSITION_POLICY
    assert "duplicates its constituent components" in DECOMPOSITION_POLICY


@pytest.mark.parametrize("field,value,message", [
    ("input_references", ["unknown:1"], "unknown input reference"),
    ("component_ids", ["component-999"], "unknown component"),
])
def test_review_references_fail_before_delivery(scripted_service, monkeypatch, field, value, message):
    import json

    state = scripted_service
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_plan_reviewer":
            issue = {"rule": "overlap", "component_ids": ["component-1"],
                     "input_references": ["original_request:1"], "correction": "Remove overlap"}
            issue[field] = value
            return json.dumps({"approved": False, "reason": "Review", "issues": [issue]})
        return original(role, prompt, tools)

    state.service.runner = runner
    monkeypatch.setattr(state.service, "get", lambda *a, **kw: pytest.fail("Invalid review reached delivery"))
    with pytest.raises(ValueError, match=message):
        state.service.resolve_bundle("Reusable boundary checks")
