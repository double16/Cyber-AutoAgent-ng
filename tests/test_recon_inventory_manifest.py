import json
from types import SimpleNamespace

import pytest

from modules.handlers.utils import get_tool_spec
from modules.operation_plugins.web.tools import recon_inventory_manifest as manifest_tool
from modules.prompts.factory import ModulePromptLoader
from modules.tools import artifact
from modules.tools import memory
from modules.tools.recon_inventory_manifest import recon_output_to_inventory_manifest


@pytest.mark.parametrize(
    ("source_format", "text", "expected_url", "expected_status"),
    [
        (
            "katana",
            '{"request":{"endpoint":"https://target.test/a","method":"POST"},'
            '"response":{"status_code":201}}',
            "https://target.test/a",
            201,
        ),
        ("feroxbuster", '{"url":"https://target.test/admin","status":403}', "https://target.test/admin", 403),
        (
            "ffuf",
            '{"results":[{"url":"https://target.test/api","status":200}]}',
            "https://target.test/api",
            200,
        ),
        ("gobuster", "https://target.test/backup Status: 301", "https://target.test/backup", 301),
        ("dirsearch", "[200] https://target.test/login", "https://target.test/login", None),
        (
            "httpx",
            '{"url":"https://target.test/","status_code":200,"tech":["nginx"]}',
            "https://target.test/",
            200,
        ),
        ("gospider", "[url] - https://target.test/assets/app.js", "https://target.test/assets/app.js", None),
        ("url_list", "https://target.test/plain", "https://target.test/plain", None),
    ],
)
def test_supported_recon_parsers_extract_urls(source_format, text, expected_url, expected_status):
    records = manifest_tool.PARSERS[source_format](text)

    assert records[0]["url"] == expected_url
    assert records[0]["status"] == expected_status


def test_ffuf_parser_accepts_csv_and_rejects_relative_fuzz_input():
    csv_records = manifest_tool._parse_ffuf("url,status\nhttps://target.test/csv,204\n")
    relative_records = manifest_tool._parse_ffuf('{"results":[{"input":{"FUZZ":"admin"},"status":200}]}')

    assert csv_records[0]["url"] == "https://target.test/csv"
    assert csv_records[0]["status"] == 204
    assert relative_records == []


def test_gobuster_parser_extracts_default_relative_path_output():
    records = manifest_tool._parse_gobuster("/admin (Status: 301) [Size: 0]")

    assert records == [{"url": "/admin", "method": "GET", "status": 301, "technologies": []}]


def test_format_inference_handles_structured_formats_and_url_lists():
    assert manifest_tool._infer_format('{"request":{"endpoint":"https://target.test"}}') == "katana"
    assert manifest_tool._infer_format('{"results":[],"ffufhash":"x"}') == "ffuf"
    assert manifest_tool._infer_format('{"input":"target.test","status_code":200,"tech":[]}') == "httpx"
    assert manifest_tool._infer_format('{"type":"response","url":"https://target.test"}') == "feroxbuster"
    assert manifest_tool._infer_format("https://target.test/a\nhttps://target.test/b\n") == "url_list"
    assert manifest_tool._infer_format(
        '{"meta":{"format":"recon_result_v1"},"endpoints":["https://target.test/a"]}'
    ) == "specialized_recon"
    assert manifest_tool._infer_format(
        '{"auth_endpoints":[{"full_url":"https://target.test/login"}],"flow_analysis":{}}'
    ) == "auth_chain"
    with pytest.raises(ValueError, match="Unable to infer"):
        manifest_tool._infer_format("not recon output")


@pytest.mark.parametrize(
    ("source_format", "payload", "expected_kind"),
    [
        (
            "specialized_recon",
            {
                "meta": {"format": "recon_result_v1"},
                "live_hosts": ["https://target.test"],
                "endpoints": ["https://target.test/app?q=1"],
                "technologies": ["nginx"],
                "parameters": ["csrf"],
            },
            "technology",
        ),
        (
            "auth_chain",
            {
                "auth_endpoints": [{"full_url": "https://target.test/login", "status": 200}],
                "auth_mechanisms": [{"type": "Session-based"}],
                "flow_analysis": {"authentication_steps": ["Submit credentials"]},
            },
            "workflow",
        ),
    ],
)
def test_native_recon_formats_preserve_structured_inventory_context(source_format, payload, expected_kind):
    records = manifest_tool.PARSERS[source_format](json.dumps(payload))
    workflows, technologies, parameters = manifest_tool._structured_inventory_fields(json.dumps(payload), source_format)
    manifest = manifest_tool.records_to_inventory_manifest(
        records,
        target_id="target-1",
        target="https://target.test",
        workflows=workflows,
        technologies=technologies,
        parameters=parameters,
    )

    assert any(item["kind"] == "endpoint" for item in manifest["items"])
    assert any(item["kind"] == expected_kind for item in manifest["items"])


