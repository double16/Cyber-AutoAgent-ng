"""Fixed paired identity-presentation study; no production persistence or target execution."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import baseline as baseline_renderer
import ollama
from candidate import render
from dotenv import load_dotenv

from modules.agents import hypothesis_review
from modules.agents.hypothesis_review import BoundHypothesisReview
from modules.agents.structured_outputs import HypothesisSemanticChecks
from modules.techniques import roles
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import HypothesisSet

BASE = Path(__file__).parent
GUIDANCE = json.loads((BASE / "contracts.json").read_text())["semantic_guidance"]
ROOT = Path("outputs/technique_review_trials/identity-presentation-20261122")
COUNTER = {"requests": 0, "limit": 45}
ORIGINAL = ollama.AsyncClient.chat
TRANSPORT = []


async def counted(client, *args, **kwargs):
    if COUNTER["requests"] >= COUNTER["limit"]:
        raise RuntimeError("Identity presentation allowance exhausted")
    COUNTER["requests"] += 1
    TRANSPORT.append({"native_schema": isinstance(kwargs.get("format"), dict)})
    (ROOT / "requests.json").write_text(json.dumps(COUNTER))
    return await ORIGINAL(client, *args, **kwargs)


def binding(publication):
    return {"artifact_ref": "artifact:frozen-current.json", "sha256": hashlib.sha256(
        publication.model_dump_json().encode(),
    ).hexdigest()}


def views(publication):
    current = binding(publication)
    with patch.object(baseline_renderer, "hypothesis_binding", return_value=current), patch.object(
        baseline_renderer, "load_hypothesis_set", return_value=publication,
    ):
        baseline = baseline_renderer.publication_review(SimpleNamespace(), "OP_FIXTURE")
    return baseline, render(publication, current)


def bound_schema(publication):
    task = SimpleNamespace(task_uid="frozen-task", acceptance=SimpleNamespace(criteria=[
        SimpleNamespace(id="assess-the-assigned-endpoint"),
    ]))
    journal = SimpleNamespace(inspection_availability=dict)
    with patch.object(hypothesis_review, "hypothesis_binding", return_value=binding(publication)), patch.object(
        hypothesis_review, "load_hypothesis_set", return_value=publication,
    ), patch.object(hypothesis_review, "hypothesis_inspection_journal", return_value=journal), patch.object(
        hypothesis_review.memory, "_operation_output_root", return_value=str(ROOT),
    ):
        return BoundHypothesisReview.capture(task, "OP_FIXTURE").output_model


def trial(runner, name, variant, repetition, instructions, data, schema, system=None):
    destination = ROOT / f"{name}-{variant}-{repetition}.json"
    if destination.exists():
        return
    before, start = COUNTER["requests"], len(TRANSPORT)
    row = {"case": name, "variant": variant, "repetition": repetition, "instructions": instructions,
           "input": data, "schema": schema.model_json_schema()}
    try:
        with patch.object(roles, "SYSTEM", system or roles.SYSTEM):
            result = runner.structured("task_evaluator", RolePrompt(instructions, data), schema, [])
        row.update(valid=True, output=result.model_dump())
        if name == "recorded_admin":
            from modules.agents.hypothesis_review import validate_subject_review

            reviewed = result.subject_reviews[0]
            try:
                validate_subject_review(reviewed, {h.hypothesis_id for h in reviewed.hypotheses}, None, "OP_FIXTURE", [])
                row["controller_contract_valid"] = True
            except ValueError as error:
                row.update(controller_contract_valid=False, controller_error=str(error))
    except Exception as error:
        row.update(valid=False, error=str(error))
    row.update(requests=COUNTER["requests"] - before, transport=TRANSPORT[start:])
    destination.write_text(json.dumps(row, indent=2))
    print(destination.name, row["valid"], row["requests"], flush=True)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    counter = ROOT / "requests.json"
    if counter.exists():
        COUNTER["requests"] = json.loads(counter.read_text())["requests"]
    cases = json.loads((BASE / "cases.json").read_text())
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    # Fixed order and repeat counts; no case is rerun until it passes.
    for repetition in range(2):
        for case in cases:
            publication = HypothesisSet.model_validate(case["publication"])
            baseline, candidate = views(publication)
            instructions = GUIDANCE + (
                "\nAssess the one frozen current hypothesis using HypothesisSemanticChecks. "
                "The provided source-policy statements are fixture premises, not independently verified evidence. "
                "Do not execute tests."
            )
            for variant, data in (("baseline", baseline), ("candidate", candidate)):
                trial(runner, case["name"], variant, repetition, instructions, data, HypothesisSemanticChecks)
    source = json.loads((BASE / "admin-review.json").read_text())
    publication = HypothesisSet.model_validate(cases[0]["publication"])
    candidate = render(publication, binding(publication))
    original = source["input"]
    start = original.index("## Authoritative current hypothesis publication")
    end = original.index("## Hypothesis semantic rubric", start)
    revised = original[:start] + candidate.lstrip("\n") + original[end:]
    schema = bound_schema(publication)
    for repetition in range(3):
        for variant, data in (("baseline", original), ("candidate", revised)):
            trial(runner, "recorded_admin", variant, repetition, "", data, schema, source["system"])


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
