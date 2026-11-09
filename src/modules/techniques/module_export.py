"""Write authored technique documents into a selected module without replacing files."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from modules.techniques.documents import CONTEXTS, TechniqueDocument


def export_bundle(store, bundle, module_dir: Path, phases: list[str]) -> list[Path]:
    root = module_dir.resolve()
    destination = root / "techniques"
    destination.mkdir(exist_ok=True)
    if not destination.resolve().is_relative_to(root):
        raise ValueError("Technique directory escapes the selected module")
    prefix = "+".join(phase.replace("_", "-") for phase in CONTEXTS if phase in phases)
    paths = []
    seen = set()
    for item in bundle.delivered:
        if item.revision_id in seen:
            continue
        seen.add(item.revision_id)
        document = store.document(item.revision_id)
        data = document.markdown.encode("utf-8")
        TechniqueDocument(data.decode("utf-8"))
        slug = re.sub(r"[^a-z0-9]+", "-", document.metadata.title.lower()).strip("-")[:120].rstrip("-")
        stem = (prefix + "--" if prefix else "") + (slug or "technique")
        digest = hashlib.sha256(data).hexdigest()
        candidates = [stem] + [f"{stem}-{digest[:length]}" for length in range(12, 65, 4)]
        for candidate in candidates:
            target = destination / f"{candidate}.md"
            if target.is_symlink():
                raise ValueError(f"Refusing technique symlink: {target}")
            if target.exists():
                if target.read_bytes() == data:
                    paths.append(target)
                    break
                continue
            descriptor, temporary = tempfile.mkstemp(dir=destination, prefix=".technique-")
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if target.is_symlink():
                        raise ValueError(f"Refusing technique symlink: {target}") from None
                    if target.read_bytes() != data:
                        continue
                paths.append(target)
                break
            finally:
                Path(temporary).unlink()
        else:
            raise ValueError(f"No available technique filename for {stem}")
    return paths
