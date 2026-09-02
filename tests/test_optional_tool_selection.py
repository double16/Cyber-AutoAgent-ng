import pytest
import yaml

from modules.tools import optional_tool_selection as selection
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    Task,
)


def _task(*, output_kind="artifact", evidence_kind="artifact"):
    return Task(
        task_uid="task-1",
        title="Bounded task",
        objective="Produce the declared output",
        phase=1,
        status="pending",
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded procedure",
                source_refs=["target:target-1"],
                procedure={
                    "methods": ["analyze"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": output_kind,
                },
            ),
            criteria=[
                AcceptanceCriterion(
                    id="criterion-1",
                    description="Store the declared output",
                    evidence_requirements=[EvidenceRequirement(kind=evidence_kind)],
                )
            ],
        ),
    )


def _snapshot_task(*, evidence_kind):
    return Task(
        task_uid="snapshot-task",
        title="Snapshot task",
        objective="Assess the frozen snapshot",
        phase=1,
        status="pending",
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Frozen task input",
                source_refs=["artifact:artifacts/input.json"],
            ),
            criteria=[
                AcceptanceCriterion(
                    id="criterion-1",
                    description="Store the declared output",
                    evidence_requirements=[EvidenceRequirement(kind=evidence_kind)],
                )
            ],
        ),
    )


def test_required_optional_tools_match_inventory_output_or_evidence():
    expected = [
        "recon_output_to_inventory_manifest",
        "specialized_recon_orchestrator",
        "auth_chain_analyzer",
    ]
    assert selection.required_optional_tool_names(
        _task(output_kind="inventory_manifest", evidence_kind="inventory_manifest")
    ) == expected
    assert selection.required_optional_tool_names(
        _snapshot_task(evidence_kind="inventory_manifest")
    ) == expected


def test_required_optional_tools_ignore_generic_artifact_contracts():
    assert selection.required_optional_tool_names(_task()) == []


def test_optional_tool_selection_catalog_rejects_invalid_rule(monkeypatch, tmp_path):
    catalog = tmp_path / "optional_tool_selection.yaml"
    catalog.write_text("version: 1\nrules:\n  - id: invalid\n    output_kinds: [unknown]\n    tools: [tool]\n")
    monkeypatch.setattr(selection, "_CATALOG_PATH", catalog)

    try:
        selection.load_optional_tool_selection_rules()
    except ValueError as error:
        assert "invalid" in str(error)
    else:
        raise AssertionError("invalid optional tool selection rule was accepted")


@pytest.mark.parametrize(
    "content, error_type, message",
    [
        ("version: 2\nrules: []\n", ValueError, "version 1"),
        ("version: 1\nrules: invalid\n", TypeError, "must be a list"),
        ("version: 1\nrules: [invalid]\n", TypeError, "must be objects"),
    ],
)
def test_optional_tool_selection_catalog_rejects_invalid_catalog_shapes(
    monkeypatch, tmp_path, content, error_type, message
):
    catalog = tmp_path / "optional_tool_selection.yaml"
    catalog.write_text(content, encoding="utf-8")
    monkeypatch.setattr(selection, "_CATALOG_PATH", catalog)

    with pytest.raises(error_type, match=message):
        selection.load_optional_tool_selection_rules()


def test_optional_tool_selection_catalog_wraps_reader_and_yaml_errors(monkeypatch, tmp_path):
    missing_catalog = tmp_path / "missing.yaml"
    monkeypatch.setattr(selection, "_CATALOG_PATH", missing_catalog)

    with pytest.raises(ValueError, match="unavailable"):
        selection.load_optional_tool_selection_rules()

    monkeypatch.setattr(selection.yaml, "safe_load", lambda _content: (_ for _ in ()).throw(yaml.YAMLError("bad yaml")))
    with pytest.raises(ValueError, match="unavailable"):
        selection.load_optional_tool_selection_rules()


def test_optional_tool_selection_catalog_strips_and_deduplicates_tool_names(monkeypatch, tmp_path):
    catalog = tmp_path / "optional_tool_selection.yaml"
    catalog.write_text(
        """version: 1
rules:
  - id: scoped-tool
    output_kinds: [artifact]
    evidence_requirement_kinds: []
    tools: [" analyzer ", analyzer]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(selection, "_CATALOG_PATH", catalog)

    assert selection.load_optional_tool_selection_rules() == [
        {
            "id": "scoped-tool",
            "output_kinds": frozenset({"artifact"}),
            "evidence_requirement_kinds": frozenset(),
            "tools": ("analyzer",),
        }
    ]
