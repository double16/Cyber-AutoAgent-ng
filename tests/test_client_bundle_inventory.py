import json
from pathlib import Path

import pytest

from modules.handlers.utils import get_tool_spec
from modules.operation_plugins.web.tools import client_bundle_inventory as bundle_tool


def _operation_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "modules.tools.artifact._operation_output_root", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "modules.tools.memory._operation_output_root", lambda: str(tmp_path)
    )


def test_client_bundle_inventory_exposes_required_runtime_schema():
    schema = get_tool_spec(bundle_tool.client_bundle_inventory)["inputSchema"]["json"]

    assert schema["required"] == [
        "source_artifact",
        "output_file",
        "inventory_manifest",
    ]
    assert set(schema["properties"]) == {
        "source_artifact",
        "output_file",
        "inventory_manifest",
        "target",
        "target_id",
    }


def test_client_bundle_inventory_writes_extraction_and_scoped_manifest(
    monkeypatch, tmp_path: Path
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bundle = artifacts / "app.js"
    bundle.write_text(
        """
        const products = \"/api/products\";
        const profile = \"/userprofile\";
        const asset = \"/assets/app.js\";
        sessionStorage.setItem(\"token\", value);
        fetch(url, { headers: { authorization: sessionStorage.getItem(\"token\") }, credentials: \"include\" });
        //# sourceMappingURL=app.js.map
        const remote = \"https://example.invalid/api/ignored\";
        """,
        encoding="utf-8",
    )
    _operation_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bundle_tool,
        "resolve_inventory_target",
        lambda *_args: ("http://target.test:3000", "target-1"),
    )

    result = json.loads(
        bundle_tool.client_bundle_inventory(
            "artifact:artifacts/app.js",
            "artifacts/bundle-inventory.json",
            "artifacts/inventory-manifest.json",
        )
    )

    extraction = json.loads(
        (artifacts / "bundle-inventory.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (artifacts / "inventory-manifest.json").read_text(encoding="utf-8")
    )
    assert result["api_path_count"] == 1
    assert extraction["api_paths"] == ["/api/products"]
    assert extraction["spa_routes"] == ["/userprofile"]
    assert extraction["auth_storage_keys"] == ["token"]
    assert extraction["source_map_references"] == ["app.js.map"]
    assert extraction["external_origins"] == ["https://example.invalid"]
    assert all("example.invalid" not in item["value"] for item in manifest["items"])
    assert any(
        item["value"] == "http://target.test:3000/api/products"
        for item in manifest["items"]
    )


def test_client_bundle_inventory_writes_target_service_for_empty_bundle(
    monkeypatch, tmp_path: Path
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "empty.js").write_text("minified", encoding="utf-8")
    _operation_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bundle_tool,
        "resolve_inventory_target",
        lambda *_args: ("https://target.test", "target-1"),
    )

    bundle_tool.client_bundle_inventory(
        "artifact:artifacts/empty.js",
        "artifacts/empty-extraction.json",
        "artifacts/empty-manifest.json",
    )

    manifest = json.loads(
        (artifacts / "empty-manifest.json").read_text(encoding="utf-8")
    )
    assert [item["kind"] for item in manifest["items"]] == ["service", "endpoint"]


def test_client_bundle_inventory_rejects_output_outside_operation(
    monkeypatch, tmp_path: Path
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "app.js").write_text("const api = '/api/products';", encoding="utf-8")
    _operation_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bundle_tool,
        "resolve_inventory_target",
        lambda *_args: ("https://target.test", "target-1"),
    )

    with pytest.raises(ValueError, match="output_file must remain"):
        bundle_tool.client_bundle_inventory(
            "artifact:artifacts/app.js",
            "../outside.json",
            "artifacts/manifest.json",
        )
