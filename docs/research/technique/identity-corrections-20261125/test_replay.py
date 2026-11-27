"""Offline checks of the paired replay's transport and actual controller mutations."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("identity_replay", Path(__file__).with_name("replay.py"))
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def test_baseline_keeps_objective_and_roles_but_omits_siblings():
    _, source, guidance = replay.baseline_dispatch()
    assert "Assigned objective" in source and "_hypothesis_identity_context" in source
    assert "sibling_context" not in source and "tools.append(build_read_hypotheses_tool" not in source
    assert "normally permits" not in guidance


@pytest.mark.parametrize("arm", ["baseline", "candidate"])
@pytest.mark.parametrize("name", list(replay.CASES))
def test_offline_replay_uses_controller_and_durable_tools(tmp_path, monkeypatch, arm, name):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0}))

    def invoke(_runner, _role, _prompt, tools):
        terminal = next(item for item in tools if replay.workflow.get_tool_name(item) in {
            "revise_hypothesis", "record_task_acceptance",
        })
        if replay.workflow.get_tool_name(terminal) == "revise_hypothesis":
            terminal(replacement=None, reason="Offline withdrawal to verify real durable mutation")
        else:
            terminal()
        return "Saved"

    monkeypatch.setattr(replay.TechniqueRoleRunner, "__call__", invoke)
    baseline, _, guidance = replay.baseline_dispatch()
    row = replay.trial(name, arm, 0, baseline if arm == "baseline" else
                       replay.workflow.MultiAgentWorkflowController._run_hypothesis_correction_batch, guidance)
    assert "error" not in row
    assert row["acceptance_called"] and not row["pending"]
    assert len(row["dispatches"]) == 2
    assert len(row["publication"]["subjects"][0]["hypotheses"]) == (1 if name == "rendering" else 0)
    assert (tmp_path / f"{name}-{arm}-0/state.db").exists()


def test_baseline_identity_rejection_and_candidate_acceptance():
    value = {**replay.hypothesis(), "authentication_requirement": {"comparison": "account", "roles": []}}
    with pytest.raises(ValueError, match="explicit roles"):
        replay.BaselineHypothesis.model_validate(value)
    assert replay.hypotheses.Hypothesis.model_validate(value).authentication_requirement.comparison == "account"


@pytest.mark.asyncio
async def test_request_cap_preserves_failure_without_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 96, "limit": 96}))
    with pytest.raises(RuntimeError, match="96-request"):
        await replay.counted(None)
    assert json.loads((tmp_path / "requests.json").read_text())["requests"] == 96


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_transport_retains_responses_and_counts_requests(tmp_path, monkeypatch, stream):
    from types import SimpleNamespace

    monkeypatch.setattr(replay, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0, "limit": 72}))
    value = SimpleNamespace(model_dump=lambda **_: {"message": "retained"})

    async def response(_client, **kwargs):
        assert kwargs["stream"] == stream
        if stream:
            async def chunks():
                yield value
            return chunks()
        return value

    monkeypatch.setattr(replay, "ORIGINAL", response)
    result = await replay.counted(None, stream=stream)
    if stream:
        assert [item async for item in result] == [value]
    else:
        assert result == value
    saved = json.loads((tmp_path / "request-001.json").read_text())
    assert saved["response_chunks" if stream else "response"]
    assert json.loads((tmp_path / "requests.json").read_text()) == {"requests": 1, "limit": 72}


@pytest.mark.asyncio
async def test_transport_preserves_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    (tmp_path / "requests.json").write_text(json.dumps({"requests": 0, "limit": 72}))

    async def response(*_args, **_kwargs):
        raise ConnectionError("Offline transport failure")

    monkeypatch.setattr(replay, "ORIGINAL", response)
    with pytest.raises(ConnectionError):
        await replay.counted(None)
    assert "Offline transport failure" in json.loads((tmp_path / "request-001.json").read_text())["error"]


@pytest.mark.parametrize("split", [False, True])
def test_structured_adapter_applies_typed_revisions_through_bound_tool(monkeypatch, split):
    from types import SimpleNamespace

    path = Path(__file__).with_name("replay_structured.py")
    spec = importlib.util.spec_from_file_location("structured_replay", path)
    structured = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(structured)
    calls = []

    @replay.tool(name="revise_hypothesis")
    def revise_hypothesis(replacement: dict | list | None, reason: str) -> str:
        """Capture deterministic invocation after structured output."""
        calls.append((replacement, reason))
        return "Saved"

    adapter = structured.StructuredControllerRunner.__new__(structured.StructuredControllerRunner)
    value = replay.hypothesis()
    adapter.runner = SimpleNamespace(structured=lambda *_: structured.Revision(
        replacement=[value, {**value, "suspected_mechanism": "Complementary mechanism"}] if split else value,
        reason="Distinguish permitted behavior",
    ))
    adapter("task_executor", replay.RolePrompt("", "Exact record"), [revise_hypothesis])
    assert len(calls) == 1
    assert isinstance(calls[0][0], list if split else dict)
    assert "hypothesis_id" not in (calls[0][0][0] if split else calls[0][0])
    with pytest.raises(ValueError, match="exactly one"):
        adapter("task_executor", replay.RolePrompt("", "Exact record"), [])


def test_structured_linked_source_keeps_inspection_before_finalization():
    from types import SimpleNamespace

    path = Path(__file__).with_name("replay_structured.py")
    spec = importlib.util.spec_from_file_location("structured_source_replay", path)
    structured = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(structured)
    packets = []

    @replay.tool(name="revise_hypothesis")
    def revise_hypothesis(replacement: dict | list | None, reason: str) -> str:
        """Apply an offline final disposition."""
        return reason

    def reason(_role, packet, _schema, _tools):
        packets.append(packet)
        return structured.Revision(replacement=None, reason="No supported consequence")

    adapter = structured.StructuredControllerRunner.__new__(structured.StructuredControllerRunner)
    adapter.runner = SimpleNamespace(structured=reason)
    adapter.require_inspection = True
    adapter("task_executor", replay.RolePrompt("", "Inspect retained linked consumer"), [revise_hypothesis])
    assert isinstance(packets[0], structured.DeferredReviewPrompt)
    assert "read-only tools" in packets[0].investigation
    assert "structured final output" in packets[0].comparison
