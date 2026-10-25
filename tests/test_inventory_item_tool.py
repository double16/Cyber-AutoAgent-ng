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
    assert properties["view"]["enum"] == ["record", "investigation"]
    assert properties["context_limit"]["maximum"] == 50


def test_investigation_retains_oversized_entries_with_exact_locator(tmp_path, monkeypatch):
    import modules.tools.inventory_item as module

    entry = {"type": "input_candidate", "origins": ["large" * 10000]}
    manifest, reader = _reader_for(tmp_path, max_output_chars=1200)
    monkeypatch.setattr(module, "investigation_entries", lambda *_args, **_kwargs: [entry])
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view="investigation"))
    descriptor = result["context_entries"][0]
    saved = tmp_path / descriptor["entry_ref"].removeprefix("artifact:")
    assert json.loads(saved.read_text()) == entry
    assert result["next_context_offset"] is None and descriptor["incomplete"]
    assert manifest.exists()


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


@pytest.mark.parametrize("kwargs", [{"view": "unknown"}, {"context_offset": -1}, {"context_offset": True},
                                   {"context_limit": 0}, {"context_limit": 51}, {"context_limit": 1.5}])
def test_investigation_validates_runtime_arguments(kwargs):
    with pytest.raises(ValueError):
        create_inventory_item_reader()("artifact:inventory.json", item_id="endpoint-1", **kwargs)


def test_investigation_view_pages_relationships_without_changing_legacy_lookup(tmp_path):
    manifest, reader = _reader_for(tmp_path)
    original = manifest.read_bytes()
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        offset = 0
        entries = []
        while offset is not None:
            page = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1",
                                     view="investigation", context_offset=offset, context_limit=1))
            assert page["view"] == "investigation"
            entries.extend(page["context_entries"])
            offset = page["next_context_offset"]
        legacy = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view="raw"))
        alias = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view="context"))
        ambiguous = json.loads(reader("artifact:artifacts/inventory.json", target_id="target-1", view="investigation"))
    assert legacy["item"]["id"] == "endpoint-1"
    assert "context_entries" not in legacy
    assert alias["context_entries"] == entries
    assert ambiguous["status"] == "ambiguous" and "context_entries" not in ambiguous
    assert [entry["id"] for entry in entries if entry["type"] == "record"] == ["endpoint-1", "parameter-1"]
    assert manifest.read_bytes() == original


@pytest.mark.parametrize("restriction", [None, [], ["artifact:source.js"]])
def test_investigation_source_excerpts_respect_explicit_allowlist(tmp_path, restriction):
    manifest, _ = _reader_for(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["items"][0]["attributes"] = {
        "interaction": {"extensions": {"client_source_operations": [{
            "url": "/", "method": "GET", "source_artifact": "artifact:source.js",
            "source_location": {"start_line": 1, "end_line": 1},
        }]}},
    }
    manifest.write_text(json.dumps(payload))
    (tmp_path / "source.js").write_text('fetch("/")')
    with (patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
          patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path))):
        reader = create_inventory_item_reader(allowed_source_refs=restriction)
        page = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view="investigation"))
    excerpts = [entry for entry in page["context_entries"] if entry["type"] == "source_excerpt"]
    assert bool(excerpts) == (restriction is None or bool(restriction))
    if not excerpts:
        assert any(entry.get("code") == "source_unavailable" for entry in page["context_entries"])


def test_investigation_reuses_source_read_and_rejects_oversized_sources(tmp_path):
    manifest, _ = _reader_for(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["items"][0]["attributes"] = {"source_artifact_refs": ["artifact:source.js"],
        "interaction": {"extensions": {"client_source_operations": [{
            "url": "/", "source_artifact": "artifact:source.js",
            "source_location": {"start_line": 1, "end_line": 1},
        }]}}}
    manifest.write_text(json.dumps(payload))
    source = tmp_path / "source.js"
    source.write_text("x" * 16_000_001)
    with (patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
          patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path))):
        page = json.loads(create_inventory_item_reader()("artifact:artifacts/inventory.json",
                         item_id="endpoint-1", view="investigation"))
        assert any("inspection limit" in entry.get("detail", "") for entry in page["context_entries"])
        source.write_text('fetch("/")')
        page = json.loads(create_inventory_item_reader()("artifact:artifacts/inventory.json",
                         item_id="endpoint-1", view="investigation"))
        assert any(entry["type"] == "source_excerpt" for entry in page["context_entries"])


