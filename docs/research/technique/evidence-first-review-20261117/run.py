"""Resumable, isolated comparison of actual assembled hypothesis-review inputs."""

import inspect
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import ollama
import pytest
from dotenv import load_dotenv
from strands import Agent

from modules.agents import multi_agent_workflow as workflow
from modules.agents.hypothesis_review import BoundHypothesisReview, normalize_review, validate_review
from modules.agents.native_structured import invoke_structured
from modules.config.models.factory import create_strands_model, require_prompt_token_limit
from tests import test_multi_agent_workflow as fixtures

# Fixed before model execution. True means an empty publication should be revised.
CASES = [
    ("whole-query", True, "https://service.test/?label=map",
     ("function render(){ htmlRenderer(decodeURIComponent(window.location.search)); } "
     "htmlRenderer assigns createContextualFragment(input) to the displayed footer.")),
    ("unlinked-parameter", False, "https://service.test/?label=map",
     ("The route displays a fixed map title. A separate unused library defines htmlRenderer. "
     "No observed call connects the query or route to that library.")),
    ("storage-identity", True, "https://service.test/admin",
     ("The admin route reads editable browser storage email and passes it to fetchUser(email). "
     "The returned isAdmin flag controls the admin view. Backend enforcement is unobserved.")),
    ("bare-route", False, "https://service.test/entry",
     "Only a route name is recorded. No request, implementation, input, or behavior is available."),
    ("network-access", True, "tcp://service.test:1234",
     ("A listener accepts a supplied principal and record selector and returns that record. "
     "Ownership enforcement is unobserved. Two same-role accounts own separate records.")),
    ("filesystem-access", True, "/workspace/source.py",
     ("A source handler joins an attacker-supplied filename to an export directory and opens the result. "
     "Path containment checks are unobserved. A harmless sibling fixture can test containment.")),
]


def prepare(root, case, monkeypatch, with_validator=False, with_controller=False):
    monkeypatch.setenv("CYBER_AGENT_OUTPUT_DIR", str(root))
    name, _, subject, evidence = case
    controller, *_ = fixtures.worker_flow_case.__wrapped__(root, monkeypatch)
    plan = fixtures._plan()
    phase = replace(plan.phases[0], produces_hypotheses=True)
    task = workflow.hypothesis_contract(fixtures._hypothesis_task(status="active"))
    manifest = {"schema_version": 1, "unassessed_gaps": [], "items": [{
        "id": "subject", "kind": ("service" if subject.startswith("tcp:") else
                                  "technology" if subject.startswith("/") else "endpoint"), "target_id": "target-1", "value": subject,
        "attributes": {"source_artifact_refs": ["artifact:source.txt"]},
    }]}
    (root / "inventory.json").write_text(json.dumps(manifest))
    (root / "source.txt").write_text(evidence)
    _, digest = fixtures.memory_mod._load_inventory_manifest(
        "artifact:inventory.json", persist_normalization=False,
    )
    task = replace(task, acceptance=replace(task.acceptance, manifest_hash="", basis=replace(
        task.acceptance.basis, source_refs=["artifact:inventory.json"], snapshot_hash=digest,
    )))
    controller.state.store_task(task)
    fixtures._publish_controller_hypotheses(controller, task)
    task = next(t for t in controller.state.tasks if t.task_uid == task.task_uid)
    outcomes = [workflow.ToolOutcome(
        sequence=1, tool_use_id=name, tool_name="read_artifact", success=True, correctable=False,
        input_summary="Read assigned source", output_summary=evidence,
        artifact_refs=("artifact:source.txt",),
    )]
    if with_validator:
        journal = workflow.hypothesis_inspection_journal(task, controller.runtime.operation_id, str(root))
        reference = journal.record_inspection(
            "read_artifact", name, {"path": "artifact:source.txt"},
            json.dumps({"content": evidence}), ("artifact:source.txt",),
        )
        outcomes[0] = replace(outcomes[0], inspection_result_ref=reference,
                              structured_input={"path": "artifact:source.txt"})
        workflow.retain_inspections(task, outcomes, str(root))
    binding = BoundHypothesisReview.capture(task, controller.runtime.operation_id)
    kwargs = {"worker_context": "No safe probe in current scope", "tool_outcomes": outcomes,
              "review_binding": binding}
    source = inspect.getsource(type(controller)._task_evaluator_prompt)
    old = (
        '+ task_payload + "\\nFrozen acceptance results:\\n" + acceptance_result_section\n'
        '                + hypothesis_review + self._hypothesis_input_context(task)\n'
        '                + artifact_limit_section + tool_outcome_section + worker_context_section'
    )
    new = (
        '+ task_payload + self._hypothesis_input_context(task)\n'
        '                + artifact_limit_section + tool_outcome_section + hypothesis_review'
    )

    def assemble(candidate):
        namespace = dict(vars(workflow))
        candidate = "\n".join(line[4:] if line.startswith("    ") else line for line in candidate.splitlines())
        exec(compile(candidate, "<review-assembly>", "exec"), namespace)
        return namespace["_task_evaluator_prompt"](controller, plan, phase, task, **kwargs)

    source = source.replace(new, old)
    baseline = assemble(source)
    revised = assemble(source.replace(old, new))
    assert baseline != revised
    result = (controller._task_evaluator_system_prompt(), binding.output_model, baseline, revised)
    if with_validator:
        def validate(data):
            canonical = normalize_review(binding.canonicalize(data, task))
            validate_review(canonical, task, controller.runtime.operation_id)
            return canonical
        if with_controller:
            return (*result, validate, controller, plan, phase, task)
        return (*result, validate)
    return result


