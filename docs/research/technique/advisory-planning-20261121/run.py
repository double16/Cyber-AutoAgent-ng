"""Paired production planning replay; isolated storage and no target execution."""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.techniques import bundles
from modules.techniques.alignment import RequestAlignment
from modules.techniques.context import MethodologyContext
from modules.techniques.roles import TechniqueRoleRunner
from tests.techniques.conftest import markdown, scripted_service, store

ROOT = Path.cwd() / "outputs/technique_review_trials/advisory-20261121"
ROOT.mkdir(parents=True, exist_ok=True)
load_dotenv()
baseline = type(bundles)("modules.techniques.baseline_replay")
exec(
    compile(
        subprocess.check_output(
            ["git", "show", "d0cc1a7b84edc4d783519fcb25a8589391b59b0f:src/modules/techniques/bundles.py"],
            text=True,
        ),
        "baseline_bundles.py",
        "exec",
    ),
    baseline.__dict__,
)
CASES = {
    "email": (
        "Generate conditional hypotheses for email header injection and mail relay abuse via "
        "recorded recipient, subject and content query inputs. Include baseline, controlled "
        "variation, security consequence and future evidence."
    ),
    "csrf": (
        "Generate conditional CSRF hypotheses for a recorded client-generated fingerprint "
        "header. Cookie authentication, protected action and cross-origin request feasibility "
        "remain unknown."
    ),
    "oidc": (
        "Generate conditional information-disclosure hypotheses for a client-declared OIDC "
        "configuration GET. Public configuration versus protected registration data is "
        "unknown."
    ),
    "chat": (
        "Generate conditional prompt-injection, tool-mediated SSRF and mass-assignment "
        "hypotheses for a client-declared chat POST with an opaque JSON body. Tool execution "
        "and property binding remain unknown."
    ),
    "filesystem": (
        "Generate conditional path-containment hypotheses for source joining an attacker- "
        "supplied filename to an export directory. Allowed root policy and containment "
        "enforcement require future checks."
    ),
}
counter = {"requests": 0, "limit": 210}
original = ollama.AsyncClient.chat


async def counted(client, *args, **kwargs):
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Isolated replay request limit exhausted")
    counter["requests"] += 1
    (ROOT / "requests.json").write_text(json.dumps(counter))
    return await original(client, *args, **kwargs)


def trial(variant, case, repetition):
    records = []
    target = ROOT / f"{case}-{variant}-{repetition}.json"
    if target.exists():
        return
    tmp = ROOT / f"storage-{case}-{variant}-{repetition}"
    tmp.mkdir(exist_ok=True)
    generator = store.__wrapped__(tmp)
    storage = next(generator)
    state = scripted_service.__wrapped__(storage, markdown.__wrapped__())
    live = TechniqueRoleRunner("ollama", "muse-glimmer:30b-mlx")

    class Runner:
        def structured(self, role, prompt, schema, tools):
            row = {
                "role": role,
                "system_instructions": getattr(prompt, "instructions", ""),
                "input": getattr(prompt, "data", str(prompt)),
                "schema": schema.model_json_schema(),
            }
            records.append(row)
            result = live.structured(role, prompt, schema, tools)
            row["output"] = result.model_dump()
            return result

    state.service.runner = Runner()
    state.service.get = lambda request, *a, **kw: (
        "isolated-revision",
        "Isolated lookup boundary; no authoring or execution.",
    )
    module = bundles if variant == "advisory" else baseline
    start = time.monotonic()
    before = counter["requests"]
    try:
        with patch.object(
            module,
            "align_request",
            lambda service, request, context: (
                RequestAlignment(original_request=request, adjusted_request=request),
                context,
            ),
        ):
            result = module.resolve_bundle(
                state.service, CASES[case], MethodologyContext(phase_contexts=["hypothesis_generation"])
            )
        payload = {"components": [item.component.model_dump() for item in result.delivered], "valid": True}
    except Exception as error:
        payload = {"valid": False, "error": str(error)}
    finally:
        generator.close()
    payload.update(
        case=case,
        variant=variant,
        repetition=repetition,
        calls=records,
        requests=counter["requests"] - before,
        elapsed_seconds=time.monotonic() - start,
    )
    target.write_text(json.dumps(payload, indent=2))
    print(target.name, payload["valid"], payload["requests"], flush=True)


if __name__ == "__main__":
    with patch.object(ollama.AsyncClient, "chat", counted):
        for case in CASES:
            for repetition in range(3):
                for variant in ("baseline", "advisory"):
                    trial(variant, case, repetition)
