"""Automatic matching and delivery diagnosis with shared ranked leads across arms."""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ollama
from dotenv import load_dotenv
from strands import Agent

from modules.config.models.factory import create_strands_model, require_prompt_token_limit
from modules.prompts.factory import get_module_loader
from modules.techniques.cli import build_service
from modules.techniques.context import methodology_context
from modules.techniques.delivery import MODE, AutomaticGuidanceHook, task_uses
from modules.techniques.documents import TechniqueDocument
from modules.techniques.triggers import RecommendationContext, signal_adapters

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/automatic-matching-20261123")
MODEL = "muse-glimmer:30b-mlx"
ORIGINAL = ollama.AsyncClient.chat
SCOPE = {"unit": "initialization", "start": 0, "limit": 4}


class OriginalDelivery(AutomaticGuidanceHook):
    """Previous decision path: first lead, methodology-only existing selection."""

    def _selection(self):
        if not self.state["attempted"]:
            leads = self.context.recommended()
            if not leads:
                return None
            self.state["attempted"] = True
            self._save()
            with self.service.events.scope(**self.context.event_context, delivery_mode=MODE):
                selected = self.service.select_existing(leads[0]["request"], methodology_context(self.context.lookup))
            if selected is not None:
                revision, _document, receipt = selected
                self.state["selection"] = {"revision_id": revision, "receipt": receipt}
            self._save()
            self._event("selected" if selected else "no_match")
        return self.state["selection"]


async def counted(client, *args, **kwargs):
    path = ROOT / "requests.json"
    counter = json.loads(path.read_text())
    if counter["requests"] >= counter["limit"] or counter["requests"] - SCOPE["start"] >= SCOPE["limit"]:
        raise RuntimeError("Automatic matching diagnostic request cap exhausted")
    counter["requests"] += 1
    path.write_text(json.dumps(counter))
    with (ROOT / "request-units.jsonl").open("a") as stream:
        stream.write(json.dumps({"sequence": counter["requests"], "unit": SCOPE["unit"],
                                 "native_schema": isinstance(kwargs.get("format"), dict)}) + "\n")
    return await ORIGINAL(client, *args, **kwargs)


def save(folder, name, value):
    (folder / name).write_text(json.dumps(value, indent=2, default=str))


def enter_unit(name, limit):
    count = json.loads((ROOT / "requests.json").read_text())["requests"]
    SCOPE.update(unit=name, start=count, limit=limit)
    return count


def match(service, case, folder):
    destination = folder / "matching.json"
    if destination.exists():
        return json.loads(destination.read_text())
    before = enter_unit(case["id"] + ":matching", 4)
    events, retrieval = [], []
    service.events.callback = events.append
    context = RecommendationContext(service, ["hypothesis_generation"], signal_adapters(get_module_loader(), "web"), folder / "recommendations.json")
    context.defer_matching = True
    context.event_context = {"operation_id": "ISOLATED_MATCH", "task_uid": case["id"] + ":matching"}
    context.lookup.purpose = case["objective"]
    context.lookup.subjects = [{"id": "assigned", "kind": case["kind"], "value": case["value"]}]
    context.lookup.constraints = ["Use recorded assigned observations; shared code requires an observed connection."]
    context.observe(case["assigned"], "artifact:assigned-source.txt", scope="subject")
    context.observe(case["shared"], "artifact:shared-bundle.txt", scope="shared")
    original_search = service.store.search_triggers

    def search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        retrieval.extend(result)
        return result

    try:
        with service.events.scope(**context.event_context), patch.object(service.store, "search_triggers", search):
            context.flush()
        row = {"status": context.state.get("batch_match", {}).get("status"),
               "leads": context.recommended(), "lookup": context.lookup.model_dump(),
               "retrieved": retrieval, "events": events}
    except Exception as error:
        row = {"status": "invalid", "error": str(error), "leads": [], "lookup": context.lookup.model_dump(),
               "retrieved": retrieval, "events": events}
    row["requests"] = json.loads((ROOT / "requests.json").read_text())["requests"] - before
    save(folder, "matching.json", row)
    return row


