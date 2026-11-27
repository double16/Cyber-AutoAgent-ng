"""Paired semantic diagnostics; native structured output, frozen inputs, no target execution."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Literal
from unittest.mock import patch

import ollama
from dotenv import load_dotenv
from pydantic import BaseModel, Field, create_model

from modules.agents import hypothesis_review
from modules.techniques import roles
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import Hypothesis, HypothesisSet
from tests.test_hypotheses import hypothesis

ROOT = Path("outputs/technique_review_trials/focused-hypothesis-20261125")
ORIGINAL = ollama.AsyncClient.chat
SOURCES = {
    "dom-flow": (
        ("Assess URL parameter title. File client.js:\n"
        "function read(){return new URL(location.href).searchParams.get('title');}\n"
        "function mount(value){const node=document.getElementById('map');\n"
        "if(node) node.outerHTML='<iframe title=\"'+value+'\"></iframe>';}\n"
        "mount(read());\nNo server-side reflection has been observed."), "propose",
    ),
    "dom-safe": (
        ("Assess URL parameter title. File client.js:\n"
        "const value=new URL(location.href).searchParams.get('title');\n"
        "document.getElementById('label').textContent=value;\n"
        "This is the complete assigned flow; no other consumer is declared."), "exclude",
    ),
    "tcp-flow": (
        ("Assess field label in a TCP request. File server.py:\n"
        "def receive(frame):\n    value=frame['label']\n    return render(value)\n"
        "def render(value):\n    return subprocess.check_output('echo '+value, shell=True)\n"
        "No request was executed. The source is retained implementation evidence."), "propose",
    ),
    "filesystem-unresolved": (
        ("Assess a filename read from an import manifest. File reader.py:\n"
        "def load(row):\n    return resolve_and_read(row['filename'])\n"
        "The linked implementation of resolve_and_read is retained in resolver.py but has not been inspected. "
        "Do not assume it implements or omits containment checks."), "inspect",
    ),
}
EDITS = {
    "authorization": (
        ("GET /records uses an account credential and a record selector. Two owned test accounts and records "
        "are available; sharing policy is unknown."),
        "Changing the record selector returns another record, proving authorization bypass",
        ("Different records or ownership do not establish prohibited access. Design a comparison against "
        "an established entitlement, with isolated identities and same-consequence controls."),
    ),
    "rendering": (
        ("POST /render passes text to a rendering service. Its implementation and any network tool use "
        "are unknown; the client call is recorded."),
        "A marker reflected in the response or a network request proves prompt injection and SSRF",
        ("Reflection does not establish instruction override, and a callback alone does not establish a "
        "prohibited server-side destination. Narrow each proposal to one consequence with future controls."),
    ),
}


class Assessment(BaseModel):
    decision: Literal["propose", "inspect", "exclude"]
    observed_flow: list[str]
    reason: str
    future_discriminating_check: str


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Frozen 64-request allowance exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    return await ORIGINAL(client, *args, **kwargs)


def historical():
    source = subprocess.check_output(
        ["git", "show", "HEAD:src/modules/agents/hypothesis_review.py"], text=True,
    )
    module = ModuleType("frozen_hypothesis_review")
    sys.modules[module.__name__] = module
    exec(compile(source, "frozen_hypothesis_review", "exec"), module.__dict__)
    return source, module


def edit_inputs(name, scope, consequence, concern):
    records = [{**hypothesis(f"{name} input {index}"), "hypothesis_id": f"record-{index}",
                "observed_basis": scope, "positive_expectations": [consequence]}
               for index in range(2)]
    publication = HypothesisSet.model_validate({
        "schema_version": "hypothesis_set_v1", "operation_id": "OP_FIXTURE", "producer_task_uid": "task",
        "snapshot_hash": "0" * 64, "source_refs": ["artifact:observation.txt"],
        "subjects": [{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": records}],
    })
    review = {"publication_sha256": "frozen", "subject_reviews": [{
        "item_id": "subject", "reason": concern,
        "hypotheses": [{"hypothesis_id": row["hypothesis_id"], "decision": "revise", "reason": concern}
                       for row in records],
    }]}
    return publication, review


def edit_schema(ids):
    replacement = create_model(
        "Replacement", hypothesis_id=(Literal[tuple(ids)], ...), replacement=(Hypothesis | None, ...),
        reason=(str, ...),
    )
    return create_model("Edits", corrections=(list[replacement], Field(min_length=len(ids), max_length=len(ids))))


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    source, before = historical()
    frozen = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
        __file__, "src/modules/agents/hypothesis_review.py", "src/modules/agents/multi_agent_workflow.py",
    )}
    frozen["baseline"] = hashlib.sha256(source.encode()).hexdigest()
    path = ROOT / "source-hashes.json"
    if path.exists() and json.loads(path.read_text()) != frozen:
        raise RuntimeError("Frozen sources changed")
    path.write_text(json.dumps(frozen, indent=2))
    counter = ROOT / "requests.json"
    if not counter.exists():
        counter.write_text(json.dumps({"requests": 0, "limit": 64}))
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    with patch.object(ollama.AsyncClient, "chat", counted), patch.object(roles, "SYSTEM", ""):
        for repetition in range(2):
            for index, name in enumerate([*SOURCES, *EDITS]):
                for arm in (("baseline", "candidate") if (index + repetition) % 2 == 0
                            else ("candidate", "baseline")):
                    destination = ROOT / f"{name}-{arm}-{repetition}.json"
                    if destination.exists():
                        continue
                    module = before if arm == "baseline" else hypothesis_review
                    row = {"case": name, "arm": arm, "repetition": repetition, "calls": []}
                    start = json.loads(counter.read_text())["requests"]
                    try:
                        if name in SOURCES:
                            data, expected = SOURCES[name]
                            instructions = (
                                "Assess whether the assigned retained source supports a conditional security test, "
                                "needs focused inspection, or supports exclusion of the assigned mechanism. "
                                "Do not execute tests.\n" + module.HYPOTHESIS_SEMANTIC_GUIDANCE
                            )
                            prompt = RolePrompt(instructions, data)
                            output = runner.structured("task_evaluator", prompt, Assessment, [])
                            row["calls"].append({"prompt": str(prompt), "output": output.model_dump()})
                            row.update(expected=expected, matched=output.decision == expected)
                        else:
                            publication, review = edit_inputs(name, *EDITS[name])
                            groups = [["record-0", "record-1"]] if arm == "baseline" else [["record-0"], ["record-1"]]
                            for ids in groups:
                                focused = {**review, "subject_reviews": [{
                                    **review["subject_reviews"][0], "hypotheses": [
                                        item for item in review["subject_reviews"][0]["hypotheses"]
                                        if item["hypothesis_id"] in ids],
                                }]}
                                packet = module.repair_context(focused, "frozen", publication)
                                prompt = RolePrompt(
                                    "Edit the supplied proposals. Return complete replacements or withdraw an "
                                    "ungrounded premise with a reason. Do not execute future tests.\n"
                                    + module.HYPOTHESIS_SEMANTIC_GUIDANCE,
                                    packet,
                                )
                                output = runner.structured("task_executor", prompt, edit_schema(ids), [])
                                row["calls"].append({"prompt": str(prompt), "output": output.model_dump()})
                        row["status"] = "completed"
                    except Exception as error:
                        row.update(status="failed", error=str(error))
                    row["requests"] = json.loads(counter.read_text())["requests"] - start
                    destination.write_text(json.dumps(row, indent=2))
                    print(destination.name, row["status"], row["requests"], flush=True)


if __name__ == "__main__":
    main()
