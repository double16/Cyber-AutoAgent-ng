"""Focused native semantic-check replay; no acceptance or target execution."""

import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.agents import hypothesis_context
from modules.agents.hypothesis_review import HYPOTHESIS_SEMANTIC_GUIDANCE
from modules.agents.structured_outputs import HypothesisSemanticChecks
from modules.prompts.security_interpretation import SECURITY_INTERPRETATION
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import HypothesisSet

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/publication-feedback-20261121/live")
ORIGINAL = ollama.AsyncClient.chat
TRANSPORT = []


async def counted(client, *args, **kwargs):
    counter = json.loads((ROOT / "requests.json").read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Combined replay allowance exhausted")
    counter["requests"] += 1
    (ROOT / "requests.json").write_text(json.dumps(counter))
    TRANSPORT.append({"native_schema": isinstance(kwargs.get("format"), dict)})
    return await ORIGINAL(client, *args, **kwargs)


def historical_module(name, path):
    module = types.ModuleType(name)
    sys.modules[name] = module
    source = subprocess.check_output(["git", "show", f"HEAD:{path}"], text=True)
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def main():
    load_dotenv()
    old_security = historical_module("old_security", "src/modules/prompts/security_interpretation.py")
    old_context = historical_module("old_context", "src/modules/agents/hypothesis_context.py")
    old_guidance = HYPOTHESIS_SEMANTIC_GUIDANCE.replace(
        SECURITY_INTERPRETATION, old_security.SECURITY_INTERPRETATION, 1,
    )
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    for repetition in range(3):
        for case in ("dom", "admin"):
            fixture = json.loads((BASE / "fixtures" / f"{case}.json").read_text())
            publication = HypothesisSet.model_validate(fixture["current"])
            selected = publication.subjects[0].hypotheses[0].hypothesis_id
            digest = hashlib.sha256(json.dumps(fixture["current"]).encode()).hexdigest()
            for variant, module, guidance in (
                ("baseline", old_context, old_guidance),
                ("revised", hypothesis_context, HYPOTHESIS_SEMANTIC_GUIDANCE),
            ):
                destination = ROOT / f"semantic-{case}-{variant}-{repetition}.json"
                if destination.exists():
                    continue
                with patch.object(module, "hypothesis_binding", return_value={
                    "artifact_ref": "artifact:frozen-current.json", "sha256": digest,
                }), patch.object(module, "load_hypothesis_set", return_value=publication):
                    data = module.publication_review(SimpleNamespace(), "OP_FIXTURE")
                instructions = guidance + (
                    "\nAssess only the current hypothesis with ID " + selected + ". Return its semantic_checks. "
                    "Use the supplied publication as the frozen task deliverable. Underlying source inspections "
                    "are unavailable in this isolated replay; do not claim to have revalidated them. "
                    "Do not execute any future test."
                )
                before = json.loads((ROOT / "requests.json").read_text())["requests"]
                transport_start = len(TRANSPORT)
                record = {"case": case, "variant": variant, "repetition": repetition,
                          "instructions": instructions, "input": data,
                          "schema": HypothesisSemanticChecks.model_json_schema()}
                try:
                    result = runner.structured(
                        "task_evaluator", RolePrompt(instructions, data), HypothesisSemanticChecks, [],
                    )
                    record.update(valid=True, output=result.model_dump())
                except Exception as error:
                    record.update(valid=False, error=str(error))
                after = json.loads((ROOT / "requests.json").read_text())["requests"]
                record.update(requests=after - before, transport=TRANSPORT[transport_start:])
                destination.write_text(json.dumps(record, indent=2))
                print(destination.name, record["valid"], record["requests"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
