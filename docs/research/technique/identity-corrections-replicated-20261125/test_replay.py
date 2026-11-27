"""Offline invariants for the frozen replicated controller replay."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("replicated_replay", BASE / "replay.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
CASES = [replay.Case.model_validate(value) for value in json.loads((BASE / "cases.json").read_text())]


def answer(case, schema, *, withdraw=False, split=False):
    value = replay.proposal(case)
    value["positive_expectations"] = ["Controlled variation violates the documented boundary"]
    values = [value, {**value, "suspected_mechanism": "Independent consequence"}] if split else value
    return schema.model_validate({"replacement": None if withdraw else values, "reason": "Evidence-based correction"})


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("arm", ["baseline", "candidate"])
def test_real_dispatch_mutation_scope_and_deterministic_availability(tmp_path, monkeypatch, case, arm):
    replay.write_json(tmp_path / "requests.json", {"requests": 0, "limit": 96})
    calls = []

    class Runner:
        def __init__(self, *_):
            pass

        def structured(self, role, packet, schema, tools):
            calls.append((role, packet, schema, tools))
            if case.required_inspection_refs:
                reader = next(item for item in tools if replay.workflow.get_tool_name(item) == "read_hypothesis_input")
                with pytest.raises(ValueError, match="identified source_ref"):
                    reader(source_ref="artifact:unrelated.txt")
                for ref in case.required_inspection_refs:
                    result = json.loads(reader(source_ref=ref))
                    assert result["text"] == case.sources[ref]
                assert isinstance(packet, replay.DeferredReviewPrompt)
            else:
                assert tools == []
            return answer(case, schema)

    row = replay.trial(case, arm, 0, tmp_path, Runner)
    if arm == "baseline" and not case.comparison.roles and case.comparison.comparison == "account":
        assert "explicit roles" in row["error"] and len(calls) == 2
        assert row["pending"] and "availability" not in row
        assert row["publication"] == row["initial_publication"]
    else:
        assert "error" not in row and not row["pending"]
        assert len(calls) == 1 and len(row["dispatches"]) == 2
        assert row["dispatches"][1]["model_requests"] == 0
        artifact_path = tmp_path / f"{case.name}-{arm}-0" / row["availability"]["artifact_ref"].removeprefix("artifact:")
        assert replay.hashlib.sha256(artifact_path.read_bytes()).hexdigest() == row["availability"]["sha256"]
        assert row["publication"] != row["initial_publication"]
        if case.sibling:
            original = next(item for item in row["initial_publication"]["subjects"][0]["hypotheses"]
                            if item["suspected_mechanism"] == case.sibling["mechanism"])
            assert original in row["publication"]["subjects"][0]["hypotheses"]
    assert row["publication"]["operation_id"] == replay.OPERATION
    assert (tmp_path / f"{case.name}-{arm}-0/state.db").exists()


@pytest.mark.parametrize("bad", ["role", "source", "inline", "canonical"])
def test_fixture_rejects_inconsistent_context(bad):
    value = CASES[0].model_dump()
    if bad == "role":
        value["comparison"]["roles"] = ["unknown"]
    elif bad == "source":
        value["required_inspection_refs"] = ["artifact:missing.js"]
    elif bad == "inline":
        value.update(sources={"artifact:source": "data"}, inline_refs=["artifact:source"],
                     required_inspection_refs=["artifact:source"])
    else:
        value.update(sources={"relative.js": "data"})
    with pytest.raises(ValueError):
        replay.Case.model_validate(value)


def test_arm_contracts_and_fixtures_are_not_shared_implicitly():
    baseline = replay.revision_schema("baseline")
    candidate = replay.revision_schema("candidate")
    value = {"replacement": replay.proposal(CASES[1]), "reason": "Preserve future comparison"}
    assert candidate.model_validate(value).replacement.authentication_requirement.comparison == "account"
    with pytest.raises(ValueError, match="explicit roles"):
        baseline.model_validate(value)
    assert "Account comparisons declare one shared role" in json.dumps(baseline.model_json_schema())
    assert "Empty roles means no named role restriction" in json.dumps(candidate.model_json_schema())
    for case in CASES[:2]:
        assert case.comparison.comparison == "account" and "two distinct" in case.objective
    assert CASES[0].roles == ["member", "administrator"]
    assert CASES[4].required_inspection_refs == ["artifact:render.js"]
    assert "artifact:render.js" in CASES[4].context()
    assert CASES[4].sources["artifact:render.js"] not in CASES[4].context()
    assert CASES[5].sources["artifact:resolver.py"] in CASES[5].context()


@pytest.mark.parametrize("split,withdraw", [(False, False), (True, False), (False, True)])
def test_apply_decision_passes_typed_atomic_inputs(split, withdraw):
    calls = []
    decision = answer(CASES[2], replay.revision_schema("candidate"), split=split, withdraw=withdraw)
    replay.apply_decision(lambda **kwargs: calls.append(kwargs), decision)
    value = calls[0]["replacement"]
    assert value is None if withdraw else isinstance(value, list if split else dict)
    if value:
        assert "hypothesis_id" not in (value[0] if split else value)


def test_scope_and_mutation_error_remain_failed_trial(tmp_path):
    replay.write_json(tmp_path / "requests.json", {"requests": 0, "limit": 96})

    class Runner:
        def __init__(self, *_):
            pass

        def structured(self, _role, _prompt, schema, _tools):
            value = replay.proposal(CASES[0])
            value["authentication_requirement"]["roles"] = ["unknown"]
            value["positive_expectations"] = ["Changed control"]
            return schema.model_validate({"replacement": value, "reason": "Unrecognized named restriction"})

    row = replay.trial(CASES[0], "candidate", 0, tmp_path, Runner)
    assert "not in the declared catalog" in row["error"]
    assert row["pending"] and "availability" not in row
    assert row["publication"] == row["initial_publication"]


def test_full_matrix_and_freeze_refuse_overwrite(tmp_path, monkeypatch):
    root = tmp_path / "run"
    replay.freeze(root, CASES)
    frozen = json.loads((root / "frozen.json").read_text())
    assert len(frozen["matrix"]) == 24
    assert all(sum(name == case.name for name, _, _ in frozen["matrix"]) == 4 for case in CASES)
    first = [(arm, rep) for name, arm, rep in frozen["matrix"] if name == CASES[0].name]
    assert first == [("baseline", 0), ("candidate", 0), ("candidate", 1), ("baseline", 1)]
    assert frozen["limit"] == 96 and frozen["repairs_per_trial"] == 1
    assert len(list((root / "sources").iterdir())) == len(frozen["source_sha256"])
    with pytest.raises(FileExistsError):
        replay.freeze(root, CASES)

    calls = []
    monkeypatch.setattr(replay, "freeze", lambda *_: None)
    monkeypatch.setattr(replay, "trial", lambda case, arm, rep, *_: calls.append((case.name, arm, rep)) or {
        "requests": 0, "error": "Retained transport failure",
    })
    replay.run(root)
    assert calls == frozen["matrix"] or [list(row) for row in calls] == frozen["matrix"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream,error", [(False, False), (False, True), (True, False), (True, True)])
async def test_counter_preserves_transport_and_stream_failures(tmp_path, monkeypatch, stream, error):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    replay.write_json(tmp_path / "requests.json", {"requests": 0, "limit": 1})
    value = SimpleNamespace(model_dump=lambda **_: {"message": "retained"})

    async def chat(*_args, **_kwargs):
        if stream:
            async def chunks():
                yield value
                if error:
                    raise OSError("Retained stream error")
            return chunks()
        if error:
            raise OSError("Retained transport error")
        return value

    monkeypatch.setattr(replay, "ORIGINAL_CHAT", chat)
    if error:
        with pytest.raises(OSError):
            result = await replay.counted(None, stream=stream)
            if stream:
                _ = [item async for item in result]
    else:
        result = await replay.counted(None, stream=stream)
        if stream:
            assert [item async for item in result] == [value]
    saved = json.loads((tmp_path / "request-001.json").read_text())
    assert bool(saved.get("error")) == error
    with pytest.raises(RuntimeError, match="allowance exhausted"):
        await replay.counted(None)
    assert json.loads((tmp_path / "requests.json").read_text())["requests"] == 1


def test_preflight_checks_model_without_generating(monkeypatch):
    monkeypatch.setattr(replay, "load_dotenv", lambda *_: None)
    client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model=replay.MODEL)]))
    monkeypatch.setattr(replay.ollama, "Client", lambda **_: client)
    replay.preflight()
    client.list = lambda: SimpleNamespace(models=[])
    with pytest.raises(RuntimeError, match="unavailable"):
        replay.preflight()


def test_decision_retry_is_schema_only_and_bounded():
    packets = []

    def structured(_role, packet, schema, tools):
        packets.append((packet, tools))
        if len(packets) == 1:
            raise ValueError("Malformed first output")
        return answer(CASES[4], schema)

    call = {}
    replay.decide(SimpleNamespace(structured=structured), replay.revision_schema("candidate"), "Record",
                  [SimpleNamespace(tool_spec={"name": "reader"})], CASES[4], call)
    assert len(call["attempts"]) == 2 and packets[1][1] == []
    assert "Malformed first output" in str(packets[1][0])
    assert isinstance(packets[0][0], replay.DeferredReviewPrompt)
    assert "artifact:render.js" in packets[0][0].investigation


def test_matrix_transport_counter_is_scoped_to_frozen_root(tmp_path, monkeypatch):
    import asyncio

    root = tmp_path / "isolated-matrix"

    async def chat(*_args, **_kwargs):
        return SimpleNamespace(model_dump=lambda **_: {"message": "Retained"})

    def trial(case, arm, rep, directory, *_):
        assert directory == root
        asyncio.run(replay.ollama.AsyncClient().chat(model="offline", stream=False))
        return {"requests": 1}

    monkeypatch.setattr(replay, "ORIGINAL_CHAT", chat)
    monkeypatch.setattr(replay, "trial", trial)
    replay.run(root)
    assert json.loads((root / "requests.json").read_text()) == {"requests": 24, "limit": 96}
    assert len(list(root.glob("request-*.json"))) == 24
