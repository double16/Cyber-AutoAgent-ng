"""Frozen paired replay of production correction dispatch and durable revision tools."""

import hashlib
import inspect
import json
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ollama
import pytest
from dotenv import load_dotenv
from pydantic import Field, model_validator
from strands import tool

from modules.agents import multi_agent_workflow as workflow
from modules.agents.hypothesis_review import HYPOTHESIS_SEMANTIC_GUIDANCE
from modules.agents.run_policy import AgentRunPolicy
from modules.techniques import roles
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools import hypotheses, memory
from tests.test_hypotheses import hypothesis, producer, publish

ROOT = Path("outputs/technique_review_trials/identity-corrections-20261125").resolve()
REQUEST_LIMIT = 96
ORIGINAL = ollama.AsyncClient.chat
CASES = {
    "authorization-named": (
        ("GET /records accepts an object selector under an account session. The documented policy permits "
        "owners and explicitly shared readers. Roles member and administrator are declared. Sharing state "
        "is unresolved; no qualifying execution credentials are currently available."),
        "Account authorization bypass", "Different record proves unauthorized access", ["member"],
    ),
    "authorization-unnamed": (
        ("GET /records accepts an object selector under an account session. The documented policy permits "
        "owners and explicitly shared readers. No roles are declared. Sharing state is unresolved; no "
        "qualifying execution credentials are currently available."),
        "Account authorization bypass", "Different record proves unauthorized access", [],
    ),
    "rendering": (
        ("POST /render supports user text and a documented URL-retrieval tool. A policy file says user "
        "instructions cannot override the template and retrieval must use public destinations only. "
        "Implementation enforcement is unknown. Both mechanisms are independent proposals."),
        "Instruction override", "Literal marker reflection proves instruction override", [],
    ),
    "permitted-fetch": (
        ("POST /preview intentionally retrieves user-supplied public URLs. Its policy permits public "
        "callbacks but prohibits private network destinations. No private destination was tested."),
        "Server-side request boundary bypass", "A public callback proves SSRF", [],
    ),
    "linked-dom": (
        ("The assigned route links client.js and render.js. client.js reads location.search title then "
        "calls mount(title). Follow the linked render.js artifact to determine its processing boundary."),
        "DOM injection", "Reflection in a page proves script execution", [],
    ),
    "safe-filesystem": (
        ("The assigned import manifest accepts filename. Source resolver.py uses Path.resolve() then "
        "relative_to(root.resolve()) before reading. Escapes raise ValueError, including symlinks. "
        "The documented operation permits reads within root; this is the complete consumer."),
        "Filesystem containment escape", "Reading an in-root file proves path traversal", [],
    ),
}


class BaselineHypothesis(hypotheses.Hypothesis):
    authentication_requirement: hypotheses.AuthenticationRequirement = Field(
        description="Identity needs of this proposed future test, not of the generation task. An authenticated "
                    "single-session test declares one role even with comparison=none; unauthenticated tests "
                    "use no roles. Account comparisons declare one shared role; role comparisons declare two "
                    "distinct roles; tenant comparisons declare their required roles. Declare future needs "
                    "even when sessions are not available.",
    )

    @model_validator(mode="after")
    def require_named_comparison(self):
        if self.authentication_requirement.comparison != "none" and not self.authentication_requirement.roles:
            raise ValueError("comparison hypotheses require explicit roles")
        return self


