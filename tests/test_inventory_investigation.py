"""Structured inventory context stays local, pageable, and distinct from semantic evidence."""

import hashlib
import json

import pytest

from modules.tools.inventory_investigation import investigation_entries, investigation_page, returned_source_ranges


def record(uid="endpoint", kind="endpoint", value="https://target.test/api/file/aws?path=aws", **attributes):
    return {"id": uid, "kind": kind, "target_id": "target-1", "value": value, "attributes": attributes}


@pytest.fixture
def client_case():
    content = b'const request = fetch("/api/file/raw?path=" + value);\n'
    operation = {"url": "/api/file/raw", "method": "GET", "inputs": [{"name": "path", "location": "query"}],
                 "source_artifact": "artifact:source.js", "source_location": {"start_line": 1, "end_line": 1}}
    endpoint = record()
    parameter = record("parameter", "parameter", "path", endpoint_id="endpoint",
                       source_artifact_refs=["artifact:extraction.json"],
                       interaction={"operations": ["GET"], "inputs": [], "operation_provenance": "inferred",
                                    "extensions": {"client_source_operations": [operation]}})
    payload = {"schema_version": "client_bundle_inventory_v2", "target_id": "target-1",
               "target": "https://target.test", "analysis_artifact": "artifact:source.js",
               "analysis_sha256": hashlib.sha256(content).hexdigest(), "operations": []}
    sources = {"artifact:source.js": content, "artifact:extraction.json": json.dumps(payload).encode()}
    return {"items": [endpoint, parameter]}, parameter, operation, payload, sources


def test_mismatch_and_excerpts_keep_original_snapshot_and_provenance(client_case):
    manifest, parameter, operation, _, sources = client_case
    before = json.dumps(manifest, sort_keys=True)
    entries = investigation_entries(manifest, parameter, read_source=sources.__getitem__)
    assert any(row.get("code") == "operation_endpoint_mismatch" for row in entries)
    assert any(row.get("code") == "inputs_unrecorded" for row in entries)
    assert any(row.get("code") == "operation_not_observed" for row in entries)
    declarations = [row for row in entries if row["type"] == "client_operation"]
    assert declarations[0]["declaration"] == operation
    assert declarations[0]["server_behavior"] == "unestablished"
    excerpts = [row for row in entries if row["type"] == "source_excerpt"]
    assert excerpts[0]["digest_verification"] == "verified"
    assert excerpts[0]["text"] == sources["artifact:source.js"].decode().strip()
    assert json.dumps(manifest, sort_keys=True) == before


@pytest.mark.parametrize("value", ["https://target.test/api/file/raw?x=1#fragment",
                                  "https://TARGET.test:443/api/file/raw"])
def test_route_comparison_normalizes_origin_query_and_fragment(client_case, value):
    manifest, parameter, _, _, sources = client_case
    manifest["items"][0]["value"] = value
    assert not any(row.get("code") == "operation_endpoint_mismatch"
                   for row in investigation_entries(manifest, parameter, read_source=sources.__getitem__))


@pytest.mark.parametrize("value,kind", [("tcp://target.test:1234", "service"), ("/workspace/source.py", "workflow")])
def test_generic_context_has_no_http_assumptions(value, kind):
    subject = record(kind=kind, value=value, interaction={"operations": ["inspect"],
                     "operation_provenance": "observed"}, implementation_refs=["artifact:source.txt"])
    entries = investigation_entries({"items": [subject]}, subject)
    assert entries[0]["operations"] == {"inspect": "observed"}
    assert any(row.get("relationship") == "broad_implementation" for row in entries)
    assert not any(row["type"] == "client_operation" for row in entries)


@pytest.mark.parametrize("fault,code", [("missing", "missing_parent"), ("target", "cross_target_parent")])
def test_parent_failures_do_not_attach_unrelated_records(client_case, fault, code):
    manifest, parameter, _, _, _ = client_case
    if fault == "missing":
        manifest["items"] = [parameter]
    else:
        manifest["items"][0]["target_id"] = "another-target"
    entries = investigation_entries(manifest, parameter)
    assert any(row.get("code") == code for row in entries)
    assert [row["id"] for row in entries if row["type"] == "record"] == ["parameter"]


