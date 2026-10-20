"""Durable hypothesis inputs, inspection reuse, and current publication review."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.agents import hypothesis_context as context
from modules.handlers.tool_recovery import ToolOutcome, ToolOutcomeJournal
from modules.tools import memory
from tests.test_hypotheses import hypothesis, no_test, publish
from tests.test_hypotheses import producer as producer


@pytest.mark.parametrize("value,kind", [
    ("https://target.test/resource", "endpoint"), ("tcp://target.test:9000", "service"),
    ("/workspace/source", "workflow"),
])
def test_frozen_records_reused_across_cycles_without_parsing_again(producer, tmp_path, monkeypatch, value, kind):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"schema_version": 1, "unassessed_gaps": [], "items": [{
        "id": "subject", "target_id": "target-1", "kind": kind, "value": value, "attributes": {},
    }]}))
    _manifest, digest = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    producer = replace(producer, acceptance=replace(producer.acceptance, manifest_hash="", basis=replace(
        producer.acceptance.basis, snapshot_hash=digest,
    )))
    loader = Mock(wraps=context._load_inventory_manifest)
    monkeypatch.setattr(context, "_load_inventory_manifest", loader)
    first = context.frozen_input_context(producer, str(tmp_path))
    assert value in first and "not completion evidence" in first
    assert context.frozen_input_context(producer, str(tmp_path)) == first
    assert loader.call_count == 1
    artifact_path = next(path for path in (tmp_path / "artifacts" / "hypothesis_context").glob("*.json")
                         if "-" not in path.stem)
    assert json.loads(artifact_path.read_text())["items"][0]["attributes"] == {}
    path.write_text(path.read_text().replace(value, value + "-changed"))
    assert "snapshot changed" in context.frozen_input_context(producer, str(tmp_path))
    assert loader.call_count == 2


def test_oversized_inventory_attributes_remain_complete_on_disk(producer, tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_load_inventory_manifest", lambda *_args, **_kwargs: ({"items": [{
        "id": "subject", "kind": "service", "value": "service", "attributes": {"large": "x" * 20_000},
    }]}, producer.acceptance.basis.snapshot_hash))
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "x" * 100 not in rendered
    path = next(path for path in (tmp_path / "artifacts" / "hypothesis_context").glob("*.json")
                         if "-" not in path.stem)
    assert len(json.loads(path.read_text())["items"][0]["attributes"]["large"]) == 20_000


def test_invalid_inventory_context_is_not_invented(producer, tmp_path):
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "unavailable" in rendered
    assert "do not invent" in rendered


def test_hypothesis_context_renders_authoritative_provenance_for_each_operation(producer, tmp_path, monkeypatch):
    interaction = {
        "operations": ["GET", "POST"], "operation_provenance": "observed",
        "operation_provenance_by_operation": {"GET": "observed", "POST": "inferred"},
    }
    monkeypatch.setattr(context, "_load_inventory_manifest", lambda *_args, **_kwargs: ({"items": [{
        "id": "subject", "kind": "endpoint", "value": "https://target.test/resource",
        "attributes": {"interaction": interaction},
    }]}, producer.acceptance.basis.snapshot_hash))
    rendered = context.frozen_input_context(producer, str(tmp_path))
    operations = rendered.split("recorded_operations", 1)[1].split("recorded_inputs", 1)[0]
    assert "POST" in operations and "inferred" in operations and "observed" in operations
    assert "recorded_operations" in rendered and "recorded_inputs" in rendered


@pytest.mark.parametrize("parent_kind,parent_target,child_target,link,child_kind,included", [
    ("endpoint", "one", "one", "subject", "parameter", True),
    ("endpoint", "one", "two", "subject", "parameter", False),
    ("endpoint", None, None, "subject", "parameter", False),
    ("endpoint", "one", "one", "other", "parameter", False),
    ("endpoint", "one", "one", None, "parameter", False),
    ("service", "one", "one", "subject", "parameter", False),
    ("workflow", "one", "one", "subject", "parameter", False),
    ("endpoint", "one", "one", "subject", "endpoint", False),
])
def test_supporting_parameters_require_explicit_same_target_endpoint(
    producer, parent_kind, parent_target, child_target, link, child_kind, included,
):
    parent = {"id": "subject", "kind": parent_kind, "target_id": parent_target}
    child = {"id": "input", "kind": child_kind, "target_id": child_target,
             "attributes": {"endpoint_id": link}}
    assigned, supporting = context._subject_records(producer, [parent, child, child])
    assert assigned == [parent]
    assert supporting == ([child] if included else [])


def test_linked_inputs_render_with_provenance_without_expanding_assignment(producer, tmp_path, monkeypatch):
    items = [{"id": "subject", "kind": "endpoint", "target_id": "one", "value": "https://target.test/object",
              "attributes": {"interaction": {"inputs": [], "operations": ["GET"]}}}]
    for name in ("path", "format"):
        items.append({"id": name, "kind": "parameter", "target_id": "one", "value": name,
                      "attributes": {"endpoint_id": "subject", "interaction": {
                          "inputs": [{"name": name, "location": "query"}], "operations": ["PUT"],
                          "operation_provenance": "inferred", "evidence_refs": ["artifact:observation.txt"],
                      }}})
    monkeypatch.setattr(context, "_load_inventory_manifest", lambda *_a, **_kw: (
        {"items": items}, producer.acceptance.basis.snapshot_hash,
    ))
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "recorded_inputs[3]" in rendered
    assert "path" in rendered and "format" in rendered and "query" in rendered
    assert "PUT" in rendered and "GET" in rendered and "inferred" in rendered
    assert "artifact:observation.txt" in rendered
    assert "not additional publication subjects" in rendered
    saved = next(p for p in (tmp_path / "artifacts/hypothesis_context").glob("*.json") if "-" not in p.stem)
    payload = json.loads(saved.read_text())
    assert [i["id"] for i in payload["items"]] == ["subject"]
    assert len(payload["supporting_items"]) == 2
    assert tuple(producer.acceptance.basis.item_ids) == ("subject",)
    assert "artifact:observation.txt" in context.prerequisite_input_sources(producer, {"artifact:inventory.json"})
    payload["identity"]["context_version"] = 1
    payload["supporting_items"] = []
    saved.write_text(json.dumps(payload))
    assert "path" in context.frozen_input_context(producer, str(tmp_path))
    assert len(json.loads(saved.read_text())["supporting_items"]) == 2


def test_oversized_input_summary_retains_complete_supporting_records(producer, tmp_path, monkeypatch):
    items = [{"id": "subject", "kind": "endpoint", "target_id": "one", "value": "subject", "attributes": {}},
             {"id": "input", "kind": "parameter", "target_id": "one", "value": "input", "attributes": {
                 "endpoint_id": "subject", "interaction": {"inputs": [{"name": "x" * 13_000}]},
             }}]
    monkeypatch.setattr(context, "_load_inventory_manifest", lambda *_a, **_kw: (
        {"items": items}, producer.acceptance.basis.snapshot_hash,
    ))
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "Input summary exceeds prompt limit" in rendered
    assert "x" * 100 not in rendered
    saved = next(p for p in (tmp_path / "artifacts/hypothesis_context").glob("*.json") if "-" not in p.stem)
    assert json.loads(saved.read_text())["supporting_items"] == [items[1]]


def test_inspection_ledger_is_bounded_durable_and_distinguishes_pages(producer, tmp_path):
    assert context.inspection_context(producer, str(tmp_path)) == ""
    outcomes = [ToolOutcome(
        sequence=index, tool_use_id=str(index), tool_name="read_artifact", success=True, correctable=False,
        input_summary=json.dumps({"path": "artifact:inventory.json", "start_line": index}),
        output_summary="not retained", artifact_refs=("artifact:inventory.json",),
    ) for index in range(30)]
    outcomes += [replace(outcomes[0], success=False), replace(outcomes[0], tool_name="unrelated")]
    context.retain_inspections(producer, outcomes, str(tmp_path))
    path = next((tmp_path / "artifacts" / "hypothesis_context").glob("*-inspections.json"))
    entries = json.loads(path.read_text())
    assert len(entries) == 24
    assert len({entry["input"] for entry in entries}) == 24
    context.retain_inspections(producer, [outcomes[12]], str(tmp_path))
    assert len(json.loads(path.read_text())) == 24
    rendered = context.inspection_context(replace(producer, recovery_context={}), str(tmp_path))
    assert "Already inspected" in rendered and "artifact:inventory.json" in rendered
    assert "not retained" not in path.read_text()


def test_review_contains_latest_records_and_no_hypothesis_dispositions(producer, tmp_path):
    first, writer = publish(producer, tmp_path)
    second = json.loads(writer(subjects=[{
        "item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis("Second path")],
    }]))
    rendered = context.publication_review(producer, "OP_TEST")
    assert second["artifact_ref"] in rendered and first["artifact_ref"] not in rendered
    assert "hypotheses[2]" in rendered and "Second path" in rendered
    assert "negative_expectations" in rendered and "Older snapshots" in rendered
    writer(subjects=[no_test(reason="This item has no supported security implication")], mode="replace")
    rendered = context.publication_review(producer, "OP_TEST")
    assert "no_testable_hypotheses" in rendered and "hypotheses[0]" in rendered
    assert "no supported security implication" in rendered


def test_review_rejects_corrupt_latest_without_using_history(producer, tmp_path):
    binding, _writer = publish(producer, tmp_path)
    from modules.tools.artifact import resolve_operation_artifact_path

    Path(resolve_operation_artifact_path(binding["artifact_ref"])).write_text("corrupt")
    with pytest.raises(ValueError, match="digest changed"):
        context.publication_review(producer, "OP_TEST")


@pytest.mark.parametrize("subject", ["https://target.test/hidden-upload", "tcp://target.test:9000", "/source/upload.py"])
def test_prerequisite_reader_follows_only_assigned_provenance(producer, tmp_path, monkeypatch, subject):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"implementation": "artifact:implementation.txt"}))
    (tmp_path / "implementation.txt").write_text("file and fileName; preview renderer")
    monkeypatch.setattr(context, "resolve_operation_artifact_path", lambda ref: str(tmp_path / ref[9:]))
    monkeypatch.setattr(context, "_load_inventory_manifest", lambda *_args, **_kwargs: ({"items": [
        {"id": "subject", "kind": "workflow", "target_id": "one", "value": subject,
         "attributes": {"source_artifact_refs": [
            "artifact:summary.json", "artifact:missing.json", "artifact:invalid.json",
            "artifact:large.json", "artifact:list.json", 7,
        ]}},
        {"id": "unassigned", "kind": "workflow", "target_id": "one", "value": "unassigned",
         "attributes": {"implementation_refs": ["artifact:unrelated.txt"]}},
    ]}, producer.acceptance.basis.snapshot_hash))
    (tmp_path / "invalid.json").write_text("not JSON")
    (tmp_path / "large.json").write_text("x" * 1_000_001)
    (tmp_path / "list.json").write_text("[]")
    sources = context.prerequisite_input_sources(producer, {"artifact:inventory.json"})
    assert "artifact:implementation.txt" in sources
    assert "artifact:unrelated.txt" not in sources
    from modules.tools import hypotheses as reader
    result = json.loads(reader.build_hypothesis_input_reader(sources)(source_ref="artifact:implementation.txt"))
    assert "fileName" in result["text"]
    assert "artifact:implementation.txt" in sources


@pytest.mark.parametrize("failure", ["changed", "unavailable"])
def test_prerequisite_sources_do_not_expand_invalid_frozen_inputs(producer, monkeypatch, failure):
    def load(*_args, **_kwargs):
        if failure == "unavailable":
            raise ValueError("missing")
        return {"items": []}, "changed"
    monkeypatch.setattr(context, "_load_inventory_manifest", load)
    assert context.prerequisite_input_sources(producer, {"artifact:inventory.json"}) == {"artifact:inventory.json"}


@pytest.mark.parametrize("provenance", ["observed", "inferred", None])
@pytest.mark.parametrize("value,kind", [
    ("https://target.test/hidden-upload", "endpoint"),
    ("tcp://target.test:9000", "service"), ("/workspace/upload.py", "workflow"),
])
def test_normal_context_resolves_old_summary_links_and_preserves_provenance(
    producer, tmp_path, provenance, value, kind,
):
    interaction = {"operations": ["GET"], "inputs": []}
    if provenance is not None:
        interaction["operation_provenance"] = provenance
    manifest = {"schema_version": 1, "unassessed_gaps": [], "items": [{
        "id": "subject", "target_id": "target-1", "kind": kind, "value": value,
        "attributes": {"interaction": interaction, "source_artifact_refs": ["artifact:summary.json"]},
    }]}
    (tmp_path / "inventory.json").write_text(json.dumps(manifest))
    _, digest = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    producer = replace(producer, acceptance=replace(producer.acceptance, manifest_hash="", basis=replace(
        producer.acceptance.basis, snapshot_hash=digest,
    )))
    (tmp_path / "summary.json").write_text(json.dumps({"analysis_artifact": "artifact:implementation.js"}))
    implementation = tmp_path / "implementation.js"
    implementation.write_text('const fileName = input; renderHtml(fileName); upload(file, fileName);')
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "artifact:implementation.js" in rendered and "artifact:summary.json" in rendered
    assert "availability is not inspection or semantic support" in rendered
    assert (provenance or "unknown") in rendered
    assert "renderHtml" not in rendered
    implementation.unlink()
    assert "unavailable" in context.frozen_input_context(producer, str(tmp_path))
    assert producer.acceptance.basis.snapshot_hash == digest


@pytest.mark.parametrize("tool,field,lookup", [
    ("read_artifact", "path", {"start_line": 10, "max_lines": 20}),
    ("search_artifact", "path", {"search_pattern": "upload", "context_lines": 3}),
    ("get_inventory_item", "manifest_ref", {"item_id": "subject"}),
    ("read_hypothesis_input", "source_ref", {"offset": 4000, "max_chars": 2000}),
])
def test_inspected_inputs_are_independent_of_truncated_inputs_and_returned_references(
    producer, tmp_path, tool, field, lookup,
):
    journal = ToolOutcomeJournal()
    outcome = journal.append(tool_use_id="inspect", tool_name=tool, success=True, correctable=False,
                             tool_input={"padding": "x" * 700, field: str(tmp_path / "observation.txt"), **lookup},
                             output={"artifact_ref": "artifact:returned-only.txt"})
    assert "observation.txt" not in outcome.input_summary
    context.retain_inspections(producer, [outcome, replace(outcome, success=False)], str(tmp_path))
    path = context._inspection_path(producer, str(tmp_path))
    entries = json.loads(path.read_text())
    assert len(entries) == 1
    assert entries[0]["inspected_reference"] == "artifact:observation.txt"
    assert entries[0]["lookup"] == lookup
    assert entries[0]["returned_references"] == ["artifact:returned-only.txt"]
    rendered = context.inspection_context(producer, str(tmp_path))
    assert "Returned references alone are not inspected" in rendered
    assert "may cover only part" in rendered


@pytest.mark.parametrize("inputs", [None, {}, {"path": 4}, {"path": "https://remote.test/code"}])
def test_missing_or_invalid_structured_inputs_do_not_prove_inspection(producer, tmp_path, inputs):
    outcome = ToolOutcome(sequence=1, tool_use_id="read", tool_name="read_artifact", success=True,
                          correctable=False, input_summary="truncated", output_summary="returned",
                          artifact_refs=("artifact:returned-only.txt",), structured_input=inputs)
    context.retain_inspections(producer, [outcome], str(tmp_path))
    entry = json.loads(context._inspection_path(producer, str(tmp_path)).read_text())[0]
    assert not entry["inspected_reference"]
    assert entry["returned_references"] == ["artifact:returned-only.txt"]
