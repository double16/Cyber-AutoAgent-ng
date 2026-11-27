"""Fresh balanced smoke checks of the clarified production contract; no causal comparison."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.agents import hypothesis_context
from modules.agents.hypothesis_review import HYPOTHESIS_SEMANTIC_GUIDANCE
from modules.agents.structured_outputs import HypothesisSemanticChecks
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import HypothesisSet

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/identity-presentation-20261122")
ORIGINAL = ollama.AsyncClient.chat
TRANSPORT = []


async def counted(client, *args, **kwargs):
    counter = json.loads((ROOT / "requests.json").read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Shared request allowance exhausted")
    counter["requests"] += 1
    (ROOT / "requests.json").write_text(json.dumps(counter))
    TRANSPORT.append({"native_schema": isinstance(kwargs.get("format"), dict)})
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    seed = json.loads((BASE / "cases.json").read_text())[0]["publication"]
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    definitions = [
        ("http", "auditor", "HTTP export handler", "Request the protected marker resource"),
        ("network", "operator", "network diagnostic service", "Read the protected diagnostic marker"),
        ("filesystem", "project_reader", "source filesystem export function with an optional principal",
         "Read the protected export marker with the principal argument"),
    ]
    for kind, role, subject, action in definitions:
        for matching in (True, False):
            name = f"contract_smoke-{kind}-{'valid' if matching else 'missing'}"
            destination = ROOT / (name + ".json")
            if destination.exists():
                continue
            payload = json.loads(json.dumps(seed))
            h = payload["subjects"][0]["hypotheses"][0]
            h.update(
                hypothesis_id=name,
                authentication_requirement={"comparison": "none", "roles": [role] if matching else []},
                observed_basis=f"Source for the {subject} accepts an optional identity context. "
                               f"Recorded policy declares the {role} role and restricts the marker to that role.",
                preconditions=[f"A future {role} context must be established; readiness is unknown."],
                suspected_mechanism="The resource may be accessible without the policy-required identity.",
                attacker_controlled_input="The optional identity context for the assigned marker operation",
                safe_test_steps=[
                    f"{action} in one future authenticated {role} context as the permitted baseline.",
                    f"{action} with no authenticated context as the explicitly assigned anonymous control.",
                    ("Compare the same marker and operation across those two contexts; "
                     "no second authenticated account, role, or tenant is involved."),
                ],
                positive_expectations=["The anonymous control receives the policy-protected marker."],
                negative_expectations=["The authenticated baseline receives the marker and anonymous access is denied."],
                required_evidence=[f"Evidence of the {role} baseline context, the policy, and both marker outcomes."],
                supporting_refs=["artifact:frozen-policy.txt"], research_refs=[],
            )
            publication = HypothesisSet.model_validate(payload)
            binding = {"artifact_ref": "artifact:frozen-current.json", "sha256": hashlib.sha256(
                publication.model_dump_json().encode(),
            ).hexdigest()}
            with patch.object(hypothesis_context, "hypothesis_binding", return_value=binding), patch.object(
                hypothesis_context, "load_hypothesis_set", return_value=publication,
            ):
                data = hypothesis_context.publication_review(SimpleNamespace(), "OP_FIXTURE")
            instructions = (
                hypothesis_context.HYPOTHESIS_GROUNDING_GUIDANCE + HYPOTHESIS_SEMANTIC_GUIDANCE
                + "\nAssess this one frozen hypothesis. Source-policy statements are fixture premises, "
                  "not independently revalidated evidence. Do not execute tests."
            )
            before = json.loads((ROOT / "requests.json").read_text())["requests"]
            start = len(TRANSPORT)
            record = {"case": name, "expected_identity": "adequate" if matching else "revise",
                      "instructions": instructions, "input": data,
                      "schema": HypothesisSemanticChecks.model_json_schema()}
            try:
                result = runner.structured(
                    "task_evaluator", RolePrompt(instructions, data), HypothesisSemanticChecks, [],
                )
                record.update(valid=True, output=result.model_dump())
            except Exception as error:
                record.update(valid=False, error=str(error))
            record.update(requests=json.loads((ROOT / "requests.json").read_text())["requests"] - before,
                          transport=TRANSPORT[start:])
            destination.write_text(json.dumps(record, indent=2))
            print(name, record["valid"], record["requests"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
