"""Scripted readiness for isolated stage handoffs, never semantic effectiveness."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import run as pipeline
from fixtures import CASES

from modules.tools.hypotheses import Hypothesis
from tests.test_hypotheses import hypothesis


@pytest.mark.parametrize("kind,vulnerable", CASES)
@pytest.mark.parametrize("arm", ["baseline", "candidate"])
def test_scripted_pipeline_reaches_real_fixture_and_independent_grade(tmp_path, monkeypatch, kind, vulnerable, arm):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0, "limit": 360}))
    kwargs_seen = []
    service = SimpleNamespace(events=SimpleNamespace(scope=lambda **_kwargs: nullcontext(), callback=None))

    def select(_request, _context, **kwargs):
        kwargs_seen.append(kwargs)
        return None

    service.select_existing = select
    folder = tmp_path / f"case-00-{arm}"

    class Scripted:
        def structured(self, _role, _prompt, schema, tools):
            if schema is Hypothesis:
                return Hypothesis.model_validate({**hypothesis(), "supporting_refs": ["artifact:source.txt"]})
            if schema is pipeline.Result:
                policy = json.loads(tools[0](kind="policy"))
                principal = "anonymous" if kind == "http" else "first"
                baseline = json.loads(tools[1](principal=principal, input_value="red" if kind == "network" else "inside"))
                variation = json.loads(tools[1](principal=principal, input_value="blue" if kind == "network" else "outside"))
                return schema(classification="violation" if vulnerable else "no_violation_observed",
                              evidence_ids=[policy["id"], baseline["id"], variation["id"]],
                              explanation="Scripted contract specimen")
            publication = json.loads((folder / "current.json").read_text())
            identifier = publication["subjects"][0]["hypotheses"][0]["hypothesis_id"]
            return schema.model_validate({"status": "done", "reason": "Scripted contract specimen",
                "repair": {"kind": "none"}, "subject_reviews": [{
                    "item_id": "subject", "decision": "retain", "reason": "Scripted",
                    "hypotheses": [{"hypothesis_id": identifier, "decision": "retain", "reason": "Scripted",
                        "semantic_checks": {name: {"verdict": "adequate", "reason": "Scripted specimen"}
                                            for name in ("grounding", "test_discrimination", "identity_requirements")}}],
                }]})

    pipeline.trial(service, Scripted(), 0, kind, vulnerable, arm)
    result = json.loads((folder / "outcome.json").read_text())
    assert result["status"] == "completed", result
    assert result["supported_correct"]
    assert not result["selection_available"]
    assert ("task_context" in kwargs_seen[0]) is (arm == "candidate")
    pipeline.trial(service, Scripted(), 0, kind, vulnerable, arm)
    assert len(kwargs_seen) == 1


def test_selection_failure_is_retained_without_proposal_or_execution(tmp_path, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0, "limit": 360}))
    service = SimpleNamespace(events=SimpleNamespace(scope=lambda **_kwargs: nullcontext(), callback=None),
                              select_existing=Mock(side_effect=RuntimeError("fixture selection unavailable")))
    runner = Mock()
    pipeline.trial(service, runner, 0, "filesystem", False, "candidate")
    result = json.loads((tmp_path / "case-00-candidate/outcome.json").read_text())
    assert result["stage"] == "selection"
    assert result["status"] == "unresolved"
    assert result["observations"] == []
    runner.structured.assert_not_called()


def test_request_caps_stop_before_network_call(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    original = AsyncMock()
    monkeypatch.setattr(pipeline, "ORIGINAL_CHAT", original)
    monkeypatch.setitem(pipeline.SCOPE, "trial_start", 0)
    for count, limit in [(30, 360), (360, 360)]:
        (tmp_path / "requests.json").write_text(json.dumps({"requests": count, "limit": limit}))
        with pytest.raises(RuntimeError, match="cap"):
            asyncio.run(pipeline.counted(None))
    original.assert_not_called()
