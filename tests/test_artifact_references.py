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
