import json
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.tools.inventory_item import create_inventory_item_reader


def _manifest(path: Path, *, schema_version: bool = True, duplicate_value: bool = False) -> Path:
    items = [
        {
            "id": "endpoint-1",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "https://example.test/",
            "attributes": {},
        },
        {
            "id": "parameter-1",
            "target_id": "target-1",
            "kind": "parameter",
            "value": "q",
            "attributes": {"endpoint_id": "endpoint-1"},
        },
    ]
    if duplicate_value:
        items.append(
            {
                "id": "endpoint-2",
                "target_id": "target-2",
                "kind": "endpoint",
                "value": "https://example.test/",
                "attributes": {},
            }
        )
    payload = {"items": items, "unassessed_gaps": []}
    if schema_version:
        payload["schema_version"] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _reader_for(tmp_path: Path, **kwargs):
    manifest = _manifest(tmp_path / "artifacts" / "inventory.json")
    reader = create_inventory_item_reader(**kwargs)
    return manifest, reader


def test_schema_advertises_inventory_selectors():
    reader = create_inventory_item_reader()
    tool_spec = reader.tool_spec
    properties = tool_spec["inputSchema"]["json"]["properties"]

    assert reader.tool_name == "get_inventory_item"
    description = tool_spec["description"]
    assert "Combine selectors to narrow results" in description
    assert "multiple matches return compact candidate records" in description
    assert "read_artifact" in description
    assert "search_artifact" in description
    assert properties["kind"]["enum"] == ["endpoint", "parameter", "workflow", "service", "technology"]
    assert "list matches" in properties["kind"]["description"]
    assert "parameters linked to that endpoint" in properties["endpoint_id"]["description"]
    assert reader.tool_spec["inputSchema"]["json"]["required"] == ["manifest_ref"]


def test_exact_id_and_endpoint_related_parameter_lookup(tmp_path: Path):
    manifest, reader = _reader_for(tmp_path)
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        exact = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1"))
        parameter = json.loads(
            reader("artifact:artifacts/inventory.json", kind="parameter", endpoint_id="endpoint-1")
        )

    assert exact["status"] == "found"
    assert exact["item"]["value"] == "https://example.test/"
    assert exact["manifest_ref"] == "artifact:artifacts/inventory.json"
    assert len(exact["manifest_sha256"]) == 64
    assert parameter["status"] == "found"
    assert parameter["item"]["id"] == "parameter-1"
    assert manifest.is_file()


def test_not_found_and_ambiguous_results_are_explicit_and_bounded(tmp_path: Path):
    _manifest(tmp_path / "artifacts" / "inventory.json", duplicate_value=True)
    reader = create_inventory_item_reader()
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        missing = json.loads(reader("artifact:artifacts/inventory.json", item_id="absent"))
        ambiguous = json.loads(
            reader("artifact:artifacts/inventory.json", kind="endpoint", value="https://example.test/")
        )

    assert missing["status"] == "not_found"
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["match_count"] == 2
    assert {item["id"] for item in ambiguous["matches"]} == {"endpoint-1", "endpoint-2"}
    assert ambiguous["truncated"] is False


def test_ambiguous_match_list_respects_output_limit(tmp_path: Path):
    items = [
        {
            "id": f"endpoint-{index}",
            "target_id": f"target-{index}",
            "kind": "endpoint",
            "value": "https://example.test/",
            "attributes": {},
        }
        for index in range(24)
    ]
    manifest_path = tmp_path / "artifacts" / "inventory.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"schema_version": 1, "items": items, "unassessed_gaps": []}))
    reader = create_inventory_item_reader(max_output_chars=600)
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        output = reader("artifact:artifacts/inventory.json", kind="endpoint")

    response = json.loads(output)
    assert len(output) <= 600
    assert response["match_count"] == 24
    assert response["truncated"] is True


def test_selector_validation_rejects_empty_and_unknown_kind(tmp_path: Path):
    _manifest(tmp_path / "artifacts" / "inventory.json")
    reader = create_inventory_item_reader()
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="Provide item_id"):
            reader("artifact:artifacts/inventory.json")
        with pytest.raises(ValueError, match="kind must be one of"):
            reader("artifact:artifacts/inventory.json", kind="unknown")


def test_lookup_never_persists_manifest_normalization(tmp_path: Path):
    manifest = _manifest(tmp_path / "artifacts" / "inventory.json", schema_version=False)
    original = manifest.read_bytes()
    reader = create_inventory_item_reader()
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1"))

    assert result["status"] == "found"
    assert manifest.read_bytes() == original


def test_allowed_manifest_references_are_enforced(tmp_path: Path):
    _manifest(tmp_path / "artifacts" / "inventory.json")
    _manifest(tmp_path / "artifacts" / "other.json")
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        reader = create_inventory_item_reader(allowed_manifest_refs=["artifact:artifacts/inventory.json"])
        with pytest.raises(RuntimeError, match="not available"):
            reader("artifact:artifacts/other.json", item_id="endpoint-1")


def test_changed_frozen_snapshot_is_not_read(tmp_path: Path):
    _manifest(tmp_path / "artifacts" / "inventory.json")
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        reader = create_inventory_item_reader(
            allowed_manifest_refs=["artifact:artifacts/inventory.json"],
            expected_manifest_digests={"artifact:artifacts/inventory.json": "stale"},
        )
        result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1"))

    assert result == {"manifest_ref": "artifact:artifacts/inventory.json", "status": "snapshot_changed"}


def test_item_larger_than_result_cap_returns_navigation_guidance(tmp_path: Path):
    manifest = _manifest(tmp_path / "artifacts" / "inventory.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["items"][0]["attributes"] = {"notes": "x" * 2_000}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    reader = create_inventory_item_reader(max_output_chars=256)
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1"))

    assert result["status"] == "item_too_large"
    assert result["item_id"] == "endpoint-1"
    assert "read_artifact" in result["guidance"]


def test_manifest_reference_outside_operation_is_rejected(tmp_path: Path):
    reader = create_inventory_item_reader()
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        with pytest.raises(ValueError, match="outside the current operation"):
            reader("artifact:../../outside.json", item_id="endpoint-1")
