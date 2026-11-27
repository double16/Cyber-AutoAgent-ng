"""Frozen paired whole-request reuse experiment; isolated catalogs and no target I/O."""

import hashlib
import inspect
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.techniques import bundles
from modules.techniques.cli import build_service
from modules.techniques.context import MethodologyContext

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/reuse-first-20261124")
CATALOG = Path("outputs/technique_review_trials/automatic-matching-20261123/catalog")
ORIGINAL = ollama.AsyncClient.chat
CASES = [
    ("recorded-file", "Path traversal and file inclusion boundaries hypothesis generation for an endpoint with a path query parameter selecting server-side file content"),
    ("jwt", "JWT verification hypothesis generation covering both unsigned or algorithm-confusion tests and claim-injection tests, with prerequisites, assumptions and future supporting/refuting checks"),
    ("network", "Object-level authorization hypothesis generation for a TCP record reader with two same-role accounts owning distinct records"),
    ("filesystem", "Path containment hypothesis generation for a filename joined to an export root, with allowed baseline, nonexistent control and owned outside-root marker"),
]


def baseline():
    source = inspect.getsource(bundles.resolve_bundle)
    start = source.index('    with service.events.scope(component_id="component-1"):\n')
    end = source.index('    planning_rules = ', start)
    source = source[:start] + source[end:]
    namespace = dict(vars(bundles))
    exec(compile(source, "baseline_manual_bundle", "exec"), namespace)
    return namespace["resolve_bundle"]


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"]:
        raise RuntimeError("Frozen 160-request replay cap exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    return await ORIGINAL(client, *args, **kwargs)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "requests.json"
    if not path.exists():
        path.write_text(json.dumps({"requests": 0, "limit": 160}))
    frozen = ROOT / "source-hashes.json"
    hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
        str(BASE / "run.py"), "src/modules/techniques/bundles.py", "src/modules/techniques/service.py",
        "src/modules/techniques/selection_review.py",
    )}
    if frozen.exists() and json.loads(frozen.read_text()) != hashes:
        raise RuntimeError("Sources changed after freeze")
    frozen.write_text(json.dumps(hashes, indent=2))
    previous = baseline()
    with patch.object(ollama.AsyncClient, "chat", counted):
        for repetition in range(2):
            for index, (name, request) in enumerate(CASES):
                for arm in (("baseline", "candidate") if (index + repetition) % 2 == 0 else ("candidate", "baseline")):
                    folder = ROOT / f"{name}-{arm}-{repetition}"
                    folder.mkdir(exist_ok=True)
                    destination = folder / "outcome.json"
                    if destination.exists():
                        continue
                    before = json.loads(path.read_text())["requests"]
                    if before >= 160:
                        destination.write_text(json.dumps({"status": "unattempted_budget_exhausted"}))
                        continue
                    shutil.copytree(CATALOG, folder / "catalog")
                    events = []
                    with patch.dict(os.environ, {"QDRANT_URL": "", "CYBER_AGENT_OUTPUT_DIR": str(folder / "catalog")}):
                        service = build_service("ollama", "muse-glimmer:30b-mlx", str(folder / "catalog"), "web")
                    service.events.callback = events.append
                    result = {"case": name, "arm": arm, "repetition": repetition, "request": request}
                    try:
                        with patch.dict(os.environ, {"QDRANT_URL": "", "CYBER_AGENT_OUTPUT_DIR": str(folder / "catalog")}), patch.object(
                            bundles, "resolve_bundle", previous if arm == "baseline" else bundles.resolve_bundle,
                        ):
                            bundle = service.resolve_bundle(request, MethodologyContext(phase_contexts=["hypothesis_generation"]),
                                                            operation="ISOLATED_REUSE", task=f"{name}-{arm}-{repetition}")
                        result.update(status="completed", delivered=len(bundle.delivered), unresolved=len(bundle.unresolved),
                                      revisions=[item.revision_id for item in bundle.delivered])
                        (folder / "bundle.md").write_text(bundle.render())
                    except Exception as error:
                        result.update(status="unresolved", error=str(error))
                    finally:
                        service.store.qdrant.close()
                    result.update(requests=json.loads(path.read_text())["requests"] - before, events=events)
                    destination.write_text(json.dumps(result, indent=2))
                    print(name, arm, repetition, result["status"], result["requests"], flush=True)


if __name__ == "__main__":
    main()