def baseline_dispatch():
    """Freeze the immediately preceding focused controller, retaining objective and role context."""
    source = textwrap.dedent(inspect.getsource(workflow.MultiAgentWorkflowController._run_hypothesis_correction_batch))
    start = source.index('            tools.append(build_read_hypotheses_tool(')
    end = source.index('            focused =', start)
    source = source[:start] + source[end:]
    start = source.index('                + "\\nRead-only sibling context')
    end = source.index('                + repair_context', start)
    source = source[:start] + source[end:]
    namespace = dict(vars(workflow))
    old = (Path("outputs/technique_review_trials/focused-hypothesis-20261125/candidate_review_snapshot.py")
           .read_text())
    # Snapshot rubric embeds the unchanged pre-change interpretation imported from its source.
    original_security = subprocess.check_output(
        ["git", "show", "HEAD:src/modules/prompts/security_interpretation.py"], text=True,
    )
    security = {}
    exec(original_security, security)
    rubric = old[old.index('HYPOTHESIS_SEMANTIC_GUIDANCE ='):old.index('\n\nclass HypothesisEvidenceError')]
    exec(rubric, {**namespace, **security}, namespace)
    exec(source, namespace)
    return namespace['_run_hypothesis_correction_batch'], source, namespace['HYPOTHESIS_SEMANTIC_GUIDANCE']


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError(f"Frozen {counter["limit"]}-request allowance exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    destination = ROOT / f"request-{counter['requests']:03}.json"
    record = {"request": kwargs}
    destination.write_text(json.dumps(record, default=str))
    try:
        response = await ORIGINAL(client, *args, **kwargs)
        if kwargs.get("stream"):
            async def capture():
                record["response_chunks"] = []
                try:
                    async for chunk in response:
                        record["response_chunks"].append(chunk.model_dump(mode="json"))
                        yield chunk
                except Exception as error:
                    record["error"] = repr(error)
                    raise
                finally:
                    destination.write_text(json.dumps(record, default=str))
            return capture()
        record["response"] = response.model_dump(mode="json")
        return response
    except Exception as error:
        record["error"] = repr(error)
        raise
    finally:
        destination.write_text(json.dumps(record, default=str))


def trial(name, arm, repetition, dispatch, guidance):
    directory = ROOT / f"{name}-{arm}-{repetition}"
    directory.mkdir()
    row = {"case": name, "arm": arm, "repetition": repetition, "dispatches": []}
    start = json.loads((ROOT / "requests.json").read_text())["requests"]
    with pytest.MonkeyPatch.context() as monkeypatch:
        task = producer.__wrapped__(directory, monkeypatch)
        context, mechanism, positive, declared = CASES[name]
        (directory / "observation.txt").write_text(context)
        (directory / "render.js").write_text(
            "export function mount(value){document.querySelector('#out').innerHTML=value;}"
        )
        entry = hypothesis()
        entry.update(observed_basis=context, suspected_mechanism=mechanism, positive_expectations=[positive])
        entries = [entry]
        if name == "rendering":
            sibling = {**hypothesis("URL supplied to renderer"), "observed_basis": context,
                       "suspected_mechanism": "Private destination retrieval", "positive_expectations": [
                           "A future check must show retrieval of a policy-prohibited destination"]}
            entries.append(sibling)
        binding, _ = publish(task, directory, [{
            "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": entries,
        }])
        publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], task, "OP_TEST")
        selected = next(item for item in publication.subjects[0].hypotheses if item.suspected_mechanism == mechanism)
        task.recovery_context["hypothesis_review"] = {
            "publication_sha256": binding["sha256"], "subject_reviews": [{
                "item_id": "subject", "decision": "revise", "reason": "Distinguish permitted behavior",
                "hypotheses": [{"hypothesis_id": selected.hypothesis_id, "decision": "revise",
                                "reason": "Positive observation alone does not establish the claimed consequence"}],
            }],
        }
        store = memory.SQLiteApplicationStore(str(directory / "state.db"), "target")
        store.store_task("OP_TEST", task)
        def current():
            return store.get_tasks("OP_TEST")[0]

        def persist(value):
            store.patch_task("OP_TEST", task.task_uid, recovery_context_updates={
                "hypothesis_publications": [*current().recovery_context["hypothesis_publications"], value],
            })

        @tool
        def record_task_acceptance() -> str:
            """Record corrected-publication availability; this does not approve semantic quality."""
            assert not hypotheses.hypothesis_correction_state(current()).get("pending")
            row["acceptance_called"] = True
            return "Recorded current publication for subsequent semantic review"

        def validate_identity(requirement):
            if set(requirement.roles) - set(declared):
                raise ValueError("Named roles must belong to the declared target catalog")

        def build(latest):
            return [hypotheses.build_revise_hypothesis_tool(
                latest, "OP_TEST", str(directory), persist, current_task=current,
                validate_authentication=validate_identity,
            ), record_task_acceptance,
                hypotheses.build_hypothesis_input_reader({"artifact:observation.txt", "artifact:render.js"})]

        controller = workflow.MultiAgentWorkflowController.__new__(workflow.MultiAgentWorkflowController)
        controller.state = SimpleNamespace(list_tasks=lambda: [current()])
        controller.runtime = SimpleNamespace(operation_id="OP_TEST", config=SimpleNamespace(model_id="muse-glimmer:30b-mlx"))
        controller._fit_hypothesis_replay = lambda prompt, *_: prompt
        controller._hypothesis_identity_context = lambda _: f"Declared roles for target: {declared}\n"
        controller._hypothesis_repair_context = lambda _: "Use current publication for acceptance."
        controller._emit_workflow_event = lambda event: row.setdefault("events", []).append(event)
        monkeypatch.setattr(workflow, "_operation_output_root", lambda: str(directory))
        if arm == "baseline":
            monkeypatch.setattr(hypotheses, "Hypothesis", BaselineHypothesis)
        runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")

        def execute(prompt, policy, tools):
            call = {"prompt": prompt, "tools": [workflow.get_tool_spec(item) for item in tools]}
            row["dispatches"].append(call)
            call["response"] = runner("task_executor", RolePrompt(
                "Correct the assigned future test using the supplied tools. Do not execute target tests. "
                "Inspect linked retained source when needed. Call the required terminal tool then stop. "
                 f"Required terminal tool: {next(iter(policy.required_tool_names))}.", prompt,
            ), tools)
            return workflow.TaskExecutorCycleResult(text=call["response"], outcomes=[])

        try:
            dispatch(controller, current(), execute, AgentRunPolicy(max_agent_calls=3), guidance, build)
            row["pending"] = hypotheses.hypothesis_correction_state(current()).get("pending", {})
            row["publication"] = hypotheses.load_hypothesis_set(
                hypotheses.hypothesis_binding(current())["artifact_ref"], current(), "OP_TEST",
            ).model_dump()
        except Exception as error:
            row["error"] = repr(error)
        finally:
            row["requests"] = json.loads((ROOT / "requests.json").read_text())["requests"] - start
            (directory / "result.json").write_text(json.dumps(row, indent=2, default=str))
    return row


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    baseline, source, old_guidance = baseline_dispatch()
    files = [Path(__file__), Path(workflow.__file__), Path(hypotheses.__file__)]
    frozen = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    frozen["cases"] = CASES
    frozen["request_limit"] = REQUEST_LIMIT
    frozen["baseline_dispatch"] = source
    manifest = ROOT / "frozen.json"
    if manifest.exists():
        raise RuntimeError("Replay already frozen; completed trials must never be selectively rerun")
    manifest.write_text(json.dumps(frozen, indent=2))
    (ROOT / "replay_snapshot.py").write_bytes(Path(__file__).read_bytes())
    (ROOT / "requests.json").write_text(json.dumps({"requests": 0, "limit": REQUEST_LIMIT}))
    with patch.object(ollama.AsyncClient, "chat", counted), patch.object(roles, "SYSTEM", ""):
        for repetition in range(2):
            for index, name in enumerate(CASES):
                arms = ["baseline", "candidate"] if (index + repetition) % 2 == 0 else ["candidate", "baseline"]
                for arm in arms:
                    row = trial(name, arm, repetition, baseline if arm == "baseline" else
                                workflow.MultiAgentWorkflowController._run_hypothesis_correction_batch,
                                old_guidance if arm == "baseline" else HYPOTHESIS_SEMANTIC_GUIDANCE)
                    print(name, arm, repetition, row["requests"], row.get("error", "finished"), flush=True)


if __name__ == "__main__":
    main()
