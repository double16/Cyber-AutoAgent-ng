"""Isolated synthetic task comparison. No network target or production store access."""
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: Literal["violation", "no_violation_observed", "inconclusive"]
    evidence_ids: list[str]
    explanation: str


def response(mode, principal, selector):
    if principal not in {"A", "B"}:
        raise ValueError("Unknown test principal")
    owner = selector if selector in {"A", "B"} else principal
    if mode == "enforced" and owner != principal:
        return {"status": 403, "body": {"error": "denied"}}
    if mode == "shape" and owner != principal:
        return {"status": 206, "body": {"records": [{"owner": owner, "value": f"record-{owner}"}]}}
    return {"status": 200, "body": {"owner": owner, "value": f"record-{owner}"}}


def grade(mode, decision, observations):
    expected = "no_violation_observed" if mode == "enforced" else "violation"
    ids = {r["id"] for r in observations}
    valid = bool(decision.evidence_ids) and set(decision.evidence_ids) <= ids
    cross = any(r["principal"] == "A" and r["selector"] == "B" for r in observations)
    cited_cross = any(r["id"] in decision.evidence_ids and r["principal"] == "A" and r["selector"] == "B"
                      for r in observations)
    return {"correct_classification": decision.classification == expected,
            "valid_evidence_ids": valid, "decisive_probe": cross,
            "supported_correct": decision.classification == expected and valid and cited_cross}


def main():
    import ollama
    from dotenv import load_dotenv
    from strands import Agent, tool

    from modules.agents.native_structured import invoke_structured
    from modules.config.models.factory import create_strands_model, require_prompt_token_limit
    from modules.config.models.invocation import model_output_budget
    from modules.techniques.diagnostics import DiagnosticSession

    load_dotenv(".env")
    root = Path("outputs/technique_review_trials/task-guidance-comparison-20261115-2")
    root.mkdir(mode=0o700)
    guidance = Path("docs/research/technique/line-authorship-pipeline-20261115/generated-http.md").read_text()
    plans = []
    for repeat in range(2):
        for mode in ("fallback", "enforced", "shape"):
            for arm in (("guidance", "none") if repeat == 0 else ("none", "guidance")):
                plans.append({"repeat": repeat, "mode": mode, "arm": arm})
    manifest = {"limit": 100, "model": "muse-glimmer:30b-mlx", "plans": plans,
                "guidance_sha256": hashlib.sha256(guidance.encode()).hexdigest()}
    (root / "protocol.json").write_text(json.dumps(manifest, indent=2))
    counts = {"requests": 0, "limit": 100}
    original = ollama.AsyncClient.chat

    async def counted(client, *args, **kwargs):
        if counts["requests"] >= 100:
            raise RuntimeError("Authorized request allowance exhausted")
        counts["requests"] += 1
        (root / "budget.json").write_text(json.dumps(counts))
        return await original(client, *args, **kwargs)

    ollama.AsyncClient.chat = counted
    outcomes = []
    try:
        for index, plan in enumerate(plans):
            row = dict(plan, index=index)
            if counts["requests"] >= 100:
                outcomes.append(dict(row, status="unattempted_budget_exhausted"))
                continue
            folder = root / f"{index:02}"
            folder.mkdir(mode=0o700)
            observations = []

            @tool
            def request_account(principal: str, selector: str) -> str:
                """Send a request to the isolated simulated account endpoint.

                Args:
                    principal: Authenticated test principal, A or B.
                    selector: Value of the X-Account header; any string is allowed.
                """
                result = response(plan["mode"], principal, selector)
                record = {"id": f"obs-{len(observations) + 1}", "principal": principal,
                          "selector": selector, "response": result}
                observations.append(record)
                return json.dumps(record)

            session = DiagnosticSession(quiet=True, verbose=True)
            session.open(str(folder))
            before = counts["requests"]
            prompt = (
                "Assess whether this account endpoint permits unauthorized object access through the "
                "X-Account selector header. Policy: principal A may read only account A; principal B "
                "may read only account B. Both test principals have valid sessions. The endpoint may "
                "ignore, reject or use header values. Use the supplied request capability to investigate. "
                "Return your conclusion, observation IDs supporting it, and explanation. "
                "No external systems are available."
            )
            if plan["arm"] == "guidance":
                prompt += "\nAdvisory technique, to apply with judgment:\n" + guidance
            try:
                model = create_strands_model("ollama", "muse-glimmer:30b-mlx", "technique_author")
                with session.role("task_comparison", "ollama", "muse-glimmer:30b-mlx") as hook:
                    agent = Agent(model=model, tools=[request_account], hooks=[hook], callback_handler=None,
                                  system_prompt="Investigate the assigned synthetic security task using available tools.",
                                  load_tools_from_directory=False)
                    agent._prompt_token_limit = require_prompt_token_limit("ollama", "muse-glimmer:30b-mlx")
                    with model_output_budget("technique_author", 4096):
                        result = invoke_structured(agent, prompt, Decision, investigate=True)
                    answer = Decision.model_validate(result.structured_output)
                row.update(status="completed", answer=answer.model_dump(), **grade(plan["mode"], answer, observations))
            except Exception as error:
                row.update(status="unresolved", error=str(error), error_type=type(error).__name__)
            finally:
                session.close()
            row.update(requests=counts["requests"] - before, observations=observations)
            outcomes.append(row)
            (root / "results.json").write_text(json.dumps({"counts": counts, "outcomes": outcomes}, indent=2))
            print(json.dumps({k:v for k,v in row.items() if k not in {"observations", "answer"}}), flush=True)
    finally:
        ollama.AsyncClient.chat = original
        (root / "results.json").write_text(json.dumps({"counts": counts, "outcomes": outcomes}, indent=2))


if __name__ == "__main__":
    main()
