"""Separate presentation interventions with fixed complete inputs and a shared cap."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import ollama
import review_trials
from dotenv import load_dotenv
from selection_review_snapshot import selection_output_model
from selection_trials import BASE, ORIGINAL, ROOT

from modules.agents import hypothesis_context
from modules.authentication_requirements import AuthenticationRequirement
from modules.techniques.documents import json_payload
from modules.techniques.roles import TechniqueRoleRunner
from modules.tools.hypotheses import HypothesisSet

METHODOLOGY_DELIVERABLE = "reusable methodology"
DESTINATION = ROOT / "contract-view"


def freeze():
    for filename in ("review-inputs-v3.json", "selection-inputs-v3.json"):
        if (BASE / filename).exists():
            raise RuntimeError("Contract-view inputs are already frozen")
    reviews = []
    for row in json.loads((BASE / "review-inputs-v2.json").read_text()):
        if row["arm"] != "candidate":
            continue
        publication = HypothesisSet.model_validate(row["publication"])
        with patch.object(hypothesis_context, "hypothesis_binding", return_value=row["binding"]), patch.object(
            hypothesis_context, "load_hypothesis_set", return_value=publication,
        ):
            after = hypothesis_context.publication_review(SimpleNamespace(), publication.operation_id)
            with patch.object(AuthenticationRequirement, "review_view", AuthenticationRequirement.model_dump):
                before = hypothesis_context.publication_review(SimpleNamespace(), publication.operation_id)
        assert row["prompt"].count(before) == 1
        for arm, prompt in (("baseline", row["prompt"]), ("candidate", row["prompt"].replace(before, after))):
            reviews.append({**row, "arm": arm, "prompt": prompt,
                            "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
    (BASE / "review-inputs-v3.json").write_text(json.dumps(reviews, indent=2))
    selections = []
    for row in json.loads((BASE / "selection-inputs.json").read_text()):
        if row["arm"] != "candidate":
            continue
        request = row["requirements"][0]["description"]
        anchor = f"Request: {request}\n"
        assert row["prompt"].count(anchor) == 1
        after = row["prompt"].replace(anchor, json_payload("requested_deliverable", METHODOLOGY_DELIVERABLE) + "\n" + anchor)
        for arm, prompt in (("baseline", row["prompt"]), ("candidate", after)):
            selections.append({**row, "arm": arm, "prompt": prompt,
                               "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
    (BASE / "selection-inputs-v3.json").write_text(json.dumps(selections, indent=2))


async def counted(client, *args, **kwargs):
    path = DESTINATION / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Contract-view study's 128-request cap exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    if not isinstance(kwargs.get("format"), dict):
        raise RuntimeError("Native structured output required")
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    counter = DESTINATION / "requests.json"
    if not counter.exists():
        counter.write_text(json.dumps({"requests": 0, "limit": 128}))
    review_trials.main("review-inputs-v3.json", DESTINATION, counted)
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    for repetition in range(2):
        for row in json.loads((BASE / "selection-inputs-v3.json").read_text()):
            destination = DESTINATION / f"selection-{row['case']}-{row['arm']}-{repetition}.json"
            if destination.exists():
                continue
            schema = selection_output_model(row["requirements"], task_local=True)
            assert schema.model_json_schema() == row["schema"]
            result = {key: row[key] for key in ("case", "arm", "expected")}
            result.update(repetition=repetition, input_sha256=row["sha256"])
            try:
                with patch.object(ollama.AsyncClient, "chat", counted):
                    answer = runner.structured("technique_judge", row["prompt"], schema, [])
                result.update(valid=True, output=answer.model_dump(), selected=answer.reusable)
            except Exception as error:
                result.update(valid=False, error=str(error))
            destination.write_text(json.dumps(result, indent=2))
            print(destination.name, result["valid"], flush=True)


if __name__ == "__main__":
    main()