@pytest.mark.parametrize(
    "text",
    [
        "https://target.test/one",
        "\nhttps://target.test/one\n\nhttp://target.test/two\n",
        "\n".join(f"https://target.test/{index}" for index in range(5)),
    ],
)
def test_url_list_auto_detection_accepts_one_through_five_non_empty_lines(text):
    assert manifest_tool._infer_format(text) == "url_list"


def test_url_list_auto_detection_checks_only_first_five_non_empty_lines():
    first_five = [f"https://target.test/{index}" for index in range(5)]
    text = "\n\n".join([*first_five, "not-a-url", "https://target.test/later"])

    assert manifest_tool._infer_format(text) == "url_list"
    records = manifest_tool._parse_url_list(text)
    manifest = manifest_tool.records_to_inventory_manifest(records, target_id="target-1")
    assert manifest["extraction"]["candidate_count"] == 7
    assert manifest["unassessed_gaps"] == [{"reason": "unparseable_url", "value": "not-a-url"}]


@pytest.mark.parametrize(
    "text",
    [
        "https://target.test/one\nnot-a-url\nhttps://target.test/three",
        "https://target.test/one extra",
        "/relative/path\nhttps://target.test/two",
        "\n\n",
    ],
)
def test_url_list_auto_detection_rejects_invalid_sample_lines(text):
    with pytest.raises(ValueError, match="Unable to infer"):
        manifest_tool._infer_format(text)


def test_manifest_builder_deduplicates_and_records_parameters_scope_and_gaps():
    records = [
        {
            "url": "HTTPS://TARGET.TEST:443/search?q=one&q=two#fragment",
            "method": "POST",
            "status": 200,
            "technologies": ["nginx", "nginx"],
        },
        {"url": "https://target.test/search?q=one", "method": "GET"},
        {"url": "https://other.test/out", "method": "GET"},
        {"url": "not-a-url", "method": "GET"},
    ]

    first = manifest_tool.records_to_inventory_manifest(
        records,
        target_id="target-7",
        target="https://target.test",
        source_ref="artifact:artifacts/recon.json",
        workflows=[{"value": "Sign in"}, {"description": "Sign in"}],
        technologies=["Python"],
        parameters=[{"name": "csrf"}, "state"],
    )
    second = manifest_tool.records_to_inventory_manifest(
        records,
        target_id="target-7",
        target="https://target.test",
    )

    kinds = [item["kind"] for item in first["items"]]
    assert kinds.count("service") == 1
    assert kinds.count("endpoint") == 1
    assert kinds.count("parameter") == 3
    assert kinds.count("technology") == 2
    assert kinds.count("workflow") == 1
    assert {gap["reason"] for gap in first["unassessed_gaps"]} == {"out_of_scope", "unparseable_url"}
    assert first["items"][0]["target_id"] == "target-7"
    assert [item["id"] for item in second["items"]] == [
        item["id"] for item in first["items"] if item["kind"] not in {"workflow"} and item["value"] != "Python"
        and item["value"] not in {"csrf", "state"}
    ]


def test_converter_tool_schema_advertises_canonical_formats():
    schema = get_tool_spec(manifest_tool.recon_output_to_inventory_manifest)["inputSchema"]["json"]

    assert schema["properties"]["source_format"]["enum"] == ["auto", *manifest_tool.SUPPORTED_RECON_FORMATS]
    assert set(schema["required"]) == {"source_artifact", "output_file"}


