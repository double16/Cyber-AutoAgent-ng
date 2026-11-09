"""Run only scripted exact-input checks; this entry point cannot dispatch live inference."""

import json
import os
import subprocess
import sys
from pathlib import Path

from modules.techniques.review_calibration import CalibrationCase, code_identity

research = Path(__file__).resolve().parent
repo = research.parents[2]
protocol = json.loads((research / "protocol.json").read_text())
cases = [CalibrationCase.model_validate(row) for row in json.loads((research / "cases.json").read_text())]
assert {case.id: case.digest() for case in cases} == {
    row["id"]: row["sha256"] for row in protocol["subjects"]
}
assert code_identity(repo) == protocol["candidate_code_identity"]
trial = repo / "outputs/technique_review_trials/single-review-preflight-20261106-1"
results = []
for mode in ("native", "tool"):
    for index, case in enumerate(cases):
        root = trial / mode / f"{index:02d}-{case.id}"
        root.mkdir(parents=True)
        job = {
            "case": case.model_dump(), "arm": "candidate", "review_mode": "single",
            "root": str(root), "budget": str(trial / f"{mode}-budget.json"),
            "model": "scripted-single-review", "offline": True, "wire_mode": mode,
            "budget_module": str(repo / "src/modules/techniques/calibration_budget.py"),
            "offline_module": str(repo / "src/modules/techniques/review_calibration_offline.py"),
        }
        path = root / "job.json"
        path.write_text(json.dumps(job, indent=2))
        with (root / "worker.log").open("w") as stream:
            subprocess.run(
                [sys.executable, str(repo / "src/modules/techniques/review_calibration_worker.py"), str(path)],
                cwd=repo, env=dict(os.environ, PYTHONPATH=str(repo / "src")),
                stdout=stream, stderr=stream, check=True,
            )
        result = json.loads((root / "outcome.json").read_text())
        assert result["status"] == "offline_contract_passed"
        assert result["counts"]["requests"] == 0 and result["scripted_model_dispatches"] > 0
        results.append({"case": case.id, "wire_mode": mode, **result})
assert code_identity(repo) == protocol["candidate_code_identity"]
(research / "preflight-results.json").write_text(json.dumps({
    "runtime_identity": protocol["candidate_code_identity"], "provider_requests": 0,
    "semantic_judgment": "not_performed", "results": results,
}, indent=2) + "\n")
print("Ten scripted exact-input checks passed; no provider requests.")
