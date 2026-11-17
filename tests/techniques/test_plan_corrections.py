"""Local planner edits preserve unrelated methodology and provenance."""

import pytest
from pydantic import ValidationError

from modules.techniques.bundles import ComponentEdit, PlanCorrection, PlannerOutput, apply_correction


def component(request):
    return {"request": request, "input_references": ["original_request:1"], "requirements": [{
        "description": f"Apply {request}", "kind": "authored_deliverable",
        "input_references": ["original_request:1"],
    }]}


def proposal():
    return PlannerOutput(components=[component("HTTP comparison"), component("Network reachability"),
                                     component("Filesystem ownership")])


def edit(action, ids, replacements=()):
    return PlanCorrection(edits=[ComponentEdit(action=action, component_ids=ids, replacements=replacements)])


@pytest.mark.parametrize("action,ids,replacements,expected", [
    ("remove", ["component-2"], [], ["HTTP comparison", "Filesystem ownership"]),
    ("merge", ["component-2", "component-1"], [component("Combined")], ["Combined", "Filesystem ownership"]),
    ("replace", ["component-2"], [component("TLS"), component("DNS")],
     ["HTTP comparison", "TLS", "DNS", "Filesystem ownership"]),
])
def test_apply_local_edits(action, ids, replacements, expected):
    original = proposal()
    frozen = original.model_dump()
    result = apply_correction(original, edit(action, ids, replacements))
    assert [item.request for item in result.components] == expected
    assert original.model_dump() == frozen
    assert result.components[-1].model_dump() == original.components[-1].model_dump()


@pytest.mark.parametrize("action,ids,replacements", [
    ("remove", ["unknown"], []),
    ("remove", ["component-1", "component-1"], []),
    ("remove", ["component-1"], [component("Invalid")]),
    ("merge", ["component-1"], [component("Invalid")]),
    ("merge", ["component-1", "component-2"], []),
    ("replace", ["component-1", "component-2"], [component("Invalid")]),
    ("replace", ["component-1"], []),
])
def test_invalid_edits_fail_atomically(action, ids, replacements):
    original = proposal()
    frozen = original.model_dump()
    with pytest.raises(ValueError):
        apply_correction(original, edit(action, ids, replacements))
    assert original.model_dump() == frozen


def test_conflicting_edits():
    removal = edit("remove", ["component-1"]).edits[0]
    with pytest.raises(ValueError, match="conflicting"):
        apply_correction(proposal(), PlanCorrection(edits=[removal, removal]))


def test_cannot_remove_entire_plan():
    with pytest.raises(ValidationError):
        apply_correction(proposal(), edit("remove", ["component-1", "component-2", "component-3"]))


def test_structured_correction_path(scripted_service, monkeypatch):
    from modules.techniques.bundles import BundlePlanReview

    service = scripted_service.service
    legacy = service.runner
    reviews = []

    class Runner:
        def structured(self, role, prompt, schema, tools):
            if schema is PlannerOutput:
                return proposal()
            if schema is PlanCorrection:
                assert "apply local edits" in prompt
                return edit("remove", ["component-2"])
            if schema is BundlePlanReview:
                reviews.append(prompt)
                if len(reviews) == 1:
                    return schema(approved=False, reason="Remove overlap", issues=[{
                        "rule": "overlap", "component_ids": ["component-2"],
                        "input_references": ["original_request:1"], "correction": "Remove overlap",
                    }])
                assert "review_history" in prompt
                return schema(approved=True, reason="Coverage retained")
            return schema.model_validate_json(legacy(role, prompt, tools))

    service.runner = Runner()
    monkeypatch.setattr(service, "get", lambda *args, **kwargs: ("revision", "Guidance"))
    bundle = service.resolve_bundle("HTTP, network and filesystem methods")
    assert [row.component.request for row in bundle.delivered] == ["HTTP comparison", "Filesystem ownership"]
    assert len(reviews) == 2


def test_exact_duplicates_merge_provenance_without_touching_distinct_requirements():
    from modules.techniques.bundles import deduplicate_plan

    first = component("HTTP comparison")
    second = component("HTTP comparison")
    second["input_references"] = ["original_request:2"]
    second["requirements"][0]["input_references"] = ["original_request:2"]
    third = component("HTTP comparison")
    third["requirements"][0]["description"] = "Different requested control"
    result = deduplicate_plan(PlannerOutput(components=[first, second, third]))
    assert len(result.components) == 1
    assert result.components[0].input_references == ["original_request:1", "original_request:2"]
    assert result.components[0].requirements[0].input_references == ["original_request:1", "original_request:2"]
    assert result.components[0].requirements[1].description == "Different requested control"


def test_identical_requests_keep_all_obligations_when_union_exceeds_schema_bound():
    from modules.techniques.bundles import deduplicate_plan

    first = component("HTTP comparison")
    first["requirements"] = [{**first["requirements"][0], "description": f"Control {i}"} for i in range(16)]
    second = component("HTTP comparison")
    second["requirements"][0]["description"] = "Additional control"
    result = deduplicate_plan(PlannerOutput(components=[first, second]))
    assert len(result.components) == 2
    assert sum(len(row.requirements) for row in result.components) == 17


def test_duplicate_requirement_origins_in_incoming_component_are_all_retained():
    from modules.techniques.bundles import deduplicate_plan

    first = component("HTTP comparison")
    second = component("HTTP comparison")
    second["requirements"] = [{**second["requirements"][0], "input_references": [reference]}
                              for reference in ["phase_guidance:1", "methodology_restriction:1"]]
    result = deduplicate_plan(PlannerOutput(components=[first, second]))
    assert result.components[0].requirements[0].input_references == [
        "original_request:1", "phase_guidance:1", "methodology_restriction:1",
    ]


def test_requirement_bound_applies_to_distinct_obligations_after_grouping():
    from modules.techniques.bundles import deduplicate_plan

    first = component("HTTP comparison")
    first["requirements"] = first["requirements"] * 16
    second = component("HTTP comparison")
    second["requirements"][0]["description"] = "Different control"
    result = deduplicate_plan(PlannerOutput(components=[first, second]))
    assert len(result.components) == 1
    assert [row.description for row in result.components[0].requirements] == [
        "Apply HTTP comparison", "Different control",
    ]
