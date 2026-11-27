"""No provider execution before exact-input preflight and explicit authorization."""

import hashlib
import json

import pytest

from modules.techniques.generation_pilot_coordinator import identity, preflight, run
from modules.techniques.review_calibration import code_identity


@pytest.fixture
def frozen(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/worker.py").write_text("frozen = True\n")
    cases = []
    for family in ("http", "network", "filesystem"):
        case = {"id": family, "request": "Describe a controlled method"}
        case["input_sha256"] = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
        cases.append(case)
    protocol = {"candidate_code_identity": code_identity(repo), "cases": [
        {"id": case["id"], "input_sha256": case["input_sha256"]} for case in cases],
        "provider": "scripted", "model": "scripted", "status": "prepared"}
    return repo, cases, protocol


def scripted(case, root, mode, budget):
    return {"status": "offline_contract_passed", "semantic_judgment": "not_performed",
            "scripted_model_dispatches": 4}


def test_preflight_binds_all_six_inputs_and_live_gate(frozen, tmp_path):
    repo, cases, protocol = frozen
    receipt = preflight(cases, protocol, tmp_path / "offline", scripted, repository=repo)
    with pytest.raises(ValueError, match="allowance"):
        run(cases, protocol, receipt, tmp_path / "never", None, repository=repo)
    assert not (tmp_path / "never").exists()
    protocol.update(status="authorized", authorized_provider_request_cap=2)

    def worker(case, root, budget):
        budget.reserve("requests")
        return {"status": "unresolved"}

    results = run(cases, protocol, receipt, tmp_path / "live", worker, repository=repo)
    assert len(results) == 3
    assert results[-1]["status"] == "unattempted_budget_exhausted"


@pytest.mark.parametrize("defect", ["runtime", "input", "manifest", "answers"])
def test_identity_rejects_drift(frozen, defect):
    repo, cases, protocol = frozen
    if defect == "runtime":
        (repo / "src/worker.py").write_text("changed = True")
    elif defect == "manifest":
        protocol["cases"] = []
    else:
        cases[0]["draft" if defect == "answers" else "request"] = "changed"
    with pytest.raises(ValueError):
        identity(cases, protocol, repo)


def test_failed_preflight_never_emits_receipt(frozen, tmp_path):
    repo, cases, protocol = frozen
    with pytest.raises(ValueError, match="preflight failed"):
        preflight(cases, protocol, tmp_path / "failed", lambda *args: {"status": "unresolved"}, repository=repo)
    assert not (tmp_path / "failed/receipt.json").exists()


@pytest.mark.parametrize("cap", [None, True, 0, 31])
def test_invalid_authorized_cap_cannot_dispatch(frozen, tmp_path, cap):
    repo, cases, protocol = frozen
    protocol.update(status="authorized", authorized_provider_request_cap=cap)
    with pytest.raises(ValueError, match="cap"):
        run(cases, protocol, {}, tmp_path / "never", None, repository=repo)


def test_stale_receipt_and_mid_run_changes_fail_closed(frozen, tmp_path):
    repo, cases, protocol = frozen
    protocol.update(status="authorized", authorized_provider_request_cap=30)
    with pytest.raises(ValueError, match="preflight"):
        run(cases, protocol, {}, tmp_path / "never", None, repository=repo)
    receipt = preflight(cases, protocol, tmp_path / "offline", scripted, repository=repo)

    def mutate(case, root, budget):
        (repo / "src/worker.py").write_text("changed = True")
        return {"status": "unresolved"}

    with pytest.raises(ValueError, match="runtime"):
        run(cases, protocol, receipt, tmp_path / "live", mutate, repository=repo)
    assert (tmp_path / "live/results.json").exists()


def test_worker_errors_are_preserved_and_missing_sdk_dispatch_cannot_pass(frozen, tmp_path):
    repo, cases, protocol = frozen

    def fail(*args):
        raise OSError("worker unavailable")

    with pytest.raises(ValueError):
        preflight(cases, protocol, tmp_path / "failed", fail, repository=repo)
    assert "worker_failed" in (tmp_path / "failed/outcomes.json").read_text()
    with pytest.raises(ValueError):
        preflight(cases, protocol, tmp_path / "empty",
                  lambda *args: {"status": "offline_contract_passed"}, repository=repo)
    receipt = preflight(cases, protocol, tmp_path / "valid", scripted, repository=repo)
    protocol.update(status="authorized", authorized_provider_request_cap=30)
    outcomes = run(cases, protocol, receipt, tmp_path / "live", fail, repository=repo)
    assert len(outcomes) == 3 and all(row["status"] == "worker_failed" for row in outcomes)
