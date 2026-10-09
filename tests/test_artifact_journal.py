"""Exact returned evidence remains available after bounded artifact access closes."""

import ast
import json

import pytest

from modules.tools import artifact
from modules.tools.artifact_journal import ReturnedArtifactJournal


@pytest.mark.parametrize("content", ["HTTP/1.1 201\n<form>📧</form>", "SSH banner\nport 22", "def main():\n    pass"])
def test_journal_preserves_exact_returned_pages_on_disk_and_groups_duplicates(tmp_path, content):
    journal = ReturnedArtifactJournal(tmp_path)
    for name in ("one", "two"):
        result = repr({"artifact_ref": f"artifact:{name}", "content": content, "start_line": 1})
        journal.record("read_artifact", {"path": name}, result)
    rendered = journal.context(10000, len)
    assert rendered.count('"returned_payload":') == 1
    assert "artifact:one" in rendered and "artifact:two" in rendered
    record = json.loads(next(journal.directory.glob("*.json")).read_text())
    assert ast.literal_eval(record["payload"])["content"] == content
    assert "payload" not in journal.__dict__
    other = ReturnedArtifactJournal(tmp_path)
    assert other.context(10000, len) == ""
    assert other.directory != journal.directory
    omitted = journal.context(0, len)
    assert '"returned_payload":' not in omitted
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
