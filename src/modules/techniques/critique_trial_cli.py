"""Run opt-in frozen critique trials; results are never production acceptance."""

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, model_validator
from strands.types.exceptions import MaxTokensReachedException

from modules.techniques.alignment import AUTHORING_GUIDANCE
from modules.techniques.coverage import Requirement
from modules.techniques.critique import assess
from modules.techniques.critique_experiment import Variant
from modules.techniques.critique_trials import ExperimentRunner
from modules.techniques.diagnostics import DiagnosticSession
from modules.techniques.roles import TechniqueRoleRunner
from modules.techniques.service import TechniqueService


class FrozenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    draft: str
    requirements: list[Requirement]
    sources: list[dict]

    @model_validator(mode="before")
    @classmethod
    def require_explicit_requirement_kinds(cls, data):
        """Benchmarks cannot silently reinterpret authoring requests as factual claims."""
        if isinstance(data, dict):
            rows = data.get("requirements", [])
            for row in rows if isinstance(rows, (list, tuple)) else ():
                if ((isinstance(row, dict) and "kind" not in row)
                        or (isinstance(row, Requirement) and "kind" not in row.model_fields_set)):
                    raise ValueError("Every experimental requirement must declare its kind explicitly")
        return data


def schedule(cases, variants, repetitions, seed):
    if repetitions < 1:
        raise ValueError("Repetitions must be positive")
    jobs = [(case, variant, repeat) for repeat in range(repetitions)
            for case in cases for variant in variants]
    random.Random(seed).shuffle(jobs)
    return jobs


def run_trial(case, variant, runner, directory, events_callback=None):
    directory.mkdir()
    adapter = ExperimentRunner(runner, variant, case.draft, case.sources)
    service = TechniqueService(SimpleNamespace(repository=None), adapter, None)
    service.events.callback = events_callback
    outcome = {"case": case.id, "variant": variant.value,
               "draft_sha256": hashlib.sha256(case.draft.encode()).hexdigest(),
               "human_adjudication": "pending"}
    try:
        result = assess(service, AUTHORING_GUIDANCE + "\n"
            + json.dumps({"requirements": [r.model_dump() for r in case.requirements]}),
            case.draft, case.requirements, case.sources, [s["url"] for s in case.sources])
        outcome.update(status="completed", review=result.model_dump())
    except (ValueError, RuntimeError, MaxTokensReachedException) as error:
        outcome.update(status="unresolved", error=str(error), error_type=type(error).__name__)
    except Exception as error:
        outcome.update(status="interrupted", error=str(error), error_type=type(error).__name__)
        raise
    finally:
        # Exact internal evidence is sensitive. This directory must not be exported as a report.
        (directory / "model-evidence.json").write_text(json.dumps(adapter.records, indent=2))
        (directory / "outcome.json").write_text(json.dumps(outcome, indent=2))
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--model", default="muse-glimmer:30b")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--variants", nargs="+", choices=[v.value for v in Variant], default=[v.value for v in Variant])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raw = args.corpus.read_bytes()
    cases = [FrozenCase.model_validate(row) for row in json.loads(raw)]
    if len({case.id for case in cases}) != len(cases):
        parser.error("Case IDs must be unique")
    jobs = schedule(cases, [Variant(v) for v in args.variants], args.repetitions, args.seed)
    if args.dry_run:
        print(json.dumps({"trials": len(jobs), "corpus_sha256": hashlib.sha256(raw).hexdigest()}))
        return
    root = Path(tempfile.mkdtemp(prefix="critique-comparison-"))
    (root / "corpus.json").write_bytes(raw)
    (root / "configuration.json").write_text(json.dumps(vars(args), default=str, indent=2))
    print(root, flush=True)
    for index, (case, variant, repeat) in enumerate(jobs):
        directory = root / f"trial-{index:03d}"
        diagnostics = DiagnosticSession(verbose=True)
        diagnostics.open(str(root / f"diagnostics-{index:03d}"))
        try:
            runner = TechniqueRoleRunner(args.provider, args.model, diagnostics=diagnostics)
            outcome = run_trial(case, variant, runner, directory, diagnostics.lifecycle)
            outcome["repeat"] = repeat
            (directory / "outcome.json").write_text(json.dumps(outcome, indent=2))
            print(index, case.id, variant.value, outcome["status"], flush=True)
        finally:
            diagnostics.close()


if __name__ == "__main__":
    main()
