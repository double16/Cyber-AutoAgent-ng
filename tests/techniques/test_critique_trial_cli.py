"""The live comparison entrypoint isolates storage and withholds adjudication labels."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from strands.types.exceptions import MaxTokensReachedException

from modules.techniques import critique_trial_cli as cli
from modules.techniques.critique import Critique
from modules.techniques.critique_experiment import Variant


def case():
    return cli.FrozenCase(id="case", draft="Guide", requirements=[], sources=[])


def test_schedule_is_repeatable_and_complete():
    jobs = cli.schedule([case()], list(Variant), 3, 7)
    assert len(jobs) == 12
    assert jobs == cli.schedule([case()], list(Variant), 3, 7)
    with pytest.raises(ValueError):
        cli.schedule([], [], 0, 7)


@pytest.mark.parametrize("failed", [True, False])
def test_trial_records_resolved_and_unresolved(tmp_path, monkeypatch, failed):
    def assess(*args):
        if failed:
            raise ValueError("Unresolved after one correction")
        return Critique(approved=True, reason="Complete", no_security_checks_reason="No checks")
    monkeypatch.setattr(cli, "assess", assess)
    directory = tmp_path / "trial"
    outcome = cli.run_trial(case(), Variant.A, SimpleNamespace(), directory)
    assert outcome["status"] == ("unresolved" if failed else "completed")
    assert outcome["human_adjudication"] == "pending"
    assert json.loads((directory / "outcome.json").read_text()) == outcome
    assert (directory / "model-evidence.json").exists()


def test_output_exhaustion_is_unresolved_and_does_not_abort_next_trial(tmp_path, monkeypatch):
    calls = []

    def assess(*args):
        calls.append(args)
        if len(calls) == 1:
            raise MaxTokensReachedException("Output budget exhausted")
        return Critique(approved=True, reason="Complete", no_security_checks_reason="No checks")

    monkeypatch.setattr(cli, "assess", assess)
    failed = cli.run_trial(case(), Variant.B, SimpleNamespace(), tmp_path / "failed")
    following = cli.run_trial(case(), Variant.A, SimpleNamespace(), tmp_path / "following")
    assert failed["status"] == "unresolved"
    assert failed["error_type"] == "MaxTokensReachedException"
    assert "review" not in failed
    assert json.loads((tmp_path / "failed" / "outcome.json").read_text()) == failed
    assert following["status"] == "completed"
    assert len(calls) == 2  # No retry or increased generation budget for the exhausted trial.


def test_unexpected_failure_is_recorded_and_still_raised(tmp_path, monkeypatch):
    def assess(*args):
        raise KeyError("Unexpected harness defect")

    monkeypatch.setattr(cli, "assess", assess)
    directory = tmp_path / "trial"
    with pytest.raises(KeyError, match="Unexpected harness defect"):
        cli.run_trial(case(), Variant.A, SimpleNamespace(), directory)
    outcome = json.loads((directory / "outcome.json").read_text())
    assert outcome["status"] == "interrupted"
    assert outcome["error_type"] == "KeyError"
    assert "review" not in outcome
    assert (directory / "model-evidence.json").exists()


@pytest.mark.parametrize("mode", ["dry", "live", "duplicate"])
def test_cli_modes(tmp_path, monkeypatch, capsys, mode):
    corpus = tmp_path / "cases.json"
    corpus.write_text(json.dumps([case().model_dump()] * (2 if mode == "duplicate" else 1)))
    argv = ["trial", str(corpus), "--variants", Variant.A.value]
    if mode == "dry":
        argv.append("--dry-run")
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(cli.tempfile, "mkdtemp", lambda **kw: str(tmp_path))
    monkeypatch.setattr(cli, "DiagnosticSession", lambda **kw: SimpleNamespace(
        open=lambda path: None, close=lambda: None, lifecycle=lambda *a, **kw: None))
    monkeypatch.setattr(cli, "TechniqueRoleRunner", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(cli, "assess", lambda *args: Critique(approved=True, reason="Complete"))
    if mode == "duplicate":
        with pytest.raises(SystemExit):
            cli.main()
    else:
        cli.main()
        if mode == "dry":
            assert json.loads(capsys.readouterr().out)["trials"] == 1
        else:
            assert (tmp_path / "trial-000" / "outcome.json").exists()
            assert (tmp_path / "configuration.json").exists()


def test_screen_corpus_has_paired_independent_expectations():
    root = Path(__file__).parent / "fixtures"
    cases = [cli.FrozenCase.model_validate(row) for row in json.loads((root / "critique_screen.json").read_text())]
    labels = json.loads((root / "critique_screen_expectations.json").read_text())
    assert len(cases) == len(labels) == 8
    assert {c.id for c in cases} == {row["id"] for row in labels}
    assert sum(row["expected_approval"] for row in labels) == 4
    assert all(requirement.kind == "authored_deliverable" for item in cases for requirement in item.requirements)


@pytest.mark.parametrize("as_model", [False, True])
def test_experiment_rejects_implicit_requirement_kind(as_model):
    from pydantic import ValidationError

    from modules.techniques.coverage import Requirement

    row = {"id": "method", "description": "Provide a reusable procedure"}
    requirement = Requirement(**row) if as_model else row
    with pytest.raises(ValidationError, match="declare its kind explicitly"):
        cli.FrozenCase(id="case", draft="Guide", requirements=[requirement], sources=[])


@pytest.mark.parametrize("kind", ["factual_support", "authored_deliverable", "constraint"])
def test_explicit_requirement_kind_is_authoritative(kind):
    from modules.techniques.coverage import Requirement

    requirement = Requirement(id="method", description="Provide a procedure", kind=kind)
    result = cli.FrozenCase(id="case", draft="Guide", requirements=[requirement], sources=[])
    assert result.requirements[0].kind == kind


@pytest.mark.parametrize("data", ["invalid", {"id": "case", "draft": "Guide", "requirements": None, "sources": []}])
def test_invalid_case_shapes_use_normal_validation(data):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        cli.FrozenCase.model_validate(data)
