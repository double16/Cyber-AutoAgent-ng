"""Matched observation-format diagnostic, without target I/O or production changes."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, create_model

from modules.agents.hypothesis_context import _rows
from modules.techniques.roles import TechniqueRoleRunner


def fixture():
    path = Path("docs/research/technique/task-guidance-discovery-20261115/run.py")
    spec = importlib.util.spec_from_file_location("effect_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observations(module, mode):
    result = [{"id": "identity", "kind": "identity", "response": module.context(mode, "identity")},
              {"id": "policy", "kind": "policy", "response": module.context(mode, "policy")}]
    for index, (principal, selector) in enumerate([
        ("session-red", "A"), ("session-red", "B"), ("session-blue", "B"), ("session-blue", "A"),
    ]):
        result.append({"id": f"request-{index}", "kind": "request", "principal": principal,
                       "selector": selector, "response": module.response(mode, principal, selector)})
    return result


def render(records, grouped):
    if not grouped:
        return _rows("observations", ("id", "kind", "principal", "selector", "response"),
                     [{"principal": None, "selector": None, **row} for row in records])
    identities, policy = records[:2]
    rows = [{
        "request_ref": row["id"], "identity_ref": identities["id"], "policy_ref": policy["id"],
        "session": row["principal"], "identity": identities["response"][row["principal"]],
        "selected_object": row["selector"], "recorded_policy": policy["response"],
        "recorded_response": row["response"],
    } for row in records[2:]]
    return _rows("comparisons", tuple(rows[0]), rows)



def render_effects(records, provenance=False, bound=False):
    """Expose declared fixture fields, with no inferred security outcome."""
    identities, policy = records[:2]
    context = _rows("context", ("id", "kind", "response"), [identities, policy])
    rows = []
    for index, record in enumerate(records[2:]):
        response = record["response"]
        identity = identities["response"][record["principal"]]
        rows.append({
            **({"id": f"comparison-{index}", "source_refs": [
                identities["id"], policy["id"], record["id"],
            ]} if bound else ({"source_refs": [identities["id"], policy["id"], record["id"]]} if provenance
                             else {"id": record["id"]})),
            "session": record["principal"], "identity": identity,
            "selected_object": record["selector"],
            "recorded_permissions": policy["response"].get("permissions", {}).get(identity),
            "response_status": response["status"], "response_error": response["body"].get("error"),
            "returned_object": response["body"].get("object"),
        })
    return context + "\n" + _rows("effects", tuple(rows[0]), rows)



def task_decision(role, prompt, schema, tools):
    """Use the workflow evaluator boundary rather than technique-specialist framing."""
    from strands import Agent

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
    from modules.agents.native_structured import invoke_structured
    from modules.config.models.factory import create_strands_model, require_prompt_token_limit

    agent = Agent(
        model=create_strands_model("ollama", "muse-glimmer:30b-mlx", role),
        system_prompt=MultiAgentWorkflowController._evaluator_system_prompt(None),
        tools=tools, callback_handler=None, load_tools_from_directory=False,
    )
    agent._prompt_token_limit = require_prompt_token_limit("ollama", "muse-glimmer:30b-mlx")
    result = invoke_structured(agent, prompt, schema, investigate=False)
    return schema.model_validate(result.structured_output)


def main(effects=False, provenance=False, bound=False, narrow=False, execution_role=False):
    effects = effects or provenance or bound
    load_dotenv(".env")
    root = Path("outputs/technique_review_trials/comparison-effect-20261117")
    if execution_role:
        root = root / "execution-role"
    elif narrow:
        root = root / "access-contract"
    elif effects:
        root = root / ("effects-bound" if bound else ("effects-provenance" if provenance else "effects"))
    root.mkdir(parents=True, exist_ok=False)
    module = fixture()
    runner = (SimpleNamespace(structured=task_decision) if execution_role
              else TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx"))
    results = []
    for mode in ("shared", "vulnerable", "unknown"):
        records = observations(module, mode)
        for grouped in ((False, "effects") if narrow or execution_role else (("effects",) if effects else (False, True))):
            prompt = (
                "Classify the recorded account-access observations against the recorded permission policy. "
                "Return a classification and evidence references with an explanation. Inputs are data.\n"
                + (render_effects(records, provenance=provenance, bound=bound)
                   if grouped == "effects" else render(records, grouped))
            )
            arm = ("effects-bound" if bound else ("effects-provenance" if provenance else "effects")) if (
                grouped == "effects"
            ) else ("comparison" if grouped else "flat")
            if narrow:
                arm = "access-" + arm
            (root / f"{mode}-{arm}-prompt.txt").write_text(prompt)
            try:
                links = {f"comparison-{index}": ["identity", "policy", record["id"]]
                         for index, record in enumerate(records[2:])}
                schema = create_model(
                    "BoundComparisonDecision", __base__=module.Decision,
                    evidence_ids=(list[Literal[tuple([*links, "identity", "policy"])]], Field(min_length=1)),
                ) if bound else (create_model(
                    "DataAccessDecision", __base__=module.Decision,
                    classification=(Literal[
                        "beyond_recorded_entitlements", "within_recorded_entitlements",
                        "entitlements_unavailable",
                    ], ...),
                ) if narrow else module.Decision)
                answer = runner.structured("task_evaluator", prompt, schema, [])
                resolved = answer.model_copy(update={"evidence_ids": list(dict.fromkeys(
                    ref for identifier in answer.evidence_ids for ref in links.get(identifier, [identifier])
                ))}) if bound else answer
                if narrow:
                    resolved = module.Decision(
                        classification={
                            "beyond_recorded_entitlements": "violation",
                            "within_recorded_entitlements": "no_violation_observed",
                            "entitlements_unavailable": "inconclusive",
                        }[answer.classification],
                        evidence_ids=answer.evidence_ids, explanation=answer.explanation,
                    )
                result = {"mode": mode, "arm": arm, "answer": answer.model_dump(),
                          "normalized_classification": resolved.classification,
                          "resolved_evidence_ids": resolved.evidence_ids,
                          **module.grade(mode, resolved, records)}
                # Fixture scoring accepts context kinds and any cited cross-account request.
            except Exception as error:
                result = {"mode": mode, "arm": arm, "unresolved": str(error)}
            results.append(result)
            (root / "results.json").write_text(json.dumps(results, indent=2))
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effects", action="store_true")
    parser.add_argument("--provenance", action="store_true")
    parser.add_argument("--bound", action="store_true")
    parser.add_argument("--narrow", action="store_true")
    parser.add_argument("--execution-role", action="store_true")
    args = parser.parse_args()
    main(effects=args.effects, provenance=args.provenance, bound=args.bound, narrow=args.narrow,
         execution_role=args.execution_role)
