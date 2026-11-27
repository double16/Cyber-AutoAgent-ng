"""Isolated selection/proposal/review/execution diagnosis, without operation scheduling."""

import hashlib
import inspect
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import ollama
import pytest
from dotenv import load_dotenv
from fixtures import CASES, Fixture
from pydantic import BaseModel, ConfigDict, Field
from strands import tool

from modules.agents import hypothesis_context, hypothesis_review
from modules.agents.hypothesis_context import HYPOTHESIS_AUTHORIZATION_GUIDANCE, HYPOTHESIS_GROUNDING_GUIDANCE
from modules.agents.hypothesis_review import BoundHypothesisReview, normalize_review, validate_review
from modules.techniques import roles
from modules.techniques.cli import build_service
from modules.techniques.context import MethodologyContext
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import Hypothesis, HypothesisSet
from tests import test_multi_agent_workflow as support

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/e2e-diagnostic-20261123")
MODEL = "muse-glimmer:30b-mlx"
ORIGINAL_CHAT = ollama.AsyncClient.chat
SCOPE = {"trial": "initialization", "stage": "catalog", "trial_start": 0}
REQUESTS = []


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: Literal["violation", "no_violation_observed", "inconclusive"]
    evidence_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)


def baseline_renderer():
    source = "".join(line[4:] if line.startswith("    ") else line for line in inspect.getsource(
        support.MultiAgentWorkflowController._task_evaluator_prompt,
    ).splitlines(True))
    start = source.index('            + json.dumps({\n')
    end = source.index('            + "\\nPlan constraints:', start)
    source = source[:start] + (
        '            + task_payload + self._hypothesis_input_context(task)\n'
        '            + artifact_limit_section + tool_outcome_section + hypothesis_review\n'
    ) + source[end:]
    namespace = dict(support.workflow_mod.__dict__)
    exec(compile(source, "diagnostic_baseline", "exec"), namespace)
    return namespace["_task_evaluator_prompt"]


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Diagnostic's 360-request cap exhausted")
    if counter["requests"] - SCOPE["trial_start"] >= 30:
        raise RuntimeError("Trial's 30-request cap exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    REQUESTS.append({"sequence": counter["requests"], "trial": SCOPE["trial"], "stage": SCOPE["stage"],
                     "native_schema": isinstance(kwargs.get("format"), dict)})
    with (ROOT / "request-stages.jsonl").open("a") as stream:
        stream.write(json.dumps(REQUESTS[-1]) + "\n")
    return await ORIGINAL_CHAT(client, *args, **kwargs)


def save(folder, name, value):
    (folder / name).write_text(json.dumps(value, indent=2, default=str))


def review(folder, fixture, proposal, arm, runner):
    with pytest.MonkeyPatch.context() as monkeypatch:
        controller, *_rest = support.worker_flow_case.__wrapped__(folder, monkeypatch)
        task = support.workflow_mod.hypothesis_contract(support._hypothesis_task(status="active"))
        manifest = {"schema_version": 1, "unassessed_gaps": [], "items": [{
            "id": "subject", "kind": {"http": "endpoint", "network": "service", "filesystem": "technology"}[fixture.kind],
            "target_id": "target-1", "value": (
                f"http://127.0.0.1:{fixture.server.server_address[1]}/navigate" if fixture.kind == "http" else
                f"tcp://127.0.0.1:{fixture.server.server_address[1]}" if fixture.kind == "network" else
                str(fixture.root / "exports")
            ), "attributes": {
                "source_artifact_refs": ["artifact:source.txt"], "recorded_observation": fixture.source(),
            },
        }]}
        (folder / "inventory.json").write_text(json.dumps(manifest))
        (folder / "source.txt").write_text(fixture.source())
        _, digest = support.memory_mod._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
        task = replace(task, acceptance=replace(task.acceptance, manifest_hash="", basis=replace(
            task.acceptance.basis, snapshot_hash=digest, source_refs=["artifact:inventory.json"],
        )))
        proposal.hypothesis_id = hashlib.sha256(proposal.model_dump_json().encode()).hexdigest()[:24]
        publication = HypothesisSet.model_validate({
            "schema_version": "hypothesis_set_v1", "operation_id": controller.runtime.operation_id,
            "producer_task_uid": task.task_uid, "snapshot_hash": digest,
            "source_refs": ["artifact:inventory.json"], "subjects": [{
                "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [proposal.model_dump()],
            }],
        })
        binding = {"artifact_ref": "artifact:current.json", "sha256": hashlib.sha256(publication.model_dump_json().encode()).hexdigest()}
        save(folder, "current.json", publication.model_dump())
        journal = support.workflow_mod.hypothesis_inspection_journal(task, controller.runtime.operation_id, str(folder))
        journal.record_inspection("read_artifact", "assigned-source", {}, json.dumps({"content": fixture.source()}), ("artifact:source.txt",))
        monkeypatch.setattr(controller, "_hypothesis_identity_context", lambda _task: json.dumps({
            "declared_roles": ["user"] if fixture.kind != "http" else [], "readiness": "not established by generation",
        }))
        for module in (hypothesis_context, hypothesis_review, support.workflow_mod):
            monkeypatch.setattr(module, "hypothesis_binding", lambda _task: binding)
        for module in (hypothesis_context, hypothesis_review):
            monkeypatch.setattr(module, "load_hypothesis_set", lambda *_args, **_kwargs: publication)
        capture = BoundHypothesisReview.capture(task, controller.runtime.operation_id)
        schema = capture.output_model
        prompt = (baseline_renderer()(controller, support._plan(), support._plan().phases[0], task) if arm == "baseline"
                  else controller._task_evaluator_prompt(support._plan(), support._plan().phases[0], task))
        system = controller._task_evaluator_system_prompt()
        with patch.object(roles, "SYSTEM", system):
            answer = runner.structured("task_evaluator", prompt, schema, [])
        canonical = normalize_review(capture.canonicalize(answer.model_dump(), task))
        validate_review(canonical, task, controller.runtime.operation_id)
        return answer, {"system": system, "prompt": prompt, "schema": schema.model_json_schema()}


