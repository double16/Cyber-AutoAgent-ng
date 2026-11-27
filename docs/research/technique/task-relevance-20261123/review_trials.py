"""Paired complete evaluator inputs on frozen synthetic source-policy fixtures."""

import hashlib
import inspect
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import ollama
import pytest
from dotenv import load_dotenv
from selection_trials import BASE, ROOT, counted

from modules.agents import hypothesis_context, hypothesis_review
from modules.techniques import roles
from modules.techniques.roles import TechniqueRoleRunner
from modules.tools.hypotheses import HypothesisSet
from tests import test_multi_agent_workflow as support


def cases():
    definitions = json.loads((BASE.parent / "identity-presentation-20261122/cases.json").read_text())
    for row in definitions:
        row["expected_check"] = "identity_requirements"
        row["expected_verdict"] = row["expected_identity"]
    seed = definitions[2]["publication"]
    additions = [
        ("dom", "Browser query input flows directly into an HTML DOM sink without server reflection.",
         "The browser may interpret query-controlled markup.",
         "Require server reflection before trying a harmless DOM marker.",
         "No server reflection refutes client-side DOM injection.",
         "Trace a harmless markup marker from location.search to the DOM sink.",
         "The browser inserts the marker as active markup rather than escaped text."),
        ("csrf", "A client generates a fingerprint header for a GET helper; ambient credentials and protected actions are unknown.",
         "A browser may perform an unauthorized protected action using ambient authority.",
         "Replay a captured fingerprint header with an HTTP client.",
         "Acceptance of any fingerprint proves CSRF.",
         "Identify an authorized reversible protected action and ambient credential attachment, then compare a browser cross-origin request and inert control, checking preflight behavior.",
         "The cross-origin browser request performs the policy-protected action under ambient authority; the inert control does not."),
        ("property", "A handler accepts JSON object properties; the protected flag is reserved by declared policy.",
         "A caller may modify a policy-reserved object property.",
         "Submit a new ordinary display label and check persistence.",
         "Persistence of an ordinary label proves mass assignment.",
         "With an authorized disposable object compare an ordinary label baseline, one policy-reserved flag write, and an inert unknown property; verify authoritative state and restore it.",
         "The excluded actor changes the reserved flag, beyond the permitted ordinary-property baseline."),
        ("parser", "A handler supports two documented content types with equivalent authorization policy.",
         "Parser selection may bypass a policy-protected operation.",
         "Switch content type and observe which parser accepts the request.",
         "A parser change alone proves a security weakness.",
         "Compare the same policy-prohibited operation through both documented parsers using an inert marker and permitted-operation baseline.",
         "One parser permits the policy-prohibited effect while the other denies it under equivalent identity and input."),
    ]
    for name, observed, mechanism, bad_step, bad_positive, good_step, good_positive in additions:
        for valid in (False, True):
            payload = json.loads(json.dumps(seed))
            h = payload["subjects"][0]["hypotheses"][0]
            h.update(observed_basis=observed, suspected_mechanism=mechanism,
                     authentication_requirement={"comparison": "none", "roles": []},
                     preconditions=["Necessary policy, controlled objects and any required single identity must be established before future execution; this proposal does not compare authenticated identities."],
                     safe_test_steps=[good_step if valid else bad_step],
                     positive_expectations=[good_positive if valid else bad_positive],
                     negative_expectations=["The controlled comparison denies the claimed protected effect."],
                     required_evidence=["Retain observed behavior, policy, controlled inputs and semantic outcomes."],
                     attacker_controlled_input="Assigned input only")
            definitions.append({"name": f"{name}-{'valid' if valid else 'defect'}", "publication": payload,
                                "expected_check": "test_discrimination", "expected_verdict": "adequate" if valid else "revise"})
    return definitions


