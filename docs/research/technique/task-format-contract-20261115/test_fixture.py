"""Offline checks for the experiment's fixture and deterministic grading."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("comparison", Path(__file__).with_name("run.py"))
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.mark.parametrize("mode", ["vulnerable", "shared", "unknown"])
def test_fixture_and_grading(mode):
    assert comparison.context(mode, "identity")["session-red"] == "A"
    assert comparison.response(mode, "session-red", "invalid") == comparison.response(mode, "session-red", "A")
    assert "object" not in comparison.response("shared", "session-blue", "A")["body"]
    with pytest.raises(ValueError):
        comparison.context(mode, "bad")
    with pytest.raises(ValueError):
        comparison.response(mode, "bad", "A")
    expected = {"vulnerable": "violation", "shared": "no_violation_observed", "unknown": "inconclusive"}[mode]
    answer = comparison.Decision(classification=expected, evidence_ids=["1", "2", "3"], explanation="Evidence")
    rows = [{"id": "1", "kind": "policy"}, {"id": "2", "kind": "identity"},
            {"id": "3", "principal": "session-red", "selector": "B"}]
    assert comparison.grade(mode, answer, rows)["supported_correct"]
    assert not comparison.grade(mode, answer, rows[:2])["supported_correct"]
    answer.classification = "violation" if expected != "violation" else "inconclusive"
    assert not comparison.grade(mode, answer, rows)["supported_correct"]


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
            for _ in range(13):
                asyncio.run(ollama.AsyncClient().chat())
        else:
            asyncio.run(ollama.AsyncClient().chat())
        identity = json.loads(agent.tools[1](kind="identity"))
        policy = json.loads(agent.tools[1](kind="policy"))
        record = json.loads(agent.tools[0](principal="session-red", selector="B"))
        classification = ("inconclusive" if not policy["response"]["available"] else
                          "no_violation_observed" if "B" in policy["response"]["permissions"]["A"] else "violation")
        return SimpleNamespace(structured_output=schema(classification=classification,
                               evidence_ids=[identity["id"], policy["id"], record["id"]], explanation="Observed"))

    monkeypatch.setattr(ollama.AsyncClient, "chat", chat)
    monkeypatch.setattr(strands, "Agent", Agent)
    monkeypatch.setattr(factory, "create_strands_model", lambda *args: None)
    monkeypatch.setattr(factory, "require_prompt_token_limit", lambda *args: 40000)
    monkeypatch.setattr(diagnostics, "DiagnosticSession", Session)
    monkeypatch.setattr(native_structured, "invoke_structured", invoke)
    comparison.main()
    result = json.loads(next(tmp_path.glob("outputs/**/results.json")).read_text())
    assert len(result["outcomes"]) == 1
    assert result["counts"]["requests"] == (12 if exhaust else 1)
    assert result["outcomes"][0]["status"] == ("unresolved" if exhaust else "completed")
    if not exhaust:
        assert all(row["supported_correct"] for row in result["outcomes"])
    assert ollama.AsyncClient.chat is chat


def test_shared_contract_is_format_only():
    import json

    from modules.techniques.roles import SYSTEM

    prompt = comparison.task_system()
    assert SYSTEM not in prompt
    assert "Unknown prerequisites require future checks" not in prompt
    assert "When finalizing, produce exactly one JSON object" in prompt
    assert json.loads(prompt.splitlines()[-1]) == comparison.Decision.model_json_schema()
