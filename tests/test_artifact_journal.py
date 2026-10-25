"""Exact returned evidence remains available after bounded artifact access closes."""

import ast
import json

import pytest

from modules.tools import artifact
from modules.tools.artifact_journal import ReturnedArtifactJournal


def bound_journal(tmp_path, **overrides):
    return ReturnedArtifactJournal(tmp_path / "returned", operation_root=tmp_path,
                                   binding={"operation_id": "OP", "task_uid": "task", "snapshot_hash": "snapshot",
                                            **overrides})


@pytest.mark.parametrize("tool,payload", [
    ("read_artifact", {"content": "HTTP response\nobserved body"}),
    ("search_artifact", {"matches": [{"line": 4, "content": "invoke(input)"}]}),
    ("get_inventory_item", {"context_entries": [{"type": "source_excerpt", "text": "handler(input)"}]}),
    ("read_hypothesis_input", {"text": "ssh banner"}),
])
def test_bound_journal_recovers_exact_results_and_preserves_availability(tmp_path, tool, payload):
    from modules.tools.artifact_journal import inspection_has_content

    journal = bound_journal(tmp_path)
    text = json.dumps(payload, ensure_ascii=False)
    reference = journal.record_inspection(tool, "call", {"path": "artifact:source"}, text, ("artifact:source",))
    journal.record_inspection(tool, "call", {"path": "artifact:source"}, text, ("artifact:source",))
    restored = bound_journal(tmp_path)
    assert len(restored.index.read_text().splitlines()) == 1
    record = restored.load_inspection(reference)
    assert record["payload"] == text and inspection_has_content(record)
    context = restored.replay_context(10000, len)
    assert reference in context and "returned" in context
    assert "\"payload\":" not in restored.replay_context(0, len)
    assert "Inspection replay access" in context
    reader = restored.reader()
    assert json.loads(reader(reference))["text"] == json.dumps(record, ensure_ascii=False)
    assert json.loads(reader(restored._reference(restored.index)))["text"] == json.dumps(restored._entries())


@pytest.mark.parametrize("fault", ["task_uid", "snapshot_hash", "operation_id", "payload", "binding", "path"])
def test_saved_results_cannot_cross_scope_or_change(tmp_path, fault):
    journal = bound_journal(tmp_path)
    reference = journal.record_inspection("read_artifact", "call", {}, "{\"content\":\"observed\"}", ())
    if fault in {"task_uid", "snapshot_hash", "operation_id"}:
        reader = bound_journal(tmp_path, **{fault: "different"})
    else:
        reader = journal
        record = journal.load_inspection(reference)
        if fault == "payload":
            record["payload"] = "changed"
        elif fault == "binding":
            record["binding"] = {}
        else:
            entry = journal._entries()[0]
            entry["reference"] = "artifact:../outside.json"
            journal.index.write_text(json.dumps(entry) + "\n")
            reference = entry["reference"]
        if fault != "path":
            (tmp_path / reference.removeprefix("artifact:")).write_text(json.dumps(record))
    with pytest.raises(ValueError):
        reader.load_inspection(reference)


def test_saved_reader_is_bounded_pageable_and_never_reopens_sources(tmp_path):
    journal = bound_journal(tmp_path)
    assert journal.replay_context(1000, len) == ""
    reference = journal.record_inspection("read_artifact", "call", {}, json.dumps({"content": "x" * 20000}), ())
    reader = journal.reader(1000)
    for arguments in ({"offset": -1}, {"max_chars": 0}, {"max_chars": 10001}):
        with pytest.raises(ValueError):
            reader(reference, **arguments)
    first = json.loads(reader(reference, max_chars=10000))
    assert first["next_offset"] == len(first["text"]) and len(first["text"]) < 1000
    for _ in range(3):
        reader(reference, offset=first["next_offset"])
    with pytest.raises(ValueError, match="allowance exhausted"):
        reader(reference)
    with pytest.raises(ValueError, match="ceiling"):
        journal.reader(1)(reference)
    (tmp_path / reference.removeprefix("artifact:")).unlink()
    assert "unreadable returned results: 1" in journal.replay_context(10000, len)


@pytest.mark.parametrize("payload", ["{\"context_entries\":[{\"type\":\"diagnostic\"}]}",
                                    "{\"matches\":[]}", "\"plain\"", "invalid"])
def test_missing_content_is_not_upgraded_to_semantic_evidence(payload):
    from modules.tools.artifact_journal import inspection_has_content

    assert not inspection_has_content({"tool": "get_inventory_item", "payload": payload})


