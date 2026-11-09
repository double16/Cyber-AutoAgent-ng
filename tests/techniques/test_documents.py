import hashlib
import json
from pathlib import Path

import pytest
import yaml

from modules.techniques.documents import CONTEXTS, Metadata, Source, TechniqueDocument, json_payload, revision_id

REPOSITORY = Path(__file__).resolve().parents[2]
WEB_TECHNIQUES = REPOSITORY / "src/modules/operation_plugins/web/techniques"


def test_document_is_self_contained_with_separate_source_identity(markdown):
    raw = markdown()
    doc = TechniqueDocument(raw)
    assert doc.sha256 == hashlib.sha256(raw.encode()).hexdigest()
    rendered = doc.render({"active": False, "sentiment_count": 3, "origin": "module:web"})
    metadata = yaml.safe_load(rendered.split("---")[1])
    assert metadata["sources"][0]["url"].startswith("https://")
    assert metadata["active"] is False
    assert metadata["sentiment_count"] == 3
    assert "Check assumptions" in rendered
    assert doc.sha256 == TechniqueDocument(raw).sha256
    assert revision_id("module:a:path", doc.sha256) != revision_id("module:b:path", doc.sha256)


@pytest.mark.parametrize("raw", ["plain", "---\ntitle: hello\n---\nBody", "x" * 48_001, "---\n{}\n---\n   "])
def test_reject_invalid_document(raw):
    with pytest.raises(ValueError):
        TechniqueDocument(raw)


def test_metadata_reject_unknown_fields_versions_and_bad_urls(markdown):
    values = TechniqueDocument(markdown()).metadata.model_dump()
    with pytest.raises(ValueError):
        Metadata.model_validate({**values, "schema_version": 2})
    with pytest.raises(ValueError):
        Metadata.model_validate({**values, "executable_detector": "eval"})
    with pytest.raises(ValueError):
        Source(url="file:///etc/passwd", title="Bad")


def test_json_preserves_native_values_and_exact_text():
    import json

    rows = [{"nested": {"array": [True, None, 42, {"text": "a,b; λ\n`x`"}]},
             "code": "print(\"a,b\")\n  # 日本語"}]
    assert json.loads(json_payload("sources", rows)) == {"sources": rows}
    assert json.loads(json_payload("sources", [])) == {"sources": []}


@pytest.mark.parametrize("text", [
    ("def build_multipart(field_name, filename, content_bytes, boundary=None):\n"
     "    return content_type, body\n"),
    "def broken(a; b):\n\treturn a; b",
    'quotes " and \\ and Unicode Ω\u2028,;\n\t',
    "",
])
def test_exact_json_payload(text):
    import json

    from modules.techniques.critique import ReviewSnapshot
    from modules.techniques.documents import json_payload

    value = [{"text": text, "nested": {"items": [text, None, False]}}]
    assert json.loads(json_payload("evidence", value)) == {"evidence": value}
    snapshot = ReviewSnapshot(text or "empty")
    rows = json.loads(snapshot.render())["draft_references"]
    assert {row["reference"]: row["value"] for row in rows} == snapshot.values


def test_python_survives_review_serialization():
    import ast
    import json

    from modules.techniques.critique import ReviewSnapshot

    original = "def build_multipart(field_name, filename, content_bytes, boundary=None):\n    return content_type, body"
    rows = json.loads(ReviewSnapshot(original).render())["draft_references"]
    decoded = "\n".join(row["value"] for row in rows)
    assert decoded == original
    ast.parse(decoded)
    with pytest.raises(SyntaxError):
        ast.parse(decoded.replace("field_name,", "field_name;"))


def test_optional_prompt_fitting_drops_records_without_cutting_nested_json():
    import json

    from modules.techniques.documents import optional_records_payload

    first = {"nested": [True, None, {"code": 'print("λ,;")\n  next()'}]}
    rows = [first, {"text": "large\n" * 1000}]
    original = json.dumps(rows, ensure_ascii=False)
    rendered = optional_records_payload("examples", rows, len(json_payload("examples", [first])))
    assert json.loads(rendered) == {"examples": [first]}
    assert json.dumps(rows, ensure_ascii=False) == original
    assert json.loads(optional_records_payload("examples", rows, 16)) == {"examples": []}
    with pytest.raises(ValueError, match="cannot fit"):
        optional_records_payload("examples", rows, 1)


@pytest.mark.parametrize("path", sorted(WEB_TECHNIQUES.glob("*.md")), ids=lambda p: p.name)
def test_web_library_phase_and_signal_metadata(path):
    from modules.operation_plugins.web.technique_signals import PROTOCOL_PATTERNS

    doc = TechniqueDocument(path.read_bytes().decode("utf-8"))
    phases = doc.metadata.phases
    assert len(phases) == len(set(phases))
    assert set(phases) <= set(CONTEXTS)
    assert doc.metadata.triggers
    assert {phase for trigger in doc.metadata.triggers for phase in trigger.phases} == set(phases)
    for trigger in doc.metadata.triggers:
        assert set(trigger.phases) <= set(phases)


def test_web_module_technique_catalog_has_valid_references():
    paths = sorted(WEB_TECHNIQUES.glob("*.md"))
    assert paths

    for path in paths:
        doc = TechniqueDocument(path.read_bytes().decode("utf-8"))
        assert doc.metadata.sources
        assert all(source.url.startswith("https://") for source in doc.metadata.sources)
        assert doc.metadata.title.strip()


def test_web_module_technique_documents_are_distinct_and_parseable():
    paths = sorted(WEB_TECHNIQUES.glob("*.md"))
    digests = {}

    for path in paths:
        raw = path.read_bytes()
        doc = TechniqueDocument(raw.decode("utf-8"))
        assert doc.sha256 == hashlib.sha256(raw).hexdigest()
        assert path.stem
        assert path.stem not in digests, f"duplicate technique filename: {path.stem}"
        digests[path.stem] = doc.sha256

    assert len(set(digests.values())) == len(digests)
