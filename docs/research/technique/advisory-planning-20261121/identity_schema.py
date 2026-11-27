"""Isolate existing identity-field descriptions with identical CSRF source and guidance."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import SubjectHypotheses

ROOT = Path.cwd() / "outputs/technique_review_trials/advisory-20261121"
COUNTER = {"requests": 0, "limit": 36}
ORIGINAL = ollama.AsyncClient.chat
TRANSPORT = []


class LegacySubject(SubjectHypotheses):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = copy.deepcopy(SubjectHypotheses.model_json_schema(*args, **kwargs))
        definitions = schema["$defs"]
        for field in ("comparison", "roles"):
            definitions["AuthenticationRequirement"]["properties"][field].pop("description", None)
        definitions["Hypothesis"]["properties"]["authentication_requirement"].pop("description", None)
        return schema


async def counted(client, *args, **kwargs):
    if COUNTER["requests"] >= COUNTER["limit"]:
        raise RuntimeError("Identity-schema replay allowance exhausted")
    COUNTER["requests"] += 1
    schema = kwargs.get("format") or {}
    TRANSPORT.append({"native_format_present": isinstance(schema, dict) and bool(schema), "format": schema})
    (ROOT / "identity-requests.json").write_text(json.dumps(COUNTER))
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    source = json.loads((ROOT / "application-csrf-advisory-0.json").read_text())
    assert LegacySubject.model_json_schema() == source["schema"]
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    jobs = [
        (source, variant, repetition, schema)
        for repetition in range(3)
        for variant, schema in (("legacy", LegacySubject), ("described", SubjectHypotheses))
    ]
    oidc = json.loads((ROOT / "application-oidc-advisory-0.json").read_text())
    jobs.append((oidc, "oidc-smoke", 0, SubjectHypotheses))
    for source, variant, repetition, schema in jobs:
        destination = ROOT / f"identity-{variant}-{repetition}.json"
        if destination.exists():
            continue
        before, transports = COUNTER["requests"], len(TRANSPORT)
        record = {
            "variant": variant,
            "repetition": repetition,
            "schema": schema.model_json_schema(),
            "input": source["input"],
            "instructions": source["instructions"],
        }
        try:
            output = runner.structured(
                "task_executor",
                RolePrompt(source["instructions"], source["input"]),
                schema,
                [],
            )
            record.update(output=output.model_dump(), valid=True)
        except Exception as error:
            record.update(valid=False, error=str(error))
        record.update(requests=COUNTER["requests"] - before, transport=TRANSPORT[transports:])
        destination.write_text(json.dumps(record, indent=2))
        print(destination.name, record["valid"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