def test_inspection_availability_is_bound_and_rendered_even_for_sliced_results(tmp_path):
    journal = bound_journal(tmp_path)
    content = journal.record_inspection("read_artifact", "read", {}, json.dumps({"content": "x" * 12000}), ())
    metadata = journal.record_inspection("get_inventory_item", "lookup", {}, '{"item":{}}', ())
    missing = "artifact:foreign.json"
    assert journal.inspection_availability([content, metadata, missing]) == {
        content: "content_bearing", metadata: "metadata_only", missing: "unavailable",
    }
    assert journal.inspection_availability() == {content: "content_bearing", metadata: "metadata_only"}
    context = journal.replay_context(8000, len)
    assert '"availability": "content_bearing"' in context
    assert '"availability": "metadata_only"' in context
    assert '"next_offset"' in context
    (tmp_path / content.removeprefix("artifact:")).unlink()
    assert journal.inspection_availability([content]) == {content: "unavailable"}


def test_recovery_filters_saved_results_and_advertises_reader_bounds(tmp_path):
    journal = bound_journal(tmp_path)
    wanted = journal.record_inspection("read_artifact", "wanted", {}, '{"content":"relevant fact"}', ())
    other = journal.record_inspection("read_artifact", "other", {}, '{"content":"unrelated fact"}', ())
    text = journal.replay_context(10000, len, references={wanted})
    assert "relevant fact" in text and "unrelated fact" not in text and other not in text
    properties = journal.reader().tool_spec["inputSchema"]["json"]["properties"]
    assert properties["offset"]["minimum"] == 0
    assert properties["max_chars"]["maximum"] == 10000