def test_converter_reads_artifact_normalizes_alias_and_writes_valid_manifest(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = artifact_dir / "ffuf.json"
    source.write_text(
        json.dumps({"results": [{"url": "https://target.test/admin?debug=1", "status": 200}]}),
        encoding="utf-8",
    )
    output = artifact_dir / "inventory.json"
    plan = SimpleNamespace(targets=[SimpleNamespace(target_id="target-1", value="https://target.test")])
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_get_active_plan", lambda: plan)

    result = json.loads(
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/ffuf.json",
            str(output),
            source_format="ffuf-json",
        )
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    assert result["validation_status"] == "valid"
    assert result["source_format"] == "ffuf"
    assert result["artifact_ref"] == "artifact:artifacts/inventory.json"
    assert {item["kind"] for item in written["items"]} == {"service", "endpoint", "parameter"}
    assert memory._load_inventory_manifest(result["artifact_ref"])[0]["schema_version"] == 1


@pytest.mark.parametrize("source_format", ["auto", "url_list", "url-list"])
def test_converter_supports_auto_explicit_and_hyphenated_url_list_formats(tmp_path, monkeypatch, source_format):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = artifact_dir / "urls.txt"
    source.write_text(
        "https://target.test/one\n\nhttps://target.test/search?q=value\n",
        encoding="utf-8",
    )
    output = artifact_dir / f"inventory-{source_format}.json"
    plan = SimpleNamespace(targets=[SimpleNamespace(target_id="target-1", value="https://target.test")])
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_get_active_plan", lambda: plan)
    monkeypatch.setattr(
        memory,
        "resolve_bound_executable_target",
        lambda target: "https://target.test" if target == "target-1" else target,
    )

    result = json.loads(
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/urls.txt",
            str(output),
            source_format=source_format,
        )
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    assert result["source_format"] == "url_list"
    assert {item["kind"] for item in written["items"]} == {"endpoint", "parameter", "service"}


def test_converter_resolves_relative_gobuster_paths_against_registered_target(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = artifact_dir / "gobuster.txt"
    source.write_text("/admin (Status: 301) [Size: 0]", encoding="utf-8")
    output = artifact_dir / "inventory.json"
    plan = SimpleNamespace(targets=[SimpleNamespace(target_id="target-1", value="https://target.test")])
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_get_active_plan", lambda: plan)
    monkeypatch.setattr(
        memory,
        "resolve_bound_executable_target",
        lambda target: "https://target.test" if target == "target-1" else target,
    )

    manifest_tool.recon_output_to_inventory_manifest(
        "artifact:artifacts/gobuster.txt",
        "artifacts/inventory.json",
        source_format="gobuster",
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    endpoint = next(item for item in manifest["items"] if item["kind"] == "endpoint")
    assert endpoint["value"] == "https://target.test/admin"


@pytest.mark.parametrize("module_name", ["web", "web_recon"])
def test_web_modules_do_not_register_the_globally_available_inventory_converter(module_name):
    tools, missing = ModulePromptLoader().discover_module_tools(module_name)

    assert not any(path.endswith("/recon_inventory_manifest.py") for path in tools)
    assert missing is not None


def test_inventory_converter_is_exported_as_a_general_tool():
    assert recon_output_to_inventory_manifest is manifest_tool.recon_output_to_inventory_manifest


def test_converter_rejects_unknown_format_empty_output_and_out_of_scope_records(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = artifact_dir / "recon.txt"
    source.write_text("nothing useful", encoding="utf-8")
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))

    with pytest.raises(ValueError, match="source_format"):
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/recon.txt",
            str(artifact_dir / "inventory.json"),
            source_format="unknown",
        )
    with pytest.raises(ValueError, match="No inventory candidates"):
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/recon.txt",
            str(artifact_dir / "inventory.json"),
            source_format="gobuster",
        )

    source.write_text("https://other.test/admin", encoding="utf-8")
    monkeypatch.setattr(
        manifest_tool,
        "resolve_inventory_target",
        lambda target, target_id="target-1": ("https://target.test", target_id),
    )
    with pytest.raises(ValueError, match="no in-scope"):
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/recon.txt",
            str(artifact_dir / "inventory.json"),
            source_format="gobuster",
            target="target-1",
        )


def test_manifest_writer_rejects_missing_and_outside_output_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))

    with pytest.raises(ValueError, match="output path is required"):
        manifest_tool.write_inventory_manifest("", {"items": []})
    with pytest.raises(ValueError, match="inside the operation output root"):
        manifest_tool.write_inventory_manifest(str(tmp_path.parent / "outside.json"), {"items": []})


def test_converter_source_artifact_resolves_relative_paths_and_prefers_artifacts(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "urls.txt").write_text("https://target.test/artifact", encoding="utf-8")
    (tmp_path / "urls.txt").write_text("https://target.test/root", encoding="utf-8")
    plan = SimpleNamespace(targets=[SimpleNamespace(target_id="target-1", value="https://target.test")])
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_get_active_plan", lambda: plan)

    result = json.loads(
        manifest_tool.recon_output_to_inventory_manifest("urls.txt", "artifacts/inventory.json", source_format="url_list")
    )

    assert result["source_artifact"] == "artifact:artifacts/urls.txt"


def test_converter_source_artifact_falls_back_to_operation_root_and_accepts_safe_absolute_path(tmp_path, monkeypatch):
    source = tmp_path / "recon" / "urls.txt"
    source.parent.mkdir()
    source.write_text("https://target.test/root", encoding="utf-8")
    plan = SimpleNamespace(targets=[SimpleNamespace(target_id="target-1", value="https://target.test")])
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_get_active_plan", lambda: plan)

    relative = json.loads(
        manifest_tool.recon_output_to_inventory_manifest("recon/urls.txt", "artifacts/relative.json", source_format="url_list")
    )
    absolute = json.loads(
        manifest_tool.recon_output_to_inventory_manifest(
            str(source), "artifacts/absolute.json", source_format="url_list"
        )
    )

    assert relative["source_artifact"] == "artifact:recon/urls.txt"
    assert absolute["source_artifact"] == "artifact:recon/urls.txt"


def test_converter_source_artifact_rejects_operation_root_escapes(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside-urls.txt"
    outside.write_text("https://target.test/outside", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "escaped-link.txt").symlink_to(outside)
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))

    with pytest.raises(ValueError, match="outside"):
        manifest_tool.recon_output_to_inventory_manifest(str(outside), "artifacts/inventory.json", source_format="url_list")
    with pytest.raises(ValueError, match="outside"):
        manifest_tool.recon_output_to_inventory_manifest("../outside-urls.txt", "artifacts/inventory.json", source_format="url_list")
    with pytest.raises(ValueError, match="outside"):
        manifest_tool.recon_output_to_inventory_manifest(
            "artifact:artifacts/escaped-link.txt", "artifacts/inventory.json", source_format="url_list"
        )
