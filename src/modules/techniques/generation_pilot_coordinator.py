"""Exact-input preflight and explicit live authorization for isolated generation trials."""

import hashlib
import json
from pathlib import Path

from modules.techniques.calibration_budget import CalibrationBudget
from modules.techniques.review_calibration import code_identity


def identity(cases, protocol, repository):
    runtime = code_identity(repository)
    if runtime != protocol["candidate_code_identity"]:
        raise ValueError("Frozen generation runtime changed")
    subjects = []
    for case in cases:
        payload = {key: value for key, value in case.items() if key != "input_sha256"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != case["input_sha256"] or "draft" in case or "adjudication" in case:
            raise ValueError("Generation input changed or contains reference answers")
        subjects.append({"id": case["id"], "input_sha256": digest})
    if not subjects or len({row["id"] for row in subjects}) != len(subjects) or subjects != protocol["cases"]:
        raise ValueError("Frozen generation case manifest changed")
    return {"runtime": runtime, "cases": subjects, "provider": protocol["provider"], "model": protocol["model"]}


def preflight(cases, protocol, directory, worker, *, repository=Path.cwd()):
    frozen = identity(cases, protocol, repository)
    root = Path(directory)
    root.mkdir(mode=0o700)
    outcomes = []
    for mode in ("native", "tool"):
        for index, case in enumerate(cases):
            budget = CalibrationBudget(root / f"{mode}-{index:02d}-budget.json")
            try:
                result = worker(case, root / f"{mode}-{index:02d}", mode, budget)
            except Exception as error:
                result = {"status": "worker_failed", "error_type": type(error).__name__, "error": str(error)}
            outcomes.append({"case": case["id"], "mode": mode, **result})
            (root / "outcomes.json").write_text(json.dumps(outcomes, indent=2))
            dispatches = result.get("scripted_model_dispatches")
            if (result["status"] != "offline_contract_passed" or type(dispatches) is not int or dispatches < 1
                    or budget.counts()["requests"]
                    or identity(cases, protocol, repository) != frozen):
                raise ValueError("Generation SDK preflight failed; live dispatch prohibited")
    receipt = {"status": "passed", **frozen, "modes": ["native", "tool"]}
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def run(cases, protocol, receipt, directory, worker, *, repository=Path.cwd()):
    frozen = identity(cases, protocol, repository)
    if protocol.get("status") != "authorized":
        raise ValueError("A new explicit generation allowance is required")
    limit = protocol.get("authorized_provider_request_cap")
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Authorized generation cap must be an integer from 1 to 30")
    if receipt != {"status": "passed", **frozen, "modes": ["native", "tool"]}:
        raise ValueError("Matching exact-input native/tool preflight is required")
    root = Path(directory)
    root.mkdir(mode=0o700)
    budget = CalibrationBudget(root / "budget.json", limit)
    outcomes = []
    for index, case in enumerate(cases):
        if identity(cases, protocol, repository) != frozen:
            raise ValueError("Frozen generation runtime or inputs changed")
        if budget.counts()["requests"] >= limit:
            result = {"status": "unattempted_budget_exhausted"}
        else:
            try:
                result = worker(case, root / f"{index:02d}", budget)
            except Exception as error:
                result = {"status": "worker_failed", "error_type": type(error).__name__, "error": str(error)}
        outcomes.append({"case": case["id"], **result})
        (root / "results.json").write_text(json.dumps({"counts": budget.counts(), "outcomes": outcomes}, indent=2))
        if identity(cases, protocol, repository) != frozen:
            raise ValueError("Frozen generation runtime or inputs changed")
    return outcomes
