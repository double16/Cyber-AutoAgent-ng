"""Prepared and live review subjects use the same complete contracts and bounded workers."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.techniques.calibration_budget import CalibrationBudget
from modules.techniques.review_calibration import (
    CalibrationCase,
    code_identity,
    main,
    preflight,
    run_comparison,
    schedule,
    summarize,
    verify_transports,
)


@pytest.fixture
def cases(markdown):
    return [CalibrationCase(id=family, family=family, request="Review the declared access comparison",
                            draft=markdown(), requirements=[{"id": "method", "description": "Compare access",
                                                             "kind": "authored_deliverable"}],
                            sources=[{"url": "https://vendor.example/advisory", "title": "Advisory",
                                      "text": "Source technical facts"}])
            for family in ("http", "network", "filesystem")]


@pytest.fixture
def ready(cases):
    result = []
    for index, case in enumerate(cases):
        payload = case.model_dump()
        payload.update(adjudication="needs_correction" if index == 0 else "acceptable",
                       adjudication_reason="Exact fixture adjudication for protocol testing",
                       adjudicated_sha256=case.digest())
        result.append(CalibrationCase.model_validate(payload))
    return result


@pytest.fixture
def arms(tmp_path):
    roots = []
    for name in ("baseline", "candidate"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / "src" / "policy.py").write_text("POLICY = 'material'\n")
        roots.append(root)
    return roots


@pytest.mark.parametrize("defect", ["kind", "source", "duplicate_source", "duplicate_requirement", "digest", "rationale"])
def test_preflight_rejects_incomplete_or_stale_subjects(ready, defect):
    payload = copy.deepcopy(ready[0].model_dump())
    if defect == "kind":
        del payload["requirements"][0]["kind"]
    elif defect == "source":
        payload["sources"][0]["url"] = "https://different.example/source"
    elif defect == "duplicate_source":
        payload["sources"] *= 2
    elif defect == "duplicate_requirement":
        payload["requirements"] *= 2
    elif defect == "digest":
        payload["draft"] += "Changed after adjudication\n"
    else:
        payload["adjudication_reason"] = ""
    with pytest.raises(ValidationError):
        CalibrationCase.model_validate(payload)


def test_exact_adjudication_and_case_families(cases, ready):
    assert not preflight(cases)["ready_for_live"]
    assert preflight(ready)["ready_for_live"]
    with pytest.raises(ValueError, match="one HTTP"):
        preflight(cases[:2])
    copies = [case.model_copy(update={"id": "same"}) for case in cases]
    with pytest.raises(ValueError, match="unique"):
        preflight(copies)
    invalid_labels = [case.model_copy(update={"adjudication": "acceptable"}) for case in ready]
    with pytest.raises(ValueError, match="two acceptable"):
        preflight(invalid_labels)
    assert [arm for _, arm in schedule(cases)] == ["baseline", "candidate", "candidate", "baseline", "baseline", "candidate"]


def test_code_identity_tracks_runtime_configuration_but_excludes_vendor_cache(arms, tmp_path):
    root = arms[0]
    original = code_identity(root)
    for directory in ("__pycache__", "node_modules"):
        path = root / "src" / directory
        path.mkdir()
        (path / "generated.py").write_text("ignored = True")
    assert code_identity(root) == original
    (root / "src" / "profile.yaml").write_text("output: 4096")
    assert code_identity(root) != original
    with pytest.raises(ValueError, match="complete src"):
        code_identity(tmp_path / "missing")


def fake_execute(args, **kwargs):
    path = Path(args[-1])
    assert path.is_absolute()
    job = json.loads(path.read_text())
    assert Path(job["root"]).is_absolute()
    assert Path(job["budget"]).is_absolute()
    assert kwargs["env"]["PYTHONPATH"] == str(Path(kwargs["cwd"]) / "src")
    status = "offline_contract_passed" if job["offline"] else "completed"
    output = {"status": status, "review": {"approved": job["case"]["adjudication"] == "acceptable",
                                           "issues": [], "suggestions": []}}
    (Path(job["root"]) / "outcome.json").write_text(json.dumps(output))
    return SimpleNamespace(returncode=0)


def test_preflight_receipt_binds_exact_arms_inputs_and_transports(ready, arms, tmp_path):
    receipt = verify_transports(ready, *arms, tmp_path / "offline", execute=fake_execute)
    assert receipt["wire_modes"] == ["native", "tool"]
    results = run_comparison(ready, *arms, tmp_path / "live", execute=fake_execute, transport_receipt=receipt)
    assert len(results) == 6
    assert all(row["status"] == "completed" for row in results)
    assert summarize(results)["candidate"]["false_blocks"] == 0
    assert not summarize(results)["demonstrated_label_improvement"]
    stale = dict(receipt, subjects={})
    with pytest.raises(ValueError, match="Exact-input"):
        run_comparison(ready, *arms, tmp_path / "bad", execute=fake_execute, transport_receipt=stale)


def test_pending_adjudication_cannot_dispatch(cases, arms, tmp_path):
    with pytest.raises(ValueError, match="Human adjudication"):
        run_comparison(cases, *arms, tmp_path / "never-created", execute=lambda *_a, **_k: pytest.fail("No dispatch"))
    assert not (tmp_path / "never-created").exists()


def test_budget_exhaustion_records_unattempted_cases(ready, arms, tmp_path):
    receipt = verify_transports(ready, *arms, tmp_path / "offline", execute=fake_execute)
    calls = []

    def exhaust(args, **kwargs):
        calls.append(args)
        job = json.loads(Path(args[-1]).read_text())
        budget = CalibrationBudget(job["budget"])
        for _ in range(30):
            budget.reserve("requests")
        return SimpleNamespace(returncode=1)

    results = run_comparison(ready, *arms, tmp_path / "live", execute=exhaust, transport_receipt=receipt)
    assert len(calls) == 1
    assert results[0]["status"] == "worker_failed"
    assert all(row["status"] == "unattempted_budget_exhausted" for row in results[1:])


def test_failed_offline_workers_prevent_live_receipt(cases, arms, tmp_path):
    with pytest.raises(ValueError, match="Offline transport"):
        verify_transports(cases, *arms, tmp_path / "offline", execute=lambda *_a, **_k: SimpleNamespace(returncode=1))
    assert not (tmp_path / "offline" / "preflight.json").exists()


def test_arm_mutation_is_not_silently_compared(cases, arms, tmp_path):
    calls = []

    def mutate(args, **kwargs):
        calls.append(args)
        result = fake_execute(args, **kwargs)
        (arms[1] / "src" / "policy.py").write_text("changed = True")
        return result

    with pytest.raises(ValueError, match="Frozen arm"):
        run_comparison(cases, *arms, tmp_path / "offline", execute=mutate, offline=True)
    assert len(calls) == 1


def test_summary_does_not_turn_incompleteness_into_improvement():
    rows = []
    for arm in ("baseline", "candidate"):
        for index, expected in enumerate((True, True, False)):
            rows.append({"arm": arm, "status": "completed", "expected_approved": expected,
                         "review": {"approved": expected if arm == "candidate" else index == 2}})
    metrics = summarize(rows)
    assert metrics["demonstrated_label_improvement"]
    assert metrics["baseline"]["missed_defective_cases"] == 1
    rows[-1]["status"] = "unresolved"
    assert not summarize(rows)["demonstrated_label_improvement"]


def test_cli_preflight_and_argument_errors(cases, tmp_path, capsys):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([case.model_dump() for case in cases]))
    assert main([str(path)]) == 0
    assert '"ready_for_live": false' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([str(path), "--run"])
    with pytest.raises(SystemExit):
        main([str(path), "--run", "--verify-transports", "--baseline-root", str(tmp_path)])


def test_explicit_kind_required_for_requirement_instances(cases):
    from modules.techniques.coverage import Requirement

    payload = cases[0].model_dump()
    payload["requirements"] = [Requirement(id="method", description="Compare access")]
    with pytest.raises(ValidationError, match="declare their kinds"):
        CalibrationCase.model_validate(payload)


@pytest.mark.parametrize("live", [False, True])
def test_cli_executes_only_after_preflight(ready, tmp_path, monkeypatch, live):
    from modules.techniques import review_calibration as calibration

    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([case.model_dump() for case in ready]))
    calls = []
    receipt = {"status": "passed"}
    monkeypatch.setattr(calibration, "verify_transports", lambda *args: calls.append("offline") or receipt)
    monkeypatch.setattr(calibration, "run_comparison", lambda *args, **kwargs: calls.append(kwargs["transport_receipt"]))
    output = tmp_path / "trial"
    assert main([str(path), "--baseline-root", str(tmp_path), "--candidate-root", str(tmp_path),
                 "--output-dir", str(output), "--run" if live else "--verify-transports"]) == 0
    assert calls == (["offline", receipt] if live else ["offline"])


def test_cli_pending_labels_stop_before_transport(cases, tmp_path, monkeypatch):
    from modules.techniques import review_calibration as calibration

    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([case.model_dump() for case in cases]))
    monkeypatch.setattr(calibration, "verify_transports", lambda *args: pytest.fail("No preflight dispatch"))
    with pytest.raises(SystemExit):
        main([str(path), "--baseline-root", str(tmp_path), "--run"])


def test_current_arm_mutation_is_detected_after_worker(cases, arms, tmp_path):
    def mutate(args, **kwargs):
        result = fake_execute(args, **kwargs)
        job = json.loads(Path(args[-1]).read_text())
        selected = arms[0] if job["arm"] == "baseline" else arms[1]
        (selected / "src" / "policy.py").write_text("changed = True")
        return result

    with pytest.raises(ValueError, match="Frozen arm"):
        run_comparison(cases, *arms, tmp_path / "offline", execute=mutate, offline=True)
