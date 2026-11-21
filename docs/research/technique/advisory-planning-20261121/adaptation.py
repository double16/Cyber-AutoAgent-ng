"""Isolated guidance-application replay; no target tools or production state."""

import json
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.prompts.security_interpretation import SECURITY_INTERPRETATION
from modules.techniques.roles import RolePrompt, TechniqueRoleRunner
from modules.tools.hypotheses import SubjectHypotheses

ROOT = Path.cwd() / "outputs/technique_review_trials/advisory-20261121"
MODULE = Path("src/modules/operation_plugins/web/techniques")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GUIDES = {
    "email": FIXTURES / "email-header-guidance.md",
    "csrf": MODULE / "csrf.md",
    "oidc": MODULE / "oauth-oidc.md",
    "chat": MODULE / "ssrf.md",
    "filesystem": MODULE / "path-traversal-file-inclusion.md",
}
SOURCES = {
    "email": (
        "Client source constructs a mail-send GET with recipient, subject and content query fields. "
        "Server header construction, recipient policy and mail delivery are unobserved."
    ),
    "csrf": (
        "Client source sends a generated fingerprint header. Cookie authentication, a protected "
        "state change and browser feasibility of cross-origin header submission are unobserved."
    ),
    "oidc": (
        "Client source declares an OIDC configuration GET with no recorded parameters. "
        "Response contents and whether the endpoint is public configuration or protected data are unknown."
    ),
    "chat": (
        "Client source sends an opaque JSON body to a chat POST. Prompt field names, tool execution, "
        "property binding and destination policy are unknown."
    ),
    "filesystem": (
        "Source joins an attacker-selected filename to an export directory before opening it. "
        "An owned allowed fixture and harmless sibling are available; containment enforcement is unknown."
    ),
}

COUNTER = {"requests": 0, "limit": 120}
ORIGINAL = ollama.AsyncClient.chat


async def counted(client, *args, **kwargs):
    if COUNTER["requests"] >= COUNTER["limit"]:
        raise RuntimeError("Guidance-application replay request limit exhausted")
    COUNTER["requests"] += 1
    (ROOT / "application-requests.json").write_text(json.dumps(COUNTER))
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    runner = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")
    for case, source in SOURCES.items():
        for repetition in range(3):
            for variant in ("baseline", "advisory"):
                destination = ROOT / f"application-{case}-{variant}-{repetition}.json"
                if destination.exists():
                    continue
                plan = json.loads((ROOT / f"{case}-{variant}-{repetition}.json").read_text())
                if not plan["valid"]:
                    destination.write_text(
                        json.dumps(
                            {
                                "case": case,
                                "variant": variant,
                                "repetition": repetition,
                                "valid": False,
                                "schema_valid": None,
                                "blocked_upstream": True,
                                "requests": 0,
                                "reason": "Baseline planning did not deliver a plan",
                            },
                            indent=2,
                        )
                    )
                    print(destination.name, "blocked_upstream", flush=True)
                    continue
                guide = GUIDES[case].read_text().split("\n---\n", 1)[-1]
                prompt = RolePrompt(
                    SECURITY_INTERPRETATION
                    + "Generate future hypotheses for subject only, using the supplied source observation "
                    "and adapting useful guidance. Do not execute tests. Use artifact:source.txt for "
                    "supporting_refs. Declare future identity needs when your proposed comparisons use them. "
                    "Return the SubjectHypotheses JSON contract.",
                    "Subject: subject\nSource artifact: artifact:source.txt\n"
                    f"Observation: {source}\n"
                    "Requested methods:\n"
                    + "\n".join(f"- {item['request']}" for item in plan.get("components", []))
                    + "\nAdvisory methodology:\n"
                    + guide,
                )
                record = {
                    "case": case,
                    "variant": variant,
                    "repetition": repetition,
                    "instructions": prompt.instructions,
                    "input": prompt.data,
                    "schema": SubjectHypotheses.model_json_schema(),
                }
                before = COUNTER["requests"]
                try:
                    result = runner.structured("task_executor", prompt, SubjectHypotheses, [])
                    record.update(
                        output=result.model_dump(),
                        schema_valid=True,
                        valid=result.item_id == "subject"
                        and all(set(item.supporting_refs) == {"artifact:source.txt"} for item in result.hypotheses),
                    )
                except Exception as error:
                    record.update(valid=False, schema_valid=False, error=str(error))
                record["requests"] = COUNTER["requests"] - before
                destination.write_text(json.dumps(record, indent=2))
                print(destination.name, record["valid"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        main()
