"""Fresh whole-corpus check of simplified delivery using the original frozen matcher leads."""

import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

from modules.techniques.cli import build_service

BASE = Path(__file__).parent
PRIOR = Path("outputs/technique_review_trials/automatic-matching-20261123")
ROOT = Path("outputs/technique_review_trials/automatic-matching-simplified-20261123")
spec = importlib.util.spec_from_file_location("matching_replay_boundary", BASE / "run.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def main():
    load_dotenv()
    ROOT.mkdir(parents=True, exist_ok=True)
    counter = ROOT / "requests.json"
    if not counter.exists():
        counter.write_text(json.dumps({"requests": 0, "limit": 160}))
    paths = ["src/modules/techniques/service.py", "src/modules/techniques/selection_review.py",
             "src/modules/techniques/delivery.py", str(BASE / "cases.json"), str(BASE / "simplified-protocol.json"),
             str(BASE / "replay_simplified.py")]
    hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}
    frozen = ROOT / "source-hashes.json"
    if frozen.exists() and json.loads(frozen.read_text()) != hashes:
        raise RuntimeError("Simplified replay sources changed after freezing")
    study.save(ROOT, "source-hashes.json", hashes)
    catalog = ROOT / "catalog"
    if not catalog.exists():
        shutil.copytree(PRIOR / "catalog", catalog)
    study.ROOT = ROOT
    with patch.dict(os.environ, {"QDRANT_URL": "", "CYBER_AGENT_OUTPUT_DIR": str(catalog)}), patch.object(
        ollama.AsyncClient, "chat", study.counted,
    ):
        service = build_service("ollama", study.MODEL, str(catalog), "web")
        try:
            for case in json.loads((BASE / "cases.json").read_text()):
                matched = json.loads((PRIOR / case["id"] / "matching.json").read_text())
                # Fresh receipt/cache scope prevents reuse of the copied catalog's historical deliveries.
                scope = {**case, "id": "simplified:" + case["id"]}
                study.deliver(service, scope, matched, ROOT / case["id"], "candidate")
        finally:
            service.store.qdrant.close()


if __name__ == "__main__":
    main()
