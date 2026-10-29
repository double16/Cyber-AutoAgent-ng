import hashlib

import pytest
import yaml

from modules.techniques.documents import Metadata, Source, TechniqueDocument, revision_id, toon


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


def test_toon_escapes_fields_and_empty_rows():
    assert toon("sources", []) == "sources[0]:"
    output = toon("sources", [{"url": "a,b", "title": "a\nb"}, {"url": "c", "title": "d"}])
    assert output.startswith("sources[2]{url,title}:")
    assert len(output.splitlines()) == 3


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