def test_allowed_manifest_references_are_enforced(tmp_path: Path):
    _manifest(tmp_path / "artifacts" / "inventory.json")
    _manifest(tmp_path / "artifacts" / "other.json")
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        reader = create_inventory_item_reader(allowed_manifest_refs=["artifact:artifacts/inventory.json"])
        assert reader.tool_spec["inputSchema"]["json"]["properties"]["manifest_ref"]["enum"] == [
            "artifact:artifacts/inventory.json",
        ]
        with pytest.raises(RuntimeError, match="not available"):
            reader("artifact:artifacts/other.json", item_id="endpoint-1")


@pytest.mark.parametrize("view", ["record", "investigation"])
def test_changed_frozen_snapshot_is_not_read(tmp_path: Path, view):
    _manifest(tmp_path / "artifacts" / "inventory.json")
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        reader = create_inventory_item_reader(
            allowed_manifest_refs=["artifact:artifacts/inventory.json"],
            expected_manifest_digests={"artifact:artifacts/inventory.json": "stale"},
        )
        result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view=view))

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


@pytest.mark.asyncio
@pytest.mark.parametrize("view,expected", [("record", "success"), ("investigation", "success"),
                                          ("context", "success"), ("raw", "success"), ("unknown", "error")])
async def test_investigation_view_is_validated_by_real_strands_runtime(tmp_path, view, expected):
    _, reader = _reader_for(tmp_path)
    with patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)):
        results = [result async for result in reader.stream({
            "toolUseId": "lookup", "name": "get_inventory_item", "input": {
                "manifest_ref": "artifact:artifacts/inventory.json", "item_id": "endpoint-1", "view": view,
            },
        }, {})]
    result = results[-1]["tool_result"]
    assert result["status"] == expected
    if expected == "success":
        payload = json.loads(result["content"][0]["text"])
        assert ("context_entries" in payload) == (view in {"investigation", "context"})


def test_investigation_resolves_legacy_route_without_rewriting_frozen_mapping(tmp_path):
    from tests.test_client_route_extraction import source

    manifest, reader = _reader_for(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["items"][0]["value"] = "https://example.test/control"
    payload["items"][0]["attributes"] = {"implementation_refs": ["artifact:legacy.js"],
        "interaction": {"operations": ["GET"], "operation_provenance": "inferred", "inputs": []}}
    manifest.write_text(json.dumps(payload))
    original = manifest.read_bytes()
    (tmp_path / "legacy.js").write_text(source())
    entries, offset = [], 0
    with (patch("modules.tools.memory._operation_output_root", return_value=str(tmp_path)),
          patch("modules.tools.artifact._operation_output_root", return_value=str(tmp_path))):
        while offset is not None:
            result = json.loads(reader("artifact:artifacts/inventory.json", item_id="endpoint-1", view="investigation",
                                       context_offset=offset, context_limit=20))
            if offset == 0:
                assert "sessionStorage" in json.dumps(result)
                assert "/api/catalog" in json.dumps(result) and "/api/permission" in json.dumps(result)
            entries.extend(result["context_entries"])
            offset = result["next_context_offset"]
    assert any(entry["type"] == "client_route" for entry in entries)
    assert {"Q", "check", "alias"} <= {entry["symbol"] for entry in entries if entry["type"] == "client_route_definition"}
    assert any("sessionStorage" in entry.get("text", "") for entry in entries)
    assert not any(entry["type"] == "client_operation" for entry in entries)
    assert manifest.read_bytes() == original
    assert list((tmp_path / "artifacts/client_route_indexes").glob("*.json"))