def test_endpoint_only_expands_explicit_same_target_parameters(client_case):
    manifest, _, _, _, _ = client_case
    unrelated = record("unrelated", "parameter", "path", endpoint_id="elsewhere")
    cross_target = record("cross", "parameter", "path", endpoint_id="endpoint")
    cross_target["target_id"] = "other"
    manifest["items"].extend([unrelated, cross_target])
    manifest["unassessed_gaps"] = [{"item_id": "endpoint", "reason": "unresolved"},
                                  {"item_id": "unrelated"}, "legacy gap"]
    entries = investigation_entries(manifest, manifest["items"][0])
    assert {row["id"] for row in entries if row["type"] == "record"} == {"endpoint", "parameter"}
    assert [row["gap"] for row in entries if row["type"] == "extraction_gap"] == [manifest["unassessed_gaps"][0]]


@pytest.mark.parametrize("fault,code", [("changed", "source_changed"), ("location", "source_unavailable"),
                                      ("missing", "source_unavailable"), ("target", "cross_target_extraction")])
def test_source_failures_are_explicit(client_case, fault, code):
    manifest, parameter, operation, payload, sources = client_case
    if fault == "changed":
        sources["artifact:source.js"] = b"changed"
    elif fault == "location":
        operation["source_location"]["start_line"] = 0
    elif fault == "target":
        payload["target_id"] = "other"
        sources["artifact:extraction.json"] = json.dumps(payload).encode()

    def read(reference):
        if fault == "missing" and reference == "artifact:source.js":
            raise OSError("missing")
        return sources[reference]

    entries = investigation_entries(manifest, parameter, read_source=read)
    assert any(row.get("code") == code for row in entries)
    if fault in {"changed", "location", "missing"}:
        assert not any(row["type"] == "source_excerpt" for row in entries)


def test_extraction_recovers_exact_operations_and_local_gaps(client_case):
    manifest, parameter, operation, payload, sources = client_case
    parameter["attributes"]["interaction"]["extensions"] = {}
    manifest["items"][0]["value"] = "https://target.test/api/file/raw"
    payload.update(operations=[operation, {"url": "/other"}, {}], request_analysis_status="request_limit",
                   unresolved_requests=[{"reason": "wrapper", "candidate": {"url": "/api/file/raw"}},
                                        {"reason": "unknown"}])
    sources["artifact:extraction.json"] = json.dumps(payload).encode()
    entries = investigation_entries(manifest, parameter, read_source=sources.__getitem__)
    assert len([row for row in entries if row["type"] == "client_operation"]) == 1
    assert len([row for row in entries if row["type"] == "extraction_gap"]) == 3


def test_non_json_implementation_and_unavailable_extraction(client_case):
    manifest, parameter, _, _, sources = client_case
    parameter["attributes"]["source_artifact_refs"] = ["artifact:source.js", "artifact:missing.json"]

    def read(reference):
        if reference not in sources:
            raise OSError("missing")
        return sources[reference]

    entries = investigation_entries(manifest, parameter, read_source=read)
    assert any(row.get("digest_verification") == "unrecorded" for row in entries)
    assert [row["reference"] for row in entries if row.get("code") == "source_unavailable"] == ["artifact:missing.json"]


def test_invalid_declaration_url_is_diagnostic(client_case):
    manifest, parameter, operation, _, _ = client_case
    operation["url"] = "https://target.test:invalid/path"
    entries = investigation_entries(manifest, parameter)
    assert any(row.get("code") == "invalid_operation_url" for row in entries)


@pytest.mark.parametrize("line_length,expected_characters,next_line", [(1001, 8000, 8), (1, 200, 201)])
def test_long_source_ranges_are_bounded_and_remaining_range_is_explicit(
    client_case, line_length, expected_characters, next_line,
):
    manifest, parameter, operation, _, sources = client_case
    parameter["attributes"]["source_artifact_refs"] = []
    operation["source_location"] = {"start_line": 1, "end_line": 201}
    sources["artifact:source.js"] = (("x" * line_length + "\n") * 201).encode()
    entries = investigation_entries(manifest, parameter, read_source=sources.__getitem__)
    assert sum(len(row["text"].replace("\n", "")) for row in entries
               if row["type"] == "source_excerpt") == expected_characters
    assert next(row for row in entries if row.get("code") == "excerpt_limit")["next_line"] == next_line