def deliver(service, case, matched, folder, arm):
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "outcome.json").exists():
        return
    if matched["status"] not in {"completed", "empty"}:
        save(folder, "outcome.json", {"case": case["id"], "arm": arm, "status": "matching_unavailable", "requests": 0})
        return
    before = enter_unit(case["id"] + ":" + arm, 30)
    events = []
    service.events.callback = events.append
    context = RecommendationContext(service, ["hypothesis_generation"], [], folder / "recommendations.json")
    context.lookup = context.lookup.model_validate(matched["lookup"])
    context.state["recommendations"] = copy.deepcopy(matched["leads"])
    context.event_context = {"operation_id": "ISOLATED_MATCH", "task_uid": case["id"] + ":" + arm}
    agent = Agent(model=create_strands_model("ollama", MODEL, "task_executor"), tools=[],
                  system_prompt="Assess the assigned observation using available advisory methodology.",
                  callback_handler=None, load_tools_from_directory=False)
    agent._prompt_token_limit = require_prompt_token_limit("ollama", MODEL)
    agent.messages = [{"role": "user", "content": [{"text": case["objective"]}]}]
    hook = (OriginalDelivery if arm == "baseline" else AutomaticGuidanceHook)(context)
    hook.before_model(SimpleNamespace(agent=agent))
    uses = task_uses(service, "ISOLATED_MATCH", context.event_context["task_uid"])
    revision = uses[0]["revision_id"] if uses else None
    metadata = service.store.document(revision).metadata.model_dump() if revision else None
    row = {"case": case["id"], "arm": arm, "status": "finished", "revision": revision,
           "metadata": metadata, "events": events, "delivery_count": len(uses),
           "requests": json.loads((ROOT / "requests.json").read_text())["requests"] - before}
    save(folder, "delivered-messages.json", agent.messages)
    save(folder, "outcome.json", row)
    print(case["id"], arm, metadata["title"] if metadata else "no delivery", row["requests"], flush=True)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "requests.json"
    if not path.exists():
        path.write_text(json.dumps({"requests": 0, "limit": 512}))
    hashes = {name: hashlib.sha256((BASE / name).read_bytes()).hexdigest() for name in (
        "run.py", "cases.json", "protocol.json", "captured-authentication.md", "captured-ssrf.md",
    )}
    frozen = ROOT / "source-hashes.json"
    if frozen.exists() and json.loads(frozen.read_text()) != hashes:
        raise RuntimeError("Study sources changed after freeze")
    save(ROOT, "source-hashes.json", hashes)
    with patch.dict(os.environ, {"QDRANT_URL": "", "CYBER_AGENT_OUTPUT_DIR": str(ROOT / "catalog")}), patch.object(
        ollama.AsyncClient, "chat", counted,
    ):
        service = build_service("ollama", MODEL, str(ROOT / "catalog"), "web")
        try:
            for name in ("captured-authentication.md", "captured-ssrf.md"):
                service.store.publish(TechniqueDocument((BASE / name).read_text()), "isolated-capture:" + name, "generated")
            catalog = service.repository.revisions()
            snapshot = ROOT / "catalog-snapshot.json"
            if snapshot.exists() and json.loads(snapshot.read_text()) != catalog:
                raise RuntimeError("Catalog changed after freeze")
            save(ROOT, "catalog-snapshot.json", catalog)
            for index, case in enumerate(json.loads((BASE / "cases.json").read_text())):
                folder = ROOT / case["id"]
                folder.mkdir(exist_ok=True)
                matched = match(service, case, folder)
                for arm in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
                    deliver(service, case, matched, folder / arm, arm)
        finally:
            service.store.qdrant.close()


if __name__ == "__main__":
    main()
