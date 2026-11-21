"""Isolated generation from frozen sources; no live entry point or implicit model calls."""

import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from qdrant_client import QdrantClient

from modules.techniques.coverage import Requirement
from modules.techniques.debug import TechniqueExports
from modules.techniques.repository import TechniqueRepository
from modules.techniques.service import TechniqueService
from modules.techniques.storage import TechniqueStore


class FrozenResearch:
    """Offer retained inputs once; never manufacture additional research evidence."""

    def __init__(self, sources):
        self.sources = copy.deepcopy(sources)

    def batch(self, request, excluded):
        return copy.deepcopy([row for row in self.sources if row["url"] not in excluded])


class EmptyIndexEmbeddings:
    """Local index bookkeeping only; each request starts with an empty isolated store."""

    def embed_query(self, text):
        return [1.0, 0.0]


def run_generation(case, directory, runner, budget, *, transport=None, diagnostics=None):
    """Use production alignment, assessment, authoring and repair owners on one fixed request."""
    if set(case) - {"id", "family", "request", "requirements", "sources", "input_sha256"}:
        raise ValueError("Generation inputs must not contain drafts, labels or unknown fields")
    payload = {key: value for key, value in case.items() if key != "input_sha256"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if digest != case["input_sha256"]:
        raise ValueError("Frozen generation input hash mismatch")
    requirements = [Requirement.model_validate(row) for row in case["requirements"]]
    if not requirements or any("kind" not in row for row in case["requirements"]):
        raise ValueError("Generation requirements must declare kinds")
    root = Path(directory)
    root.mkdir(mode=0o700)
    client = QdrantClient(":memory:")
    repository = TechniqueRepository(str(root / "pilot.db"))
    events = []

    def invoke(*args, **kwargs):
        budget.reserve("roles")
        return runner.structured(*args, **kwargs)

    def invoke_legacy(*args):
        budget.reserve("roles")
        return runner(*args)

    adapter = SimpleNamespace(structured=invoke) if callable(getattr(runner, "structured", None)) else invoke_legacy
    try:
        if diagnostics is not None:
            diagnostics.open(str(root))
        store = TechniqueStore(client, EmptyIndexEmbeddings(), {"embedding_dimensions": 2,
                               "embedding_model": "isolated-pilot-index"}, repository)
        service = TechniqueService(store, adapter, FrozenResearch(case["sources"]))
        service.exports = TechniqueExports(str(root))
        def record(event):
            events.append(event)
            if diagnostics is not None:
                diagnostics.lifecycle(event)

        service.events.callback = record
        try:
            instrumentation = budget.instrument(*transport, root) if transport is not None else nullcontext()
            with instrumentation:
                revision, document = service.get(case["request"], requirements=requirements)
            path = root / "generated.md"
            path.write_text(document, encoding="utf-8")
            outcome = {"status": "generated_pending_human_adjudication", "revision": revision,
                       "document_sha256": hashlib.sha256(document.encode()).hexdigest()}
        except Exception as error:
            outcome = {"status": "unresolved", "error_type": type(error).__name__, "error": str(error)}
        outcome.update(case=case["id"], input_sha256=digest, counts=budget.counts(), human_adjudication="pending")
        (root / "outcome.json").write_text(json.dumps(outcome, indent=2))
        (root / "events.json").write_text(json.dumps(events, indent=2, default=str))
        return outcome
    finally:
        if diagnostics is not None:
            diagnostics.close()
        client.close()