def test_pagination_respects_character_limit_and_never_loses_entries():
    entries = [{"type": "test", "text": "x" * 100, "index": index} for index in range(12)]
    offset = 0
    seen = []
    while offset is not None:
        encoded = investigation_page(entries, offset=offset, limit=4, header={}, max_chars=430)
        assert len(encoded) <= 430
        page = json.loads(encoded)
        seen.extend(page["context_entries"])
        offset = page["next_context_offset"]
    assert seen == entries
    assert json.loads(investigation_page(entries, offset=20, limit=4, header={}, max_chars=430))["context_entries"] == []
    with pytest.raises(ValueError, match="entry exceeds"):
        investigation_page(entries, offset=0, limit=1, header={}, max_chars=100)
    with pytest.raises(ValueError, match="metadata"):
        investigation_page([], offset=0, limit=1, header={"large": "x" * 100}, max_chars=100)


@pytest.mark.parametrize("output", [None, [], "not json", {}, {"view": "record"}, {"content": None},
                                   {"view": "investigation", "context_entries": None},
                                   {"view": "investigation", "context_entries": [None,
                                    {"type": "source_excerpt"}, {"type": "source_excerpt", "reference": "x",
                                     "line": 1, "column": 1, "sha256": "x", "text": 4}]}])
def test_listed_references_and_non_investigation_outputs_are_not_inspected_ranges(output):
    assert returned_source_ranges(output) == ()


def test_only_returned_excerpts_produce_inspected_ranges():
    output = {"view": "investigation", "context_entries": [
        {"type": "source_reference", "reference": "artifact:listed.js"},
        {"type": "source_excerpt", "reference": "artifact:read.js", "line": 4, "column": 2,
         "sha256": "digest", "text": "hello"},
    ]}
    wrapped = {"content": [{"text": json.dumps(output)}, "ignored"]}
    assert returned_source_ranges(wrapped) == ({"reference": "artifact:read.js", "line": 4,
                                               "column": 2, "sha256": "digest", "characters": 5},)


def test_subject_declarations_and_excerpts_precede_broad_metadata():
    from modules.tools.inventory_investigation import prioritize_entries

    rows = [{"type": "record", "id": "subject"}]
    rows.extend({"type": "source_reference", "reference": str(index)} for index in range(30))
    rows += [{"type": "client_route_definition", "name": "component"}]
    rows += [{"type": "source_excerpt", "reference": "artifact:source", "line": line,
              "column": 1, "sha256": "digest", "text": f"observed line {line}"} for line in range(1, 31)]
    rows += [{"type": "client_operation", "declaration": {"url": "/resource"}},
             {"type": "source_excerpt", "reference": "artifact:source", "line": 100,
              "column": 1, "sha256": "digest", "text": "request(identity)"},
             {"type": "record", "id": "linked"}]
    ordered = prioritize_entries(rows)
    first = json.loads(investigation_page(ordered, offset=0, limit=5, header={}, max_chars=3000))
    assert [row["type"] for row in first["context_entries"]] == [
        "record", "client_route_definition", "source_excerpt", "client_operation", "source_excerpt",
    ]
    assert "observed line 30" in first["context_entries"][2]["text"]
    assert first["remaining_context"] == {"record": 5, "source_reference": 6}
    assert rows[32]["text"] == "observed line 1"
    ranges = returned_source_ranges({"view": "investigation", "context_entries": first["context_entries"]})
    assert ranges[0]["line"] == 1 and ranges[0]["end_line"] == 30
    isolated = {"type": "source_excerpt", "reference": "artifact:isolated", "line": 1,
                "column": 1, "text": "standalone"}
    assert prioritize_entries([isolated]) == [isolated]