def trial(service, runner, index, kind, vulnerable, arm):
    name = f"case-{index:02}-{arm}"
    folder = ROOT / name
    folder.mkdir(parents=True, exist_ok=True)
    outcome = folder / "outcome.json"
    if outcome.exists():
        return
    before = json.loads((ROOT / "requests.json").read_text())["requests"]
    SCOPE.update(trial=name, trial_start=before, stage="fixture")
    fixture = Fixture(folder / "owned-target", kind, vulnerable)
    row = {"case_index": index, "kind": kind, "arm": arm, "status": "unresolved"}
    try:
        descriptions = {
            "http": ("Open redirect destination control hypothesis generation", "Design and assess a future redirect destination-boundary test for the assigned navigation helper."),
            "network": ("Object-level authorization hypothesis generation", "Design and assess a selected-record ownership-boundary test for the assigned TCP listener."),
            "filesystem": ("Path traversal and file inclusion hypothesis generation", "Design and assess an export-directory containment test for the assigned file reader."),
        }
        request, objective = descriptions[kind]
        local = {"purpose": objective, "subjects": [{"id": "subject", **fixture.interface()}],
                 "observations": [{"origin": "source", "excerpt": fixture.source()}], "constraints": ["Owned fixture only"]}
        events = []
        service.events.callback = events.append
        SCOPE["stage"] = "selection"
        with service.events.scope(operation_id="ISOLATED_E2E", task_uid=name):
            selected = service.select_existing(request, MethodologyContext(phase_contexts=["hypothesis_generation"]),
                                               **({"task_context": local} if arm == "candidate" else {}))
        save(folder, "selection-events.json", events)
        row["selected_revision"] = selected[0] if selected else None
        row["selection_available"] = selected is not None
        guidance = selected[1] if selected else "No document selected; use available observations and model reasoning."
        if selected:
            service.repository.use("ISOLATED_E2E", name, selected[0], {
                **selected[2], "delivery_mode": "isolated_e2e", "delivered_documents": [guidance],
            })
        save(folder, "delivery.json", {"revision": row["selected_revision"], "guidance": guidance})
        SCOPE["stage"] = "proposal"
        instructions = HYPOTHESIS_GROUNDING_GUIDANCE + HYPOTHESIS_AUTHORIZATION_GUIDANCE + (
            "\nProduce one conditional future test for the assigned interaction. The current evidence is "
            "artifact:source.txt. Describe controls, security consequence and evidence needed; execution occurs later."
        )
        data = json.dumps({"objective": objective, "source": fixture.source(), "interface": fixture.interface(),
                           "declared_roles": ["user"] if kind != "http" else [], "advisory_guidance": guidance})
        save(folder, "proposal-input.json", {"instructions": instructions, "data": data, "schema": Hypothesis.model_json_schema()})
        proposal = runner.structured("task_executor", RolePrompt(instructions, data), Hypothesis, [])
        for attempt in range(2):
            if any(ref != "artifact:source.txt" for ref in proposal.supporting_refs):
                raise ValueError("Proposal references evidence outside the frozen assigned source")
            SCOPE["stage"] = "review"
            answer, assembled = review(folder, fixture, proposal, arm, runner)
            save(folder, f"review-{attempt}.json", {"assembled": assembled, "output": answer.model_dump()})
            if answer.status == "done" and answer.subject_reviews[0].hypotheses[0].decision == "retain":
                break
            if attempt:
                row.update(status="review_unresolved", reason=answer.reason)
                return
            SCOPE["stage"] = "proposal_repair"
            proposal = runner.structured("task_executor", RolePrompt(instructions, data + "\nExisting proposal:\n"
                                         + proposal.model_dump_json() + "\nReview feedback:\n" + answer.model_dump_json()), Hypothesis, [])
        save(folder, "accepted-proposal.json", proposal.model_dump())

        @tool
        def inspect_fixture(kind: str) -> str:
            """Read owned fixture context. kind: interface, identity, or policy."""
            value = fixture.context(kind)
            save(folder, "observations.json", fixture.observations)
            return json.dumps(value)

        @tool
        def probe_fixture(principal: str, input_value: str) -> str:
            """Perform one owned read-only probe using declared principals and input values only.

            Read inspect_fixture(interface) for canonical principal and input_value strings.
            HTTP redirects are recorded but never followed. At most 16 probes are available.
            """
            value = fixture.probe(principal, input_value)
            save(folder, "observations.json", fixture.observations)
            return json.dumps(value)

        SCOPE["stage"] = "execution"
        execution = RolePrompt(
            "Execute the assigned conditional test using only the owned fixture tools. Adapt the proposal "
            "to recorded identity/policy/interface context. Return a classification and observation IDs "
            "supporting it. Distinguish protected effects from availability. No external target is accessible.",
            json.dumps({"objective": objective, "proposal": proposal.model_dump(), "advisory_guidance": guidance}),
        )
        save(folder, "execution-input.json", {"prompt": str(execution), "tools": [inspect_fixture.tool_spec, probe_fixture.tool_spec]})
        result = runner.structured("task_executor", execution, Result, [inspect_fixture, probe_fixture])
        row.update(status="completed", result=result.model_dump(), **fixture.grade(result.classification, result.evidence_ids))
    except Exception as error:
        row.update(status="unresolved", stage=SCOPE["stage"], error=str(error))
    finally:
        fixture.close()
        row.update(requests=json.loads((ROOT / "requests.json").read_text())["requests"] - before,
                   observations=fixture.observations)
        save(folder, "outcome.json", row)
        print(name, row["status"], row["requests"], flush=True)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "requests.json"
    if not path.exists():
        path.write_text(json.dumps({"requests": 0, "limit": 360}))
    hashes = {name: hashlib.sha256((BASE / name).read_bytes()).hexdigest()
              for name in ("run.py", "fixtures.py", "protocol.json")}
    frozen = ROOT / "source-hashes.json"
    if frozen.exists() and json.loads(frozen.read_text()) != hashes:
        raise RuntimeError("Diagnostic source changed after freezing; use a new study instead")
    save(ROOT, "source-hashes.json", hashes)
    with patch.dict(os.environ, {"QDRANT_URL": "", "CYBER_AGENT_OUTPUT_DIR": str(ROOT / "catalog")}), patch.object(
        ollama.AsyncClient, "chat", counted,
    ):
        service = build_service("ollama", MODEL, str(ROOT / "catalog"), "web")
        try:
            catalog = [{key: row[key] for key in ("revision_id", "source_sha256", "active", "generation_version")}
                       for row in service.repository.revisions()]
            snapshot = ROOT / "catalog-snapshot.json"
            if snapshot.exists() and json.loads(snapshot.read_text()) != catalog:
                raise RuntimeError("Isolated catalog changed after freeze; do not mix trial catalogs")
            save(ROOT, "catalog-snapshot.json", catalog)
            runner = TechniqueRoleRunner("ollama", MODEL)
            for index, (kind, vulnerable) in enumerate(CASES):
                arms = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
                for arm in arms:
                    if json.loads(path.read_text())["requests"] >= 360:
                        folder = ROOT / f"case-{index:02}-{arm}"
                        folder.mkdir(parents=True, exist_ok=True)
                        if not (folder / "outcome.json").exists():
                            save(folder, "outcome.json", {"case_index": index, "kind": kind, "arm": arm,
                                                         "status": "unattempted_budget_exhausted", "requests": 0})
                        continue
                    trial(service, runner, index, kind, vulnerable, arm)
        finally:
            service.store.qdrant.close()


if __name__ == "__main__":
    main()
