"""Frozen paired selection study using complete production-rendered candidate inputs."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ollama
from dotenv import load_dotenv
from selection_review_snapshot import selection_output_model

from modules.techniques.context import MethodologyContext
from modules.techniques.documents import TechniqueDocument
from modules.techniques.roles import TechniqueRoleRunner
from modules.techniques.service import TechniqueService

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/task-relevance-20261123")
ORIGINAL = ollama.AsyncClient.chat
DEFINITIONS = [
    ("open-redirects", "Open redirect destination control", "Design a browser navigation destination-boundary test"),
    ("ssrf", "Server-side request destination control", "Design a server-side outbound destination-boundary test"),
    ("jwt-verification", "JWT verification", "Design a JWT signature verification test"),
    ("csrf", "Cross-site request forgery", "Design a cross-origin ambient-authority protected-action test"),
    ("path-traversal-file-inclusion", "Path traversal", "Design a file-read path-boundary test"),
    ("mass-assignment", "Mass assignment", "Design a protected-property write authorization test"),
]


def freeze():
    rows = []
    for index, (name, request, objective) in enumerate(DEFINITIONS):
        document = TechniqueDocument(Path(
            f"src/modules/operation_plugins/web/techniques/{name}.md",
        ).read_text())
        for matching in (True, False):
            purpose = objective if matching else DEFINITIONS[(index + 1) % len(DEFINITIONS)][2]
            local = {"purpose": purpose, "subjects": [{"id": "assigned", "kind": "interaction"}],
                     "observations": [], "constraints": ["Design future checks only"]}
            for arm in ("baseline", "candidate"):
                def capture(role, prompt, schema):
                    rows.append({"case": f"{name}-{'valid' if matching else 'mismatch'}", "arm": arm,
                                 "expected": matching, "prompt": str(prompt), "schema": schema.model_json_schema(),
                                 "requirements": [{"id": "request", "description": request}],
                                 "sha256": hashlib.sha256(str(prompt).encode()).hexdigest()})
                    value = {"reason": "Capture only", "judgments": [{
                        "requirement_id": "request", "verdict": "covered",
                        "passages": [{"passage_id": 1}], "explanation": "Capture only",
                    }]}
                    if arm == "candidate":
                        value["task_relevance"] = {"serves_task": True, "reason": "Capture only"}
                    return schema.model_validate(value)

                service = SimpleNamespace(
                    _candidates=lambda *_args: [{"revision_id": "frozen-revision"}],
                    store=SimpleNamespace(document=lambda _revision: document),
                    events=SimpleNamespace(emit=Mock(), current_context=dict),
                    _json=capture, exports=None, _selection_reviews={}, module_technique_policy=[],
                )
                TechniqueService._select(
                    service, request, MethodologyContext(phase_contexts=["hypothesis_generation"]),
                    task_context=local if arm == "candidate" else None,
                )
    (BASE / "selection-inputs.json").write_text(json.dumps(rows, indent=2))


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Shared 128-request study allowance exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    if not isinstance(kwargs.get("format"), dict):
        raise RuntimeError("Native structured output is required")
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    counter = ROOT / "requests.json"
    if not counter.exists():
        counter.write_text(json.dumps({"requests": 0, "limit": 128}))
    if not (BASE / "selection-inputs.json").exists():
        freeze()
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    for repetition in range(2):
        for row in json.loads((BASE / "selection-inputs.json").read_text()):
            destination = ROOT / f"selection-{row['case']}-{row['arm']}-{repetition}.json"
            if destination.exists():
                continue
            schema = selection_output_model(row["requirements"], task_local=row["arm"] == "candidate")
            assert schema.model_json_schema() == row["schema"]
            result = {"case": row["case"], "arm": row["arm"], "repetition": repetition,
                      "expected": row["expected"], "input_sha256": row["sha256"]}
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