def freeze(output_name="review-inputs-corrected.json", definitions=None):
    original = "".join(line[4:] if line.startswith("    ") else line for line in
                       inspect.getsource(support.MultiAgentWorkflowController._task_evaluator_prompt).splitlines(True))
    start = original.index('            + json.dumps({\n')
    end = original.index('            + "\\nPlan constraints:', start)
    legacy = original[:start] + (
        '            + task_payload + self._hypothesis_input_context(task)\n'
        '            + artifact_limit_section + tool_outcome_section + hypothesis_review\n'
    ) + original[end:]
    namespace = dict(support.workflow_mod.__dict__)
    exec(compile(legacy, "frozen_baseline_review", "exec"), namespace)
    baseline = namespace["_task_evaluator_prompt"]
    rows = []
    for definition in definitions or cases():
        with tempfile.TemporaryDirectory() as temporary, pytest.MonkeyPatch.context() as monkeypatch:
            controller, *_rest = support.worker_flow_case.__wrapped__(Path(temporary), monkeypatch)
            task = support.workflow_mod.hypothesis_contract(support._hypothesis_task(status="active"))
            premise = definition["publication"]["subjects"][0]["hypotheses"][0]["observed_basis"]
            manifest = {"schema_version": 1, "items": [{
                "id": "subject", "kind": "workflow", "target_id": "target-1",
                "value": "Assigned interaction", "attributes": {
                    "source_artifact_refs": ["artifact:form.html"], "recorded_observation": premise,
                },
            }], "unassessed_gaps": []}
            (Path(temporary) / "inventory.json").write_text(json.dumps(manifest))
            _, digest = support.memory_mod._load_inventory_manifest(
                "artifact:inventory.json", persist_normalization=False,
            )
            task = replace(task, acceptance=replace(task.acceptance, manifest_hash="", basis=replace(
                task.acceptance.basis, snapshot_hash=digest, source_refs=["artifact:inventory.json"],
            )))
            payload = json.loads(json.dumps(definition["publication"]))
            payload.update(operation_id=controller.runtime.operation_id, producer_task_uid=task.task_uid,
                           snapshot_hash=task.acceptance.basis.snapshot_hash, source_refs=list(task.acceptance.basis.source_refs))
            payload["subjects"][0]["item_id"] = "subject"
            h = payload["subjects"][0]["hypotheses"][0]
            h["supporting_refs"] = ["artifact:form.html"]
            publication = HypothesisSet.model_validate(payload)
            binding = {"artifact_ref": "artifact:current.json", "sha256": hashlib.sha256(publication.model_dump_json().encode()).hexdigest()}
            source = definition.get("source", "Fixture source-policy premise (synthetic, not live evidence): " + h["observed_basis"])
            (Path(temporary) / "form.html").write_text(source)
            journal = support.workflow_mod.hypothesis_inspection_journal(task, controller.runtime.operation_id, temporary)
            journal.record_inspection("read_artifact", "fixture", {}, json.dumps({"content": source}), ("artifact:form.html",))
            monkeypatch.setattr(controller, "_hypothesis_identity_context", lambda _task: "Declared roles: admin, operator, observer, reader, maintainer, auditor, user. Session readiness is unknown; future identity requirements are independent of generation-task authentication.\n")
            for module in (hypothesis_context, hypothesis_review, support.workflow_mod):
                monkeypatch.setattr(module, "hypothesis_binding", lambda _task: binding)
            for module in (hypothesis_context, hypothesis_review):
                monkeypatch.setattr(module, "load_hypothesis_set", lambda *_args, **_kwargs: publication)
            schema = hypothesis_review.BoundHypothesisReview.capture(task, controller.runtime.operation_id).output_model
            for arm in ("baseline", "candidate"):
                prompt = (baseline(controller, support._plan(), support._plan().phases[0], task) if arm == "baseline"
                          else controller._task_evaluator_prompt(support._plan(), support._plan().phases[0], task))
                prompt = prompt.replace(temporary, "/isolated/frozen-operation")
                rows.append({"case": definition["name"], "arm": arm, "prompt": prompt,
                             "system": controller._task_evaluator_system_prompt(), "schema": schema.model_json_schema(),
                             "expected_check": definition["expected_check"], "expected_verdict": definition["expected_verdict"],
                             "publication": publication.model_dump(), "binding": binding,
                             "admissible_verdicts": definition.get("admissible_verdicts", [definition["expected_verdict"]]),
                             "sha256": hashlib.sha256(prompt.encode()).hexdigest()})
    (BASE / output_name).write_text(json.dumps(rows, indent=2))


def main(input_name="review-inputs.json", destination_root=ROOT, transport=counted):
    load_dotenv()
    if not (BASE / input_name).exists():
        raise RuntimeError("Original frozen inputs missing; do not regenerate a completed study")
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    for repetition in range(2):
        for row in json.loads((BASE / input_name).read_text()):
            destination = destination_root / f"review-{row['case']}-{row['arm']}-{repetition}.json"
            if destination.exists():
                continue
            publication = HypothesisSet.model_validate(row["publication"])
            from types import SimpleNamespace

            task = SimpleNamespace(task_uid="producer", acceptance=SimpleNamespace(criteria=[SimpleNamespace(id="future-tests")]))
            with patch.object(hypothesis_review, "hypothesis_binding", return_value=row["binding"]), patch.object(
                hypothesis_review, "load_hypothesis_set", return_value=publication,
            ), patch.object(hypothesis_review, "hypothesis_inspection_journal", return_value=SimpleNamespace(inspection_availability=dict)):
                schema = hypothesis_review.BoundHypothesisReview.capture(task, publication.operation_id).output_model
            assert schema.model_json_schema() == row["schema"]
            result = {key: row[key] for key in ("case", "arm", "expected_check", "expected_verdict")}
            result.update(repetition=repetition, input_sha256=row["sha256"])
            try:
                with patch.object(ollama.AsyncClient, "chat", transport), patch.object(roles, "SYSTEM", row["system"]):
                    answer = runner.structured("task_evaluator", row["prompt"], schema, [])
                hypothesis_review.validate_subject_review(
                    answer.subject_reviews[0], {h.hypothesis_id for h in publication.subjects[0].hypotheses},
                    task, publication.operation_id, [],
                )
                checks = answer.subject_reviews[0].hypotheses[0].semantic_checks
                actual = getattr(checks, row["expected_check"]).verdict
                result.update(valid=True, output=answer.model_dump(), actual=actual, correct=actual in row.get("admissible_verdicts", [row["expected_verdict"]]))
            except Exception as error:
                result.update(valid=False, error=str(error), correct=False)
            destination.write_text(json.dumps(result, indent=2))
            print(destination.name, result["valid"], flush=True)


if __name__ == "__main__":
    main()
