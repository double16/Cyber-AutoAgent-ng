"""Input candidates expose provenance without inventing server behavior or security conclusions."""

import copy
import json

import pytest

from modules.tools.inventory_inputs import input_candidates, inspection_covers_input
from tests.test_inventory_investigation import record


def candidates(subject, entries=(), others=()):
    return input_candidates({"items": [subject, *others]}, subject, list(entries))


@pytest.mark.parametrize("provider", ["azure", "digital_ocean"])
def test_empty_input_metadata_preserves_url_query_names(provider):
    subject = record(value=f"https://target.test/api/file/{provider}?path={provider}&blank=&path=again")
    before = copy.deepcopy(subject)
    rows = candidates(subject)
    assert {(row["name"], row["location"]) for row in rows} == {("path", "query"), ("blank", "query")}
    assert subject == before
    assert candidates(subject) == rows
    assert all(row["origins"][0]["kind"] == "inventory_url" for row in rows)


def test_linked_and_recorded_parameters_deduplicate_origins():
    endpoint = record(interaction={"inputs": [{"name": "path", "location": "query"}, {}]})
    parameter = record("parameter", "parameter", "path", name="path", location="query", endpoint_id="endpoint",
                       interaction={"inputs": [{"name": "path", "location": "query"}]})
    other = record("other", "parameter", "ignored", location="query", endpoint_id="endpoint")
    other["target_id"] = "other-target"
    rows = candidates(endpoint, others=[parameter, other])
    assert len(rows) == 1
    assert {origin["kind"] for origin in rows[0]["origins"]} == {
        "recorded_input", "linked_parameter", "inventory_url",
    }
    manifest = {"items": [endpoint, parameter]}
    child_rows = input_candidates(manifest, parameter, [])
    assert len(child_rows) == 1 and child_rows[0]["candidate_id"] != rows[0]["candidate_id"]


@pytest.mark.parametrize("url", ["/api/file/aws?next=x", "https://TARGET.test:443/api/file/aws?next=x#part"])
def test_exact_client_declarations_and_links_retain_locations(url):
    endpoint = record(value="https://target.test/api/file/aws")
    declaration = {"type": "client_operation", "declaration": {
        "url": url, "inputs": [{"name": "body", "location": "body"}, {"name": 3}],
        "source_artifact": "artifact:source.js", "source_location": {"start_line": 2, "end_line": 3},
    }}
    link = {"type": "client_link", "url": url, "reference": "artifact:source.js",
            "source_location": {"start_line": 4, "end_line": 4}}
    rows = candidates(endpoint, [declaration, declaration, link, {"type": "diagnostic"}])
    assert {row["name"] for row in rows} == {"next", "body"}
    assert len(next(row for row in rows if row["name"] == "next")["origins"]) == 2


@pytest.mark.parametrize("url", ["/api/file/raw?path=x", "https://elsewhere.test/api/file/aws?path=x",
                                  "http://target.test/api/file/aws?path=x",
                                  "https://target.test:444/api/file/aws?path=x",
                                  "https://target.test:invalid/api/file/aws?path=x", None])
def test_mismatched_or_invalid_declarations_never_supply_inputs(url):
    endpoint = record(value="https://target.test/api/file/aws")
    assert candidates(endpoint, [{"type": "client_operation", "declaration": {"url": url}}]) == []
    assert candidates(endpoint, [{"type": "client_operation", "declaration": {}}]) == []


@pytest.mark.parametrize("kind,value", [("service", "tcp://target.test:9000"),
                                       ("workflow", "/workspace/module.py"),
                                       ("endpoint", "https://target.test:invalid/path")])
def test_generic_recorded_inputs_remain_available(kind, value):
    subject = record(kind=kind, value=value, interaction={"inputs": [{"name": "resource", "location": "argument"}]})
    assert candidates(subject)[0]["name"] == "resource"


def test_parameter_without_matching_parent_does_not_borrow_endpoint_url():
    parameter = record("parameter", "parameter", "name", endpoint_id="missing", location="query")
    assert candidates(parameter)[0]["name"] == "name"
    assert len(candidates(parameter, others=[record()])) == 1


def source_candidate():
    endpoint = record(value="https://target.test/api/file/aws")
    return candidates(endpoint, [{"type": "client_link", "url": "/api/file/aws?path=x",
                                 "reference": "artifact:source.js",
                                 "source_location": {"start_line": 4, "end_line": 5}}])[0]


@pytest.mark.parametrize("tool", ["read_artifact", "read_hypothesis_input", "get_inventory_item"])
def test_source_candidates_require_corresponding_returned_ranges(tool):
    candidate = source_candidate()
    if tool == "get_inventory_item":
        payload = {"view": "investigation", "context_entries": [{
            "type": "source_excerpt", "reference": "artifact:source.js", "line": 3, "end_line": 6,
            "column": 1, "sha256": "digest", "text": "returned source",
        }]}
    else:
        payload = {"artifact_ref": "artifact:source.js", "start_line": 3, "end_line": 6,
                   "content": "returned source"}
    record = {"tool": tool, "payload": json.dumps(payload)}
    assert inspection_covers_input(candidate, record)
    altered = json.loads(json.dumps(payload).replace("artifact:source.js", "artifact:other.js"))
    assert not inspection_covers_input(candidate, {**record, "payload": json.dumps(altered)})
    if tool != "get_inventory_item":
        assert not inspection_covers_input(candidate, {**record, "tool": "search_artifact"})


@pytest.mark.parametrize("payload", ["not json", "[]", "{}", '{"matches": ["path"]}',
                                    '{"content":"source", "artifact_ref":"artifact:source.js", "start_line":6}'])
def test_metadata_and_unrelated_content_do_not_prove_source_inspection(payload):
    assert not inspection_covers_input(source_candidate(), {"tool": "read_artifact", "payload": payload})


def test_recorded_candidate_does_not_require_additional_source_read():
    assert inspection_covers_input(candidates(record())[0], {})


def test_native_artifact_reader_representation_is_supported():
    payload = {"artifact_ref": "artifact:source.js", "start_line": 3, "end_line": 6,
               "content": "returned source"}
    assert inspection_covers_input(source_candidate(), {"tool": "read_artifact", "payload": str(payload)})


def test_large_inventory_does_not_expand_assigned_input_scope():
    subject = record(value="https://target.test/assigned?path=example")
    others = [record(f"endpoint-{index}", value=f"https://target.test/other/{index}?foreign=value")
              for index in range(384)]
    result = candidates(subject, others=others)
    assert len(result) == 1 and result[0]["name"] == "path"
