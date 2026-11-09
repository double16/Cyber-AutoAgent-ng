"""Preflight and execute isolated matched reviews; never publish or update operation storage."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.techniques.coverage import Requirement
from modules.techniques.documents import TechniqueDocument


class CalibrationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    retained_scope: str = "Bounded excerpt; omitted source text is unknown"


class CalibrationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    family: str = Field(pattern=r"^(http|network|filesystem)$")
    request: str = Field(min_length=1)
    draft: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    sources: list[CalibrationSource] = Field(min_length=1)
    adjudication: str = Field(default="pending", pattern=r"^(pending|acceptable|needs_correction)$")
    adjudication_reason: str = ""
    adjudicated_sha256: str = ""

    def subject(self):
        return self.model_dump(exclude={"adjudication", "adjudication_reason", "adjudicated_sha256"})

    def digest(self):
        return hashlib.sha256(json.dumps(self.subject(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @model_validator(mode="before")
    @classmethod
    def explicit_requirement_kinds(cls, data):
        if isinstance(data, dict):
            for row in data.get("requirements", []):
                if ((isinstance(row, dict) and "kind" not in row)
                        or (isinstance(row, Requirement) and "kind" not in row.model_fields_set)):
                    raise ValueError("Calibration requirements must declare their kinds")
        return data

    @model_validator(mode="after")
    def complete_subject(self):
        document = TechniqueDocument(self.draft)
        urls = {row.url for row in self.sources}
        if len(urls) != len(self.sources):
            raise ValueError("Duplicate retained source URLs")
        if set(row.url for row in document.metadata.sources) - urls:
            raise ValueError("Every document source needs retained source text")
        if len({row.id for row in self.requirements}) != len(self.requirements):
            raise ValueError("Duplicate requirement IDs")
        if self.adjudication != "pending" and (
            not self.adjudication_reason.strip() or self.adjudicated_sha256 != self.digest()
        ):
            raise ValueError("Adjudication must bind the exact subject digest and supply a rationale")
        return self


def preflight(cases):
    if len(cases) != 3 or {case.family for case in cases} != {"http", "network", "filesystem"}:
        raise ValueError("Matched calibration requires one HTTP, network and filesystem case")
    if len({case.id for case in cases}) != 3:
        raise ValueError("Calibration case IDs must be unique")
    pending = [case.id for case in cases if case.adjudication == "pending"]
    if not pending and sorted(case.adjudication for case in cases) != [
        "acceptable", "acceptable", "needs_correction",
    ]:
        raise ValueError("Calibration requires two acceptable procedures and one materially defective procedure")
    return {"fixtures_valid": True, "ready_for_live": not pending, "pending_adjudications": pending,
            "subjects": [{"id": case.id, "sha256": case.digest()} for case in cases]}


def code_identity(root):
    root = Path(root)
    files = sorted(path for path in (root / "src").rglob("*") if path.is_file()
                   and path.suffix in {".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".j2"}
                   and not {"node_modules", "__pycache__"}.intersection(path.parts))
    if not files:
        raise ValueError("An arm requires a complete src tree")
    rows = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def schedule(cases):
    return [(case, arm) for index, case in enumerate(cases)
            for arm in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"))]


def run_comparison(cases, baseline, candidate, directory, model="muse-glimmer:30b", *, execute=subprocess.run, offline=False, wire_mode="native", transport_receipt=None):
    from modules.techniques.calibration_budget import CalibrationBudget

    status = preflight(cases)
    if not offline and not status["ready_for_live"]:
        raise ValueError("Human adjudication is pending; no model calls permitted")
    arms = {"baseline": str(Path(baseline).resolve()), "candidate": str(Path(candidate).resolve())}
    identities = {arm: code_identity(root) for arm, root in arms.items()}
    subject_hashes = {case.id: case.digest() for case in cases}
    if not offline and (transport_receipt is None or transport_receipt != {
        "status": "passed", "code_identities": identities, "subjects": subject_hashes,
        "wire_modes": ["native", "tool"],
    }):
        raise ValueError("Exact-input native and schema-tool preflight must pass before live calls")
    root = Path(directory).resolve()
    root.mkdir(mode=0o700)
    budget = CalibrationBudget(root / "budget.json")
    (root / "corpus.json").write_text(json.dumps([case.model_dump() for case in cases], indent=2))
    worker = Path(__file__).with_name("review_calibration_worker.py")
    (root / "configuration.json").write_text(json.dumps({
        "arms": arms, "code_identities": identities, "provider": "ollama", "model": model, "limit": 30,
    }, indent=2))
    outcomes = []
    for index, (case, arm) in enumerate(schedule(cases)):
        directory = root / f"{index:02d}-{arm}-{case.id}"
        directory.mkdir(mode=0o700)
        job = {"case": case.model_dump(), "arm": arm, "root": str(directory),
               "budget": str(root / "budget.json"), "model": model,
               "budget_module": str(worker.with_name("calibration_budget.py")),
               "offline_module": str(worker.with_name("review_calibration_offline.py")),
               "offline": offline, "wire_mode": wire_mode}
        path = directory / "job.json"
        path.write_text(json.dumps(job, indent=2))
        if budget.counts()["requests"] >= 30:
            result = {"status": "unattempted_budget_exhausted"}
        else:
            if code_identity(arms[arm]) != identities[arm]:
                raise ValueError("Frozen arm code changed during calibration")
            env = dict(os.environ, PYTHONPATH=str(Path(arms[arm]) / "src"))
            with (directory / "worker.log").open("w") as stream:
                process = execute([sys.executable, str(worker), str(path)], cwd=arms[arm], env=env,
                                  stdout=stream, stderr=stream, check=False)
            if code_identity(arms[arm]) != identities[arm]:
                raise ValueError("Frozen arm code changed during calibration")
            output = directory / "outcome.json"
            result = json.loads(output.read_text()) if output.exists() and process.returncode == 0 else {
                "status": "worker_failed", "returncode": process.returncode,
            }
        result.update(case=case.id, arm=arm, expected_approved=(None if case.adjudication == "pending" else case.adjudication == "acceptable"))
        (directory / "outcome.json").write_text(json.dumps(result, indent=2))
        outcomes.append(result)
    (root / "results.json").write_text(json.dumps({"counts": budget.counts(), "outcomes": outcomes,
                                                  "metrics": summarize(outcomes)}, indent=2))
    return outcomes


def verify_transports(cases, baseline, candidate, root, *, execute=subprocess.run):
    root = Path(root).resolve()
    root.mkdir(mode=0o700)
    identities = {"baseline": code_identity(baseline), "candidate": code_identity(candidate)}
    for mode in ("native", "tool"):
        outcomes = run_comparison(cases, baseline, candidate, root / mode, execute=execute,
                                  offline=True, wire_mode=mode)
        if any(row["status"] != "offline_contract_passed" for row in outcomes):
            raise ValueError("Offline transport preflight failed; live inference is prohibited")
    if identities != {"baseline": code_identity(baseline), "candidate": code_identity(candidate)}:
        raise ValueError("Arm code changed during offline preflight")
    receipt = {"status": "passed", "code_identities": identities,
               "subjects": {case.id: case.digest() for case in cases}, "wire_modes": ["native", "tool"]}
    (root / "preflight.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def summarize(outcomes):
    metrics = {}
    for arm in ("baseline", "candidate"):
        rows = [row for row in outcomes if row["arm"] == arm]
        completed = [row for row in rows if row["status"] == "completed"]
        scored = [row for row in completed if row["expected_approved"] is not None]
        metrics[arm] = {
            "completed": len(completed), "incomplete": len(rows) - len(completed),
            "false_blocks": sum(row["expected_approved"] and not row["review"]["approved"] for row in scored),
            "missed_defective_cases": sum(not row["expected_approved"] and row["review"]["approved"] for row in scored),
            "suggestions": sum(len(row["review"].get("suggestions", [])) for row in completed),
        }
    baseline, candidate = metrics["baseline"], metrics["candidate"]
    metrics["demonstrated_label_improvement"] = (
        candidate["completed"] == 3 and candidate["false_blocks"] == 0
        and candidate["missed_defective_cases"] == 0
        and candidate["false_blocks"] < baseline["false_blocks"]
        and candidate["missed_defective_cases"] <= baseline["missed_defective_cases"]
        and baseline["completed"] == 3
    )
    metrics["limitation"] = "Case-label agreement does not independently establish correct rejection reasons or useful-document yield"
    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--candidate-root", type=Path, default=Path.cwd())
    parser.add_argument("--verify-transports", action="store_true", help="Scripted native and schema-tool preflight")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run", action="store_true", help="Run only fully adjudicated, preflight-valid cases")
    args = parser.parse_args(argv)
    cases = [CalibrationCase.model_validate(row) for row in json.loads(args.corpus.read_text())]
    status = preflight(cases)
    print(json.dumps(status, indent=2))
    if not args.run and not args.verify_transports:
        return 0
    if not args.baseline_root:
        parser.error("Execution requires an immutable --baseline-root")
    if args.run and args.verify_transports:
        parser.error("Run offline preflight and live evaluation separately")
    if args.run and not status["ready_for_live"]:
        parser.error("Human adjudication is pending; no live calls permitted")
    from dotenv import load_dotenv

    load_dotenv(args.candidate_root / ".env", override=False)
    root = args.output_dir or (Path("outputs/technique_review_trials") / str(uuid.uuid4()))
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    receipt = verify_transports(cases, args.baseline_root, args.candidate_root, root / "preflight")
    if args.run:
        run_comparison(cases, args.baseline_root, args.candidate_root, root / "live", transport_receipt=receipt)
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
