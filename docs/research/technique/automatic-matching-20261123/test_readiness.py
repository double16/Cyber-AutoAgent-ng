"""Contract readiness for ranked matching, delivery receipts and isolated historical guides."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.techniques import delivery
from modules.techniques.documents import TechniqueDocument
from tests.techniques.conftest import markdown, scripted_service, store

BASE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("automatic_matching_diagnostic", BASE / "run.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    generator = store.__wrapped__(tmp_path)
    storage = next(generator)
    p = scripted_service.__wrapped__(storage, markdown.__wrapped__())
    revision = p.publish()
    monkeypatch.setattr(study, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0, "limit": 512}))
    monkeypatch.setattr(study, "Agent", lambda **kwargs: SimpleNamespace(model=kwargs["model"], messages=[]))
    monkeypatch.setattr(study, "create_strands_model", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(study, "require_prompt_token_limit", lambda *_args: 48000)
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda _agent: (48000, 4096))
    monkeypatch.setattr(delivery, "safe_estimate_tokens", lambda *_args, **_kwargs: 1000)
    p.service.select_existing = Mock(return_value=(revision, p.service.store.render(revision, guidance=True), {}))
    yield p, revision
    generator.close()


def test_case_and_capture_manifest_are_complete():
    cases = json.loads((BASE / "cases.json").read_text())
    assert len(cases) == 10
    assert sum(c["expected_file"] is None for c in cases) == 2
    assert len({c["id"] for c in cases}) == 10
    sources = json.loads((BASE / "captured-sources.json").read_text())
    assert len(sources) == 2
    for source in sources:
        doc = TechniqueDocument((BASE / source["snapshot"]).read_text())
        assert doc.sha256 == source["normalized_sha256"]
        assert "feedback_reasons" not in doc.metadata.model_dump()
        assert "use_evaluation" not in doc.metadata.model_dump()
        assert doc.metadata.active


@pytest.mark.parametrize("arm", ["baseline", "candidate"])
def test_matching_leads_reach_actual_receipted_delivery(prepared, tmp_path, monkeypatch, arm):
    p, revision = prepared
    p.service._json = lambda _role, _prompt, schema: schema.model_validate({
        "matches": [{"trigger_id": "t1", "subject_id": "assigned", "observation_reference": "o1"}],
    })
    case = json.loads((BASE / "cases.json").read_text())[0]
    folder = tmp_path / "matching"
    folder.mkdir()
    matched = study.match(p.service, case, folder)
    assert matched["status"] == "completed"
    assert matched["leads"]
    assert study.match(p.service, case, folder) == matched
    study.deliver(p.service, case, matched, tmp_path / arm, arm)
    result = json.loads((tmp_path / arm / "outcome.json").read_text())
    assert result["revision"] == revision
    assert result["delivery_count"] == 1
    kwargs = p.service.select_existing.call_args.kwargs
    assert "task_context" not in kwargs
    assert "purpose" not in p.service.select_existing.call_args.args[1].model_dump()
    study.deliver(p.service, case, matched, tmp_path / arm, arm)
    assert p.service.select_existing.call_count == 1


@pytest.mark.parametrize("status", ["completed", "empty", "invalid"])
def test_empty_and_failed_matching_are_distinct(prepared, tmp_path, status):
    p, _revision = prepared
    case = json.loads((BASE / "cases.json").read_text())[-1]
    from modules.techniques.context import LookupContext

    matched = {"status": status, "leads": [], "lookup": LookupContext().model_dump()}
    study.deliver(p.service, case, matched, tmp_path / "arm", "candidate")
    row = json.loads((tmp_path / "arm/outcome.json").read_text())
    assert row["status"] == ("matching_unavailable" if status == "invalid" else "finished")
    assert not row.get("delivery_count")
    p.service.select_existing.assert_not_called()
