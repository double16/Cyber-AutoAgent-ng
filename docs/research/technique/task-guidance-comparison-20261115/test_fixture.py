"""Offline checks for the experiment's fixture and deterministic grading."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("comparison", Path(__file__).with_name("run.py"))
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.mark.parametrize("mode,status", [("fallback", 200), ("enforced", 403), ("shape", 206)])
def test_policy_control_and_counterexample(mode, status):
    query = comparison.response
    assert query(mode, "A", "invalid") == query(mode, "A", "A")
    assert query(mode, "A", "B")["status"] == status
    assert query(mode, "B", "B")["body"]["owner"] == "B"
    with pytest.raises(ValueError):
        query(mode, "unknown", "A")


@pytest.mark.parametrize("mode", ["fallback", "enforced", "shape"])
def test_grading_requires_actual_cited_cross_principal_probe(mode):
    expected = "no_violation_observed" if mode == "enforced" else "violation"
    answer = comparison.Decision(classification=expected, evidence_ids=["obs-1"], explanation="Observed")
    observation = {"id": "obs-1", "principal": "A", "selector": "B"}
    assert comparison.grade(mode, answer, [observation])["supported_correct"]
    assert not comparison.grade(mode, answer, [])["supported_correct"]
    answer.classification = "inconclusive"
    assert not comparison.grade(mode, answer, [observation])["supported_correct"]


@pytest.mark.parametrize("exhaust", [False, True])
def test_complete_harness_offline(monkeypatch, tmp_path, exhaust):
    import asyncio
    import json
    from types import SimpleNamespace

    import ollama
    import strands

    from modules.agents import native_structured
    from modules.config.models import factory
    from modules.techniques import diagnostics

    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs/technique_review_trials").mkdir(parents=True)
    path = tmp_path / "docs/research/technique/line-authorship-pipeline-20261115"
    path.mkdir(parents=True)
    (path / "generated-http.md").write_text("Frozen advisory text")

    class Session:
        def __init__(self, **kwargs):
            pass

        def open(self, folder):
            pass

        def close(self):
            pass

        def role(self, *args):
            from contextlib import nullcontext
            return nullcontext(None)

    class Agent:
        def __init__(self, **kwargs):
            self.tools = kwargs["tools"]

    async def chat(*args, **kwargs):
        return None

    def invoke(agent, prompt, schema, **kwargs):
        if exhaust:
            for _ in range(101):
                asyncio.run(ollama.AsyncClient().chat())
        else:
            asyncio.run(ollama.AsyncClient().chat())
        record = json.loads(agent.tools[0](principal="A", selector="B"))
        classification = "no_violation_observed" if record["response"]["status"] == 403 else "violation"
        return SimpleNamespace(structured_output=schema(classification=classification,
                               evidence_ids=[record["id"]], explanation="Observed"))

    monkeypatch.setattr(ollama.AsyncClient, "chat", chat)
    monkeypatch.setattr(strands, "Agent", Agent)
    monkeypatch.setattr(factory, "create_strands_model", lambda *args: None)
    monkeypatch.setattr(factory, "require_prompt_token_limit", lambda *args: 40000)
    monkeypatch.setattr(diagnostics, "DiagnosticSession", Session)
    monkeypatch.setattr(native_structured, "invoke_structured", invoke)
    comparison.main()
    result = json.loads(next(tmp_path.glob("outputs/**/results.json")).read_text())
    assert len(result["outcomes"]) == 12
    assert result["counts"]["requests"] == (100 if exhaust else 12)
    assert result["outcomes"][0]["status"] == ("unresolved" if exhaust else "completed")
    if not exhaust:
        assert all(row["supported_correct"] for row in result["outcomes"])
    assert ollama.AsyncClient.chat is chat
