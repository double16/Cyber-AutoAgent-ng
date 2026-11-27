"""Offline checks for the frozen diagnostic's contracts and request allowance."""

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

SPEC = importlib.util.spec_from_file_location("focused_study", Path(__file__).with_name("run.py"))
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


@pytest.mark.parametrize("name", study.EDITS)
@pytest.mark.parametrize("ids", [["record-0"], ["record-0", "record-1"]])
def test_edit_packets_have_complete_records_and_bound_output_ids(name, ids):
    publication, review = study.edit_inputs(name, *study.EDITS[name])
    assert len(publication.subjects[0].hypotheses) == 2
    assert {row["hypothesis_id"] for row in review["subject_reviews"][0]["hypotheses"]} == {
        "record-0", "record-1",
    }
    schema = study.edit_schema(ids)
    rows = [{"hypothesis_id": identifier, "replacement": None, "reason": "Unsupported premise"}
            for identifier in ids]
    assert len(schema.model_validate({"corrections": rows}).corrections) == len(ids)
    with pytest.raises(ValidationError):
        schema.model_validate({"corrections": [{**rows[0], "hypothesis_id": "unassigned"}, *rows[1:]]})
    with pytest.raises(ValidationError):
        schema.model_validate({"corrections": []})


def test_counted_transport_stops_at_allowance_before_invoking_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "ROOT", tmp_path)
    provider = AsyncMock(return_value="response")
    monkeypatch.setattr(study, "ORIGINAL", provider)
    counter = tmp_path / "requests.json"
    counter.write_text(json.dumps({"requests": 0, "limit": 1}))
    assert asyncio.run(study.counted("client", model="fixture")) == "response"
    with pytest.raises(RuntimeError, match="allowance exhausted"):
        asyncio.run(study.counted("client", model="fixture"))
    assert provider.await_count == 1
    assert json.loads(counter.read_text())["requests"] == 1


@pytest.mark.parametrize("case", study.SOURCES.values())
def test_source_fixtures_have_declared_expected_decisions(case):
    content, decision = case
    assert content.strip()
    assert decision in study.Assessment.model_fields["decision"].annotation.__args__


def test_main_preserves_failures_skips_completed_trials_and_rejects_source_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "ROOT", tmp_path)
    monkeypatch.setattr(study, "load_dotenv", lambda: None)
    monkeypatch.setattr(study, "ORIGINAL", AsyncMock(return_value="response"))
    calls = []

    class Runner:
        def structured(self, role, prompt, schema, tools):
            asyncio.run(study.counted("client"))
            calls.append((role, prompt))
            assert not tools
            if len(calls) == 1:
                raise ValueError("Retain this provider failure")
            if schema is study.Assessment:
                expected = next(expected for content, expected in study.SOURCES.values() if content == prompt.data)
                return schema(decision=expected, observed_flow=[], reason="Fixture", future_discriminating_check="Future")
            record = schema.model_fields["corrections"].annotation.__args__[0]
            identifiers = record.model_fields["hypothesis_id"].annotation.__args__
            return schema.model_validate({"corrections": [
                {"hypothesis_id": identifier, "replacement": None, "reason": "Unsupported premise"}
                for identifier in identifiers
            ]})

    monkeypatch.setattr(study, "TechniqueRoleRunner", lambda *_args: Runner())
    study.main()
    outcomes = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")
                if path.name not in {"requests.json", "source-hashes.json"}]
    assert len(outcomes) == 24
    assert sum(row["status"] == "failed" for row in outcomes) == 1
    count = len(calls)
    study.main()
    assert len(calls) == count
    assert json.loads((tmp_path / "requests.json").read_text())["requests"] == count
    original = study.historical
    monkeypatch.setattr(study, "historical", lambda: ("changed source", original()[1]))
    with pytest.raises(RuntimeError, match="Frozen sources changed"):
        study.main()
