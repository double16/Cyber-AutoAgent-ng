"""Zero-provider-request preflight of actual production executor core and persistence."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from ollama import ChatResponse

spec = importlib.util.spec_from_file_location("executor_pilot", Path(__file__).with_name("run.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@pytest.mark.parametrize("mode,arm", [("unknown", "none"), ("shared", "lookup")])
def test_isolated_acceptance_and_snapshots(tmp_path, mode, arm):
    async def deny(*args, **kwargs):
        raise AssertionError("Offline checks cannot dispatch model requests")

    with patch("ollama.AsyncClient.chat", deny):
        row = pilot.run_case(tmp_path / mode, mode, arm, offline=True)
    assert row["acceptance"] and row["supported_correct"]
    assert "task_evidence" in row["acceptance"][0]["evidence_refs"][0]


@pytest.mark.parametrize("arm", ["none", "lookup"])
def test_scripted_sdk_terminal_policy_and_journal(tmp_path, arm):
    steps = []
    if arm == "lookup":
        steps.append(("get_technique", {"request": "header-selector comparison"}))
    steps.extend(
        [
            ("inspect_context", {"kind": "identity"}),
            ("inspect_context", {"kind": "policy"}),
            ("request_account", {"principal": "session-red", "selector": "B"}),
            (
                "publish_result",
                {
                    "classification": "inconclusive",
                    "evidence_ids": ["obs-1", "obs-2", "obs-3"],
                    "explanation": "Scripted specimen",
                },
            ),
            (
                "record_task_acceptance",
                {
                    "status": "satisfied",
                    "disposition": "observation",
                    "summary": "Comparison artifact produced for independent review",
                    "evidence_refs": ["artifact:artifacts/result.json"],
                },
            ),
        ]
    )
    pending = iter(steps)

    async def scripted(client, **kwargs):
        name, arguments = next(pending)
        event = ChatResponse(
            model="fixture",
            message={
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
            },
            done=True,
            done_reason="stop",
            prompt_eval_count=100,
            eval_count=20,
            total_duration=1000000,
        )
        if not kwargs.get("stream"):
            return event

        async def events():
            yield event

        return events()

    with patch("ollama.AsyncClient.chat", scripted):
        row = pilot.run_case(tmp_path / arm, "unknown", arm)
    assert row["reason"] == "task_executor_done"
    assert row["acceptance"] and row["supported_correct"]
    assert len(row["tool_outcomes"]) == len(steps)
    assert bool(row["lookups"]) == (arm == "lookup")


@pytest.mark.parametrize("exhaust", [False, True])
def test_global_allowance_and_transport_restoration(tmp_path, exhaust):
    import asyncio

    import ollama

    async def original(*args, **kwargs):
        return None

    def worker(folder, mode, arm):
        for _ in range(4 if exhaust else 1):
            asyncio.run(ollama.AsyncClient().chat())
        return {"mode": mode, "arm": arm}

    with patch.object(ollama.AsyncClient, "chat", original):
        result = pilot.run_experiment(tmp_path / "trial", worker=worker, limit=3)
        assert ollama.AsyncClient.chat is original
    assert result["counts"]["requests"] == 3
    assert len(result["outcomes"]) == 4
    assert result["outcomes"][-1]["status"] == "unattempted_budget_exhausted"
    assert result["outcomes"][0]["status"] == ("unresolved" if exhaust else "completed")


@pytest.mark.parametrize("limit", [0, 101, True])
def test_invalid_allowance_rejected_before_output(tmp_path, limit):
    with pytest.raises(ValueError):
        pilot.run_experiment(tmp_path / "invalid", limit=limit)
    assert not (tmp_path / "invalid").exists()


def test_positive_fixture_requires_supported_violation():
    fixture = pilot.load_fixture()
    observations = [
        {"id": "obs-1", "kind": "identity", "response": fixture.context("vulnerable", "identity")},
        {"id": "obs-2", "kind": "policy", "response": fixture.context("vulnerable", "policy")},
        {"id": "obs-3", "principal": "session-red", "selector": "B",
         "response": fixture.response("vulnerable", "session-red", "B")},
    ]
    assert "B" not in observations[1]["response"]["permissions"]["A"]
    assert observations[2]["response"]["body"]["object"]["owner"] == "B"
    decision = fixture.Decision(classification="violation", evidence_ids=["obs-1", "obs-2", "obs-3"],
                                explanation="Cross-account data outside permission policy")
    assert fixture.grade("vulnerable", decision, observations)["supported_correct"]
    assert not fixture.grade("vulnerable", decision.model_copy(update={"classification": "inconclusive"}),
                             observations)["supported_correct"]
    assert not fixture.grade("vulnerable", decision.model_copy(update={"evidence_ids": ["unobserved"]}),
                             observations)["supported_correct"]
