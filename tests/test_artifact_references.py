import pytest
from strands.hooks.events import BeforeToolCallEvent

from modules.tools.artifact_references import (
    ArtifactReferenceInputNormalizationHook,
    normalize_artifact_reference_token,
    split_delimited_reference_values,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("artifact:artifacts/proof.txt,", "artifact:artifacts/proof.txt"),
        ("`artifact:artifacts/proof.txt`", "artifact:artifacts/proof.txt"),
        ("[artifact:task_evidence/task/proof.txt]", "artifact:task_evidence/task/proof.txt"),
    ],
)
def test_normalize_artifact_reference_token_removes_presentation_punctuation(raw, expected):
    assert normalize_artifact_reference_token(raw) == expected


def test_split_delimited_reference_values_accepts_agent_compatible_delimiters():
    assert split_delimited_reference_values(
        "artifact:artifacts/one.txt, artifact:artifacts/two.txt|artifact:artifacts/three.txt\n"
        "artifact:artifacts/four.txt",
        allow_delimited_strings=True,
    ) == [
        "artifact:artifacts/one.txt",
        "artifact:artifacts/two.txt",
        "artifact:artifacts/three.txt",
        "artifact:artifacts/four.txt",
    ]


def test_split_delimited_reference_values_rejects_empty_internal_values():
    with pytest.raises(ValueError, match="empty value"):
        split_delimited_reference_values(
            "artifact:artifacts/one.txt,,artifact:artifacts/two.txt",
            allow_delimited_strings=True,
        )


@pytest.mark.parametrize("value", [None, ("artifact:artifacts/one.txt",)])
def test_split_delimited_reference_values_accepts_empty_and_tuple_values(value):
    expected = [] if value is None else ["artifact:artifacts/one.txt"]

    assert split_delimited_reference_values(value, allow_delimited_strings=False) == expected


@pytest.mark.parametrize("value", [None, 7, "   "])
def test_normalize_artifact_reference_token_rejects_invalid_values(value):
    with pytest.raises((TypeError, ValueError)):
        normalize_artifact_reference_token(value)


@pytest.mark.parametrize(
    ("tool_name", "field_name"),
    [
        ("store_observation", "artifacts"),
        ("store_finding", "artifacts"),
        ("record_finding_validation", "evidence_artifacts"),
        ("record_finding_validation", "control_artifacts"),
        ("record_task_acceptance", "evidence_refs"),
        ("store_objective_candidate", "evidence_artifacts"),
        ("record_objective_validation", "evidence_artifacts"),
        ("discover_flag_candidates", "evidence_artifacts"),
    ],
)
def test_reference_input_hook_normalizes_scalar_and_delimited_list_values(tool_name, field_name):
    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={
            "toolUseId": "tool-1",
            "name": tool_name,
            "input": {
                field_name: [
                    "artifact:artifacts/one.txt, artifact:artifacts/two.txt",
                    "artifact:artifacts/three.txt",
                ]
            },
        },
        invocation_state={},
    )

    ArtifactReferenceInputNormalizationHook()._normalize_tool_input(event)

    assert event.tool_use["input"][field_name] == [
        "artifact:artifacts/one.txt",
        "artifact:artifacts/two.txt",
        "artifact:artifacts/three.txt",
    ]


def test_reference_input_hook_normalizes_scalar_string_values():
    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={
            "toolUseId": "tool-1",
            "name": "store_finding",
            "input": {"artifacts": "artifact:artifacts/one.txt|artifact:artifacts/two.txt"},
        },
        invocation_state={},
    )

    ArtifactReferenceInputNormalizationHook()._normalize_tool_input(event)

    assert event.tool_use["input"]["artifacts"] == [
        "artifact:artifacts/one.txt",
        "artifact:artifacts/two.txt",
    ]


def test_reference_input_hook_leaves_invalid_reference_values_for_standard_validation():
    tool_input = {"artifacts": {"reference": "artifact:artifacts/one.txt"}}
    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"toolUseId": "tool-1", "name": "store_finding", "input": tool_input},
        invocation_state={},
    )

    ArtifactReferenceInputNormalizationHook()._normalize_tool_input(event)

    assert event.tool_use["input"] == tool_input


@pytest.mark.parametrize(
    "tool_use",
    [
        {"name": "unknown_tool", "input": {"artifacts": "artifact:artifacts/one.txt"}},
        {"name": "store_finding", "input": None},
        {"name": "store_finding", "input": {"unrelated": "value"}},
        {"name": "store_finding", "input": {"artifacts": None}},
    ],
)
def test_reference_input_hook_leaves_unrelated_or_already_valid_inputs_unchanged(tool_use):
    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
    )

    ArtifactReferenceInputNormalizationHook()._normalize_tool_input(event)

    assert event.tool_use is tool_use