def main():
    load_dotenv(".env")
    destination = Path("outputs/technique_review_trials/evidence-first-review-20261117/evidence-id-bound")
    destination.mkdir(parents=True, exist_ok=True)
    protocol = {"cases": CASES, "repetitions": 3, "arms": ["baseline", "revised"],
                "citation_schema": "explicit pattern on shared alias and conditional branches",
                "limitation": "Actual assembled prompt and bound schema; inline tool outcomes; no reader loop. "
                              "Includes actual journal and controller review validation; not a full execution cycle."}
    (destination / "protocol.json").write_text(json.dumps(protocol, indent=2))
    for case in CASES:
        for repetition in range(3):
            with TemporaryDirectory() as folder, pytest.MonkeyPatch.context() as monkeypatch:
                system, schema, baseline, revised, validate = prepare(
                    Path(folder).resolve(), case, monkeypatch, with_validator=True,
                )
                for arm, prompt in [("baseline", baseline), ("revised", revised)]:
                    path = destination / f"{case[0]}-{repetition}-{arm}.json"
                    if path.exists():
                        continue
                    record = {"case": case[0], "expected_revision": case[1], "arm": arm,
                              "system": system, "prompt": prompt, "schema": schema.model_json_schema()}
                    try:
                        agent = Agent(
                            model=create_strands_model("ollama", "muse-glimmer:30b-mlx", "task_evaluator"),
                            system_prompt=system, tools=[], callback_handler=None, load_tools_from_directory=False,
                        )
                        agent._prompt_token_limit = require_prompt_token_limit("ollama", "muse-glimmer:30b-mlx")
                        record["attempts"] = []
                        current_prompt = prompt
                        for _attempt in range(3):
                            result = invoke_structured(agent, current_prompt, schema, investigate=False)
                            output = schema.model_validate(result.structured_output).model_dump()
                            try:
                                record["output"] = validate(output)
                                record["controller_valid"] = True
                                break
                            except ValueError as error:
                                details = getattr(error, "details", {"error": str(error)})
                                record["attempts"].append({"output": output, "validation_error": details})
                                current_prompt = prompt + "\nController validation correction (data):\n" + json.dumps(details)
                        else:
                            raise ValueError("Controller validation remained unresolved after three attempts")
                    except Exception as error:
                        record["error"] = str(error)
                    path.write_text(json.dumps(record, indent=2))
                    print(path.name, "error" if "error" in record else "completed", flush=True)


if __name__ == "__main__":
    counter = {"dispatches": 0}
    original_chat = ollama.AsyncClient.chat

    async def counted_chat(client, *args, **kwargs):
        counter["dispatches"] += 1
        folder = Path("outputs/technique_review_trials/evidence-first-review-20261117/evidence-id-bound")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dispatch-count.json").write_text(json.dumps(counter))
        return await original_chat(client, *args, **kwargs)

    with patch.object(ollama.AsyncClient, "chat", counted_chat):
        main()
