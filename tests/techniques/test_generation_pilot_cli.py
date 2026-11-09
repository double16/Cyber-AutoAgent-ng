"""CLI gates live requests and keeps the default path offline."""

import json
from pathlib import Path

import pytest

from modules.techniques import generation_pilot_cli as cli
from modules.techniques.review_calibration import code_identity


@pytest.fixture
def inputs(tmp_path):
    cases = json.loads(Path("docs/research/technique-generation-yield-pilot/cases.json").read_text())
    protocol = {"status": "prepared", "candidate_code_identity": code_identity(Path.cwd()),
                "cases": [{"id": row["id"], "input_sha256": row["input_sha256"]} for row in cases],
                "provider": "ollama", "model": "scripted"}
    case_path, protocol_path = tmp_path / "cases.json", tmp_path / "protocol.json"
    case_path.write_text(json.dumps(cases))
    protocol_path.write_text(json.dumps(protocol))
    return ["--cases", str(case_path), "--protocol", str(protocol_path), "--output", str(tmp_path / "output")]


def test_cli_rejects_live_before_creating_output(inputs, monkeypatch):
    monkeypatch.setattr(cli, "preflight", lambda *args: pytest.fail("No preflight before authorization"))
    with pytest.raises(SystemExit):
        cli.main([*inputs, "--run"])
    assert not Path(inputs[-1]).exists()


@pytest.mark.parametrize("live", [False, True])
def test_cli_routes_offline_and_authorized_live(inputs, monkeypatch, live):
    calls = []
    if live:
        path = Path(inputs[3])
        protocol = json.loads(path.read_text())
        protocol.update(status="authorized", authorized_provider_request_cap=30)
        path.write_text(json.dumps(protocol))
    monkeypatch.setattr(cli, "preflight", lambda *args: calls.append("offline") or {"status": "passed"})
    monkeypatch.setattr(cli, "run", lambda *args: calls.append("live"))
    assert cli.main([*inputs, *(["--run"] if live else [])]) == 0
    assert calls == (["offline", "live"] if live else ["offline"])


def test_transport_adapter_rejects_other_provider(inputs):
    path = Path(inputs[3])
    protocol = json.loads(path.read_text())
    protocol["provider"] = "unsupported"
    path.write_text(json.dumps(protocol))
    with pytest.raises(SystemExit):
        cli.main(inputs)


def test_local_worker_selects_transport_and_diagnostics(tmp_path, monkeypatch):
    import ollama

    from modules.techniques import roles

    captured = []
    runner = object()
    monkeypatch.setattr(roles, "TechniqueRoleRunner", lambda *args, **kwargs: runner)

    def generate(case, directory, supplied_runner, budget, **kwargs):
        captured.append(kwargs)
        assert supplied_runner is runner
        return {"status": "scripted"}

    monkeypatch.setattr(cli, "run_generation", generate)
    assert cli.local_worker({}, tmp_path, object(), model="scripted")["status"] == "scripted"
    assert captured[0]["transport"] == (ollama.AsyncClient, "chat")
    assert captured[0]["diagnostics"] is not None
