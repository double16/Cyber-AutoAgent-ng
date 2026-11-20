import pytest

from modules.techniques.selection_review import SelectionReview, document_passages


def review(verdict="covered", **changes):
    row = {
        "requirement_id": "method", "verdict": verdict,
        "passages": [{"passage_id": 1, "quote": "Compare versions"}],
        "explanation": "The procedure supplies a version comparison",
        "missing_capability": "Requested control absent" if verdict == "unmet" else "",
    }
    row.update(changes)
    return SelectionReview(reason="Mapped procedure", judgments=[row])


@pytest.mark.parametrize("verdict,reusable", [("covered", True), ("conditional", True), ("unmet", False)])
def test_requirement_mapping_controls_reuse(verdict, reusable):
    result = review(verdict)
    result.validate_references([{"id": "method"}], {1: "Compare versions before inferring risk"})
    assert result.reusable is reusable


@pytest.mark.parametrize("change", [
    {"requirement_id": "foreign"},
    {"passages": [{"passage_id": 2, "quote": "Compare versions"}]},
    {"passages": [{"passage_id": 1, "quote": "Invented procedure"}]},
    {"missing_capability": ""},
])
def test_invalid_mapping_is_unresolved(change):
    with pytest.raises(ValueError):
        review("unmet", **change).validate_references([{"id": "method"}], {1: "Compare versions"})


def test_duplicate_requirements_are_invalid():
    result = review()
    result.judgments.append(result.judgments[0])
    with pytest.raises(ValueError, match="exactly once"):
        result.validate_references([{"id": "method"}], {1: "Compare versions"})


def test_unique_exact_quote_resolves_mistaken_known_passage_number():
    result = review()
    result.validate_references([{"id": "method"}], {1: "Introduction", 2: "Compare versions"})
    assert result.judgments[0].passages[0].passage_id == 2


def test_ambiguous_quote_cannot_repair_a_passage_number():
    with pytest.raises(ValueError, match="quote the submitted revision"):
        review().validate_references([{"id": "method"}], {
            1: "Introduction", 2: "Compare versions", 3: "Compare versions again",
        })


def test_numbered_passages_preserve_single_line_and_unicode():
    text = "## H3: Version prerequisites\n" + "λ" * 3400
    passages = document_passages(text)
    assert "".join(passages.values()) == text
    assert list(passages) == [1, 2, 3]


def test_controller_resolves_passage_without_model_quote():
    result = review(passages=[{"passage_id": 1}])
    result.validate_references([{"id": "method"}], {1: "Compare versions"})
    assert result.reusable


def test_absent_requested_capability_does_not_need_nonexistent_quote():
    result = review("unmet", passages=[])
    result.validate_references([{"id": "method"}], {1: "Other procedure"})
    assert not result.reusable


def test_covered_requirement_requires_a_real_passage():
    with pytest.raises(ValueError, match="require a passage"):
        review(passages=[]).validate_references([{"id": "method"}], {1: "Other"})


@pytest.mark.parametrize("verdict", ["covered", "conditional", "unmet"])
@pytest.mark.parametrize("fault", [None, "foreign", "missing", "empty", "whitespace", "no_passage", "extra"])
def test_bound_selection_native_and_runtime_tool_schemas(verdict, fault):
    from copy import deepcopy

    from jsonschema import Draft202012Validator
    from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

    from modules.techniques.selection_review import selection_output_model

    model = selection_output_model([{"id": "method"}])
    data = review(verdict).model_dump()
    row = data["judgments"][0]
    if fault == "foreign":
        row["requirement_id"] = "invented"
    elif fault == "missing":
        row.pop("missing_capability")
    elif fault == "empty":
        row["missing_capability"] = ""
    elif fault == "whitespace":
        row["missing_capability"] = "  "
    elif fault == "no_passage":
        row["passages"] = []
    elif fault == "extra":
        data["judgments"].append(deepcopy(row))
    invalid = (fault in {"foreign", "extra"}
               or (verdict == "unmet" and fault in {"missing", "empty", "whitespace"})
               or (verdict != "unmet" and fault == "no_passage"))
    for schema in (model.model_json_schema(), StructuredOutputTool(model).tool_spec["inputSchema"]["json"]):
        assert Draft202012Validator(schema).is_valid(data) is not invalid
    if invalid:
        with pytest.raises(ValueError):
            model.model_validate(data)
    else:
        bound = model.model_validate(data)
        canonical = SelectionReview.model_validate(bound.model_dump())
        canonical.validate_references([{"id": "method"}], {1: "Compare versions"})
        assert canonical.reusable == (verdict != "unmet")


@pytest.mark.parametrize("requirements", [[], [{"id": ""}], [{"id": " "}], [{"id": 4}],
                                         [{"id": "method"}, {"id": "method"}]])
def test_bound_selection_rejects_invalid_requirement_ids(requirements):
    from modules.techniques.selection_review import selection_output_model

    with pytest.raises(ValueError, match="unique requirement IDs"):
        selection_output_model(requirements)


def test_bound_selection_keeps_complete_coverage_controller_owned():
    from modules.techniques.selection_review import selection_output_model

    requirements = [{"id": "first"}, {"id": "second"}]
    model = selection_output_model(requirements)
    data = review().model_dump()
    data["judgments"][0]["requirement_id"] = "first"
    data["judgments"] *= 2
    bound = model.model_validate(data)
    with pytest.raises(ValueError, match="exactly once"):
        bound.validate_references(requirements, {1: "Compare versions"})
    data["judgments"] = [dict(row) for row in data["judgments"]]
    data["judgments"][1]["requirement_id"] = "second"
    model.model_validate(data).validate_references(requirements, {1: "Compare versions"})


@pytest.mark.parametrize("capability", ["After-expiry comparison", "A comparison\nwith an expired credential"])
def test_missing_capability_pattern_allows_complete_native_grammar_strings(capability):
    import re

    from modules.techniques.selection_review import selection_output_model

    schema = selection_output_model([{"id": "method"}]).model_json_schema()
    pattern = schema["$defs"]["UnmetRequirementJudgment"]["properties"]["missing_capability"]["pattern"]
    # Some native grammar compilers match the entire string, rather than JSON Schema's substring semantics.
    assert re.fullmatch(pattern, capability)
    assert re.fullmatch(pattern, " \n ") is None