@pytest.mark.parametrize("raw,expected", [
    ("artifact: /tmp/page.html", "artifact:/tmp/page.html"),
    ("ARTIFACT_ID: page.html", "artifact_id:page.html"),
    ('artifact: "/tmp/page with spaces.html"', "artifact:/tmp/page with spaces.html"),
    ('"artifact: /tmp/page with spaces.html"', "artifact:/tmp/page with spaces.html"),
    ("artifact:artifacts/page.html", "artifact:artifacts/page.html"),
])
def test_normalize_spaced_and_quoted_references_is_idempotent(raw, expected):
    assert normalize_artifact_reference_token(raw) == expected
    assert normalize_artifact_reference_token(expected) == expected


@pytest.mark.parametrize("raw", ["artifact:", "artifact: ", "artifact_id: ", 'artifact:""', 'artifact:" "'])
def test_normalize_rejects_empty_prefixed_references(raw):
    with pytest.raises(ValueError, match="empty"):
        normalize_artifact_reference_token(raw)


def test_shared_extraction_preserves_structured_paths_and_deduplicates():
    from modules.tools.artifact_references import extract_artifact_references

    assert extract_artifact_references(
        {"artifact_ref": "artifact: /tmp/one with spaces.html", "artifact_id": "page.html"},
        ["HTML content saved to artifact: /tmp/one.html.", "artifact_id: page.html"],
        {"ignored": 7},
    ) == ["artifact:/tmp/one with spaces.html", "artifact_id:page.html", "artifact:/tmp/one.html"]


@pytest.mark.parametrize("text,expected", [
    ("HTML content saved to artifact: /tmp/page.html", ["artifact:/tmp/page.html"]),
    ("Screenshot saved to artifact: /tmp/page.png", ["artifact:/tmp/page.png"]),
    ("artifact:artifacts/a.txt, artifact_id: b.txt|artifact: artifacts/c.txt",
     ["artifact:artifacts/a.txt", "artifact_id:b.txt", "artifact:artifacts/c.txt"]),
    ('[artifact: "/tmp/one with spaces.html"]', ["artifact:/tmp/one with spaces.html"]),
    ('See `artifact: artifacts/a b.txt`.', ["artifact:artifacts/a b.txt"]),
    ("https://example.test/artifact:page.html", []),
    ("artifact:\nartifact_id:", []),
    ('"artifact: "', []),
    ("memory:item finding:item", []),
])
def test_shared_text_extraction(text, expected):
    from modules.tools.artifact_references import extract_artifact_references

    assert extract_artifact_references(text) == expected


def test_shared_match_spans_preserve_surrounding_punctuation():
    from modules.tools.artifact_references import iter_artifact_reference_matches, replace_artifact_references

    text = 'See artifact: artifacts/a.txt, then `artifact: "/tmp/a b.txt"`. Done.'
    matches = list(iter_artifact_reference_matches(text))
    assert len(matches) == 2
    for match in matches:
        assert text[match.start:match.end] == match.raw
    replaced, count = replace_artifact_references(text, lambda reference: f"<{reference}>")
    assert count == 2
    assert replaced == "See <artifact:artifacts/a.txt>, then <artifact:/tmp/a b.txt>. Done."
    assert replace_artifact_references("unchanged", lambda _: "bad") == ("unchanged", 0)


def test_shared_extraction_supports_opt_in_legacy_paths_and_labels():
    from modules.tools.artifact_references import extract_absolute_artifact_paths, extract_artifact_references

    text = "artifacts/a.txt outputs/run/artifacts/b.txt artifact paths: /tmp/proof.txt"
    assert extract_artifact_references(text) == []
    assert extract_artifact_references(text, include_bare_paths=True, allow_legacy_labels=True) == [
        "artifact:artifacts/a.txt", "artifact:outputs/run/artifacts/b.txt", "artifact:/tmp/proof.txt",
    ]
    assert extract_absolute_artifact_paths("Saved /tmp/a.txt.") == ["/tmp/a.txt"]
    assert extract_artifact_references({"artifact_id": "", "artifact_ref": "artifact: "}) == []



def test_legacy_proof_label_preserves_multiple_paths():
    from modules.tools.artifact_references import extract_artifact_references

    assert extract_artifact_references(
        "artifact paths: /tmp/first.txt,/tmp/second.txt", allow_legacy_labels=True,
    ) == ["artifact:/tmp/first.txt", "artifact:/tmp/second.txt"]
