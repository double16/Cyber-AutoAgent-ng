"""Paired isolated production feedback replay with reconstructed publication outcomes."""

import json
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.techniques import feedback
from modules.techniques.feedback_activity import FeedbackTaskContext
from modules.techniques.roles import TechniqueRoleRunner, structured_system_prompt
from modules.techniques.service import TechniqueService

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/publication-feedback-20261121/live")
COUNTER = {"requests": 0, "limit": 120}
ORIGINAL = ollama.AsyncClient.chat
TRANSPORT = []


async def counted(client, *args, **kwargs):
    if COUNTER["requests"] >= COUNTER["limit"]:
        raise RuntimeError("Bounded replay allowance exhausted")
    COUNTER["requests"] += 1
    TRANSPORT.append({"native_schema": isinstance(kwargs.get("format"), dict)})
    (ROOT / "requests.json").write_text(json.dumps(COUNTER))
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    counter_path = ROOT / "requests.json"
    if counter_path.exists():
        COUNTER["requests"] = json.loads(counter_path.read_text())["requests"]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    baseline = types.ModuleType("modules.techniques.feedback_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(subprocess.check_output([
        "git", "show", f"{revision}:src/modules/techniques/feedback.py",
    ], text=True), "baseline-feedback.py", "exec"), baseline.__dict__)
    live = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    activity = FeedbackTaskContext(profile="hypothesis_generation", provenance="frozen_fixture")
    for repetition in range(3):
        for case in ("dom", "admin", "empty"):
            fixture = json.loads((BASE / "fixtures" / f"{case}.json").read_text())
            for variant, module in (("baseline", baseline), ("revised", feedback)):
                destination = ROOT / f"{case}-{variant}-{repetition}.json"
                if destination.exists():
                    continue
                calls, events = [], []

                def invoke(role, prompt, schema, tools):
                    row = {"role": role, "input": str(prompt), "schema": schema.model_json_schema(),
                           "system": structured_system_prompt(schema)}
                    calls.append(row)
                    output = live.structured(role, prompt, schema, tools)
                    row["output"] = output.model_dump()
                    return output

                service = SimpleNamespace(
                    _json=invoke, events=SimpleNamespace(emit=lambda event, **kw: events.append({"type": event, **kw})),
                    _author_feedback=lambda error, category: str(error),
                    _repair_prompt=lambda prompt, error: TechniqueService._repair_prompt(service, prompt, error),
                )
                outcomes = []
                for value in (fixture["previous"], fixture["current"]):
                    if value is not None:
                        outcomes.append({"tool": "record_hypotheses", "success": True, "output": value})
                context = {"status": "done", "outcomes": outcomes}
                if variant == "revised":
                    context = {"current_publication": {"status": "available", "value": fixture["current"],
                               "history_policy": "Earlier publications are superseded history."}, **context}
                use = {"revision_id": "isolated", "use_context": json.dumps({
                    "delivered_documents": [fixture["guidance"]],
                })}
                before, transport_start, start = COUNTER["requests"], len(TRANSPORT), time.monotonic()
                record = {"case": case, "variant": variant, "repetition": repetition, "baseline": revision}
                try:
                    result = module.evaluate_feedback(
                        service, use, json.dumps(context, indent=2), [], task_context=activity,
                    )
                    record.update(valid=True, result=result.model_dump(), score=result.score)
                except Exception as error:
                    record.update(valid=False, error=str(error))
                record.update(calls=calls, events=events, requests=COUNTER["requests"] - before,
                              transport=TRANSPORT[transport_start:], elapsed_seconds=time.monotonic() - start)
                destination.write_text(json.dumps(record, indent=2))
                print(destination.name, record["valid"], record["requests"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
