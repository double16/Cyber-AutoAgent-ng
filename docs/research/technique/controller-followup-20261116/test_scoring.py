"""Accepted artifact identity is independent of mutable aliases and filenames."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("accepted_scoring", Path(__file__).with_name("scoring.py"))
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


def test_frozen_result_wins_over_latest_and_duplicate_refs(tmp_path):
    frozen = {"decision": {"classification": "inconclusive"}, "observations": []}
    (tmp_path / "frozen.json").write_text(json.dumps(frozen))
    (tmp_path / "result.json").write_text(json.dumps({"decision": {"classification": "violation"},
                                                    "observations": []}))
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "metadata.json").write_text('{"available": true}')
    result = scoring.accepted_result(tmp_path, [{"evidence_refs": ["artifact:frozen.json", "artifact:frozen.json",
                                                                 "artifact:metadata.json", "artifact:list.json", "memory:example"]}])
    assert result["payload"] == frozen
    assert result["references"] == ["artifact:frozen.json", "artifact:frozen.json"]
    assert scoring.accepted_result(tmp_path, []) is None


def test_ambiguous_accepted_results_are_not_ranked_by_filename(tmp_path):
    for name, classification in [("old.json", "inconclusive"), ("new.json", "violation")]:
        (tmp_path / name).write_text(json.dumps({"decision": {"classification": classification}, "observations": []}))
    with pytest.raises(ValueError, match="Multiple distinct"):
        scoring.accepted_result(tmp_path, [{"evidence_refs": ["artifact:old.json", "artifact:new.json"]}])


def test_missing_and_escaping_sources_do_not_fall_back_to_latest(tmp_path):
    with pytest.raises(FileNotFoundError):
        scoring.accepted_result(tmp_path, [{"evidence_refs": ["artifact:missing.json"]}])
    with pytest.raises(ValueError, match="escapes"):
        scoring.accepted_result(tmp_path, [{"evidence_refs": ["artifact:../outside.json"]}])