def test_tool_boundary_capture_is_immediate_thread_safe_and_ignores_failures(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from modules.handlers.tool_recovery import ToolOutcomeJournal
    from modules.tools.artifact_journal import capture_inspections

    journal = bound_journal(tmp_path)
    with capture_inspections(journal):
        outcomes = ToolOutcomeJournal()
    arguments = {"tool_use_id": "call", "tool_name": "read_artifact", "success": True,
                 "correctable": False, "tool_input": {"path": "artifact:source.txt"},
                 "output": {"content": [{"text": json.dumps({"content": "exact observed bytes"})}]}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        outcome = executor.submit(outcomes.append, **arguments).result()
    assert journal.load_inspection(outcome.inspection_result_ref)["payload"] == "{\"content\": \"exact observed bytes\"}"
    assert len(journal._entries()) == 1
    assert outcomes.append(**{**arguments, "success": False}).inspection_result_ref == ""
    assert outcomes.append(**{**arguments, "tool_name": "shell"}).inspection_result_ref == ""
    assert ToolOutcomeJournal().append(**arguments).inspection_result_ref == ""


def test_replay_deduplicates_newest_results_and_fitting_preserves_access_index(tmp_path):
    from modules.agents.prompt_context import fit_task_prompt

    journal = bound_journal(tmp_path)
    for call_id in ("old", "new"):
        journal.record_inspection("read_artifact", call_id, {}, json.dumps({"content": "observed " * 300}), ())
    journal.record_inspection("read_artifact", "latest", {}, '{"content":"small result"}', ())
    rendered = journal.replay_context(10000, len)
    assert rendered.count('"payload":') == 2
    assert '"call_id": "new"' in rendered and '"call_id": "old"' not in rendered
    fitted = fit_task_prompt(rendered + "\n## Required final contract\nPublish or explain a prerequisite gap.",
                             "system", [], "test", 2000, 0, lambda _event: None,
                             optional_headings=("Retained inspection evidence",), estimate_input=len)
    assert "Inspection replay access" in fitted and "index.jsonl" in fitted
    assert "Required final contract" in fitted
    assert "Inspection evidence anchors" in fitted and "observed observed" in fitted


@pytest.mark.parametrize("content", ["HTTP/1.1 201\n<form>📧</form>", "SSH banner\nport 22", "def main():\n    pass"])
def test_journal_preserves_exact_returned_pages_on_disk_and_groups_duplicates(tmp_path, content):
    journal = ReturnedArtifactJournal(tmp_path)
    for name in ("one", "two"):
        result = repr({"artifact_ref": f"artifact:{name}", "content": content, "start_line": 1})
        journal.record("read_artifact", {"path": name}, result)
    rendered = journal.context(10000, len)
    assert rendered.count("\"returned_payload\":") == 1
    assert "artifact:one" in rendered and "artifact:two" in rendered
    record = json.loads(next(journal.directory.glob("*.json")).read_text())
    assert ast.literal_eval(record["payload"])["content"] == content
    assert "payload" not in journal.__dict__
    other = ReturnedArtifactJournal(tmp_path)
    assert other.context(10000, len) == ""
    assert other.directory != journal.directory
    omitted = journal.context(0, len)
    assert "\"returned_payload\":" not in omitted
    assert "access limitation" in omitted and "artifact:two" in omitted


@pytest.mark.parametrize("search", [False, True])
def test_bounded_tools_journal_only_successful_authorized_reads(tmp_path, monkeypatch, search):
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    from modules.tools import memory
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    (tmp_path / "form.html").write_text("<head>" + "x" * 1000 + "</head>\n<form>📧</form>")
    journal = ReturnedArtifactJournal(tmp_path / "returned")
    if search:
        reader = artifact.create_bounded_artifact_searcher(
            max_searches=1, allowed_artifact_refs=["artifact:form.html"], returned_journal=journal,
        )
        arguments = {"search_pattern": "form"}
    else:
        reader = artifact.create_bounded_artifact_reader(
            max_reads=1, context_window_tokens=48000,
            allowed_artifact_refs=["artifact:form.html"], returned_journal=journal,
        )
        arguments = {}
    with pytest.raises(RuntimeError, match="POLICY_VIOLATION"):
        reader(path="foreign.html", **arguments)
    assert not journal.index.exists()
    result = reader(path="form.html", **arguments)
    with pytest.raises(RuntimeError):
        reader(path="form.html", **arguments)
    assert len(journal.index.read_text().splitlines()) == 1
    rendered = journal.context(10000, len)
    assert "📧" in rendered
    assert json.loads(next(journal.directory.glob("*.json")).read_text())["payload"] == result


def test_replay_preserves_old_implementation_across_later_summary_reads(tmp_path):
    journal = bound_journal(tmp_path)
    source = "identity = storage.read(); permission(identity); search(query);"
    reference = journal.record_inspection(
        "read_artifact", "implementation", {"path": "artifact:implementation", "start_line": 10}, repr({"content": source}),
        ("artifact:implementation",),
    )
    for index in range(15):
        journal.record_inspection("search_artifact", str(index), {"path": "artifact:summary"},
                                  repr({"matches": [{"content": f"route listing {index}"}]}), ("artifact:summary",))
    replay = journal.replay_context(6500, len)
    assert reference in replay and "permission(identity)" in replay
    assert len(replay) <= 6500
    assert "index.jsonl" in replay
    # A large exact result remains recoverable, with explicit serialized-result slice offsets.
    large = journal.record_inspection("read_artifact", "large", {}, repr({"content": "network data " * 3000}), ())
    replay = journal.replay_context(4000, len)
    block = next(json.loads(line) for line in replay.splitlines() if line.startswith("{") and large in line and '"offset"' in line)
    record = journal.load_inspection(large)
    assert block["offset"] == 0
    assert block["text"] == json.dumps(record, ensure_ascii=False)[:block["next_offset"]]
    assert json.loads(journal.reader()(large, offset=block["next_offset"]))["text"]


@pytest.mark.parametrize("record,expected", [
    ({"tool": "read_artifact", "payload": "invalid"}, ""),
    ({"tool": "read_artifact", "payload": "[]"}, ""),
    ({"tool": "search_artifact", "payload": '{"matches":[{"content":"summary"}]}'}, ""),
    ({"tool": "get_inventory_item", "payload":
      '{"context_entries":[{"type":"diagnostic"},{"type":"source_excerpt","text":"exact bytes"}]}'}, "exact bytes"),
    ({"tool": "read_hypothesis_input", "payload": '{"text":"network banner"}'}, "network banner"),
])
def test_inspection_anchors_are_verbatim_content_only(record, expected):
    from modules.tools.artifact_journal import inspection_anchor

    assert inspection_anchor(record) == expected



def test_targeted_source_reads_survive_later_whole_artifact_reads(tmp_path):
    journal = bound_journal(tmp_path)
    observed = "const identity = storage.get(); permission(identity); catalog(query);"
    reference = journal.record_inspection("read_artifact", "targeted", {"path": "artifact:bundle", "start_line": 40},
                                          repr({"content": observed}), ("artifact:bundle",))
    journal.record_inspection("read_artifact", "other-range", {"path": "artifact:bundle", "start_line": 80},
                              repr({"content": "independent source range"}), ("artifact:bundle",))
    for index in range(8):
        journal.record_inspection("read_artifact", str(index), {"path": f"artifact:summary{index}"},
                                  repr({"content": "route declarations " * 100}), (f"artifact:summary{index}",))
    replay = journal.replay_context(4000, lambda text: (len(text) + 3) // 4)
    anchors = replay.split("## Retained inspection evidence")[0]
    assert reference in anchors and "catalog(query)" in anchors
    assert (len(replay) + 3) // 4 <= 4000
