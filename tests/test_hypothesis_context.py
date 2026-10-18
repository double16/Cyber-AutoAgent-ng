"""Durable hypothesis inputs, inspection reuse, and current publication review."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.agents import hypothesis_context as context
from modules.handlers.tool_recovery import ToolOutcome
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
    artifact_path = next((tmp_path / "artifacts" / "hypothesis_context").glob("*.json"))
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
    path = next((tmp_path / "artifacts" / "hypothesis_context").glob("*.json"))
    assert len(json.loads(path.read_text())["items"][0]["attributes"]["large"]) == 20_000


def test_invalid_inventory_context_is_not_invented(producer, tmp_path):
    rendered = context.frozen_input_context(producer, str(tmp_path))
    assert "unavailable" in rendered
    assert "do not invent" in rendered


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
