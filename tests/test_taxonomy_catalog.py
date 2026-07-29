import json
import os
import time
import zipfile
from io import BytesIO

import httpx
import pytest

from modules.config.taxonomy_catalog import TaxonomyCatalog, validate_taxonomy_mappings
from modules.handlers.report_generator import _format_taxonomy_mappings


def _catalog_payload():
    return {
        "version": "test-v1",
        "retrieved_at": "2026-07-28T00:00:00Z",
        "cwe": [{"id": "CWE-79", "name": "XSS", "url": "https://cwe.test/79", "deprecated": False, "keywords": "xss"}],
        "attack": [{"id": "T1190", "name": "Exploit Public-Facing Application", "url": "https://attack.test/T1190", "deprecated": False, "keywords": "exploit web"}],
    }


def test_catalog_uses_fresh_cache_before_snapshot(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "taxonomy_catalog.json").write_text(json.dumps(_catalog_payload()), encoding="utf-8")
    monkeypatch.setenv("CYBER_TAXONOMY_OFFLINE", "true")

    catalog = TaxonomyCatalog(cache_dir=cache_dir)

    assert catalog.get("cwe", "cwe-79")["name"] == "XSS"
    assert catalog.provenance()["source"] == "cache"


def test_catalog_uses_snapshot_when_cache_is_stale(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "taxonomy_catalog.json"
    cache_file.write_text(json.dumps(_catalog_payload()), encoding="utf-8")
    old = time.time() - 31 * 24 * 3600
    os.utime(cache_file, (old, old))
    monkeypatch.setenv("CYBER_TAXONOMY_OFFLINE", "true")

    catalog = TaxonomyCatalog(cache_dir=cache_dir)

    assert catalog.provenance()["source"] == "snapshot"


def test_catalog_defaults_to_a_thirty_day_cache_ttl(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "taxonomy_catalog.json"
    cache_file.write_text(json.dumps(_catalog_payload()), encoding="utf-8")
    age = time.time() - 29 * 24 * 3600
    os.utime(cache_file, (age, age))
    monkeypatch.delenv("CYBER_TAXONOMY_REFRESH_DAYS", raising=False)
    monkeypatch.setenv("CYBER_TAXONOMY_OFFLINE", "true")

    assert TaxonomyCatalog(cache_dir=cache_dir).provenance()["source"] == "cache"


def test_normalize_cwe_reads_published_xml_zip():
    xml = b'''<?xml version="1.0"?><Weakness_Catalog xmlns="http://cwe.mitre.org/cwe-7"><Weaknesses>
    <Weakness ID="79" Name="Improper Neutralization of Input During Web Page Generation" Status="Draft">
      <Description><Description_Summary>Cross-site scripting weakness.</Description_Summary></Description>
    </Weakness></Weaknesses></Weakness_Catalog>'''
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("cwec_v4.20.xml", xml)

    records = TaxonomyCatalog._normalize_cwe(payload.getvalue())

    assert records == [{
        "id": "CWE-79",
        "name": "Improper Neutralization of Input During Web Page Generation",
        "description": "Cross-site scripting weakness.",
        "keywords": "Improper Neutralization of Input During Web Page Generation Cross-site scripting weakness.",
        "url": "https://cwe.mitre.org/data/definitions/79.html",
        "deprecated": False,
    }]


def test_refresh_logs_the_cwe_source_when_cwe_request_fails(monkeypatch, tmp_path, caplog):
    response = httpx.Response(404, request=httpx.Request("GET", "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip"))

    def get_cwe_only(url, **_kwargs):
        assert url == "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip"
        return response

    monkeypatch.setattr(httpx, "get", get_cwe_only)

    with caplog.at_level("WARNING"):
        assert TaxonomyCatalog(cache_dir=tmp_path)._refresh() is None

    assert "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip" in caplog.text


def test_taxonomy_validation_rejects_unknown_low_and_ungrounded_inferred_candidates(monkeypatch):
    class Catalog:
        def provenance(self):
            return {"source": "test", "version": "v1"}

        def get(self, kind, identifier):
            records = _catalog_payload()["cwe" if kind == "cwe" else "attack"]
            return next((record for record in records if record["id"] == identifier), None)

    monkeypatch.setattr("modules.config.taxonomy_catalog.get_taxonomy_catalog", lambda: Catalog())
    with pytest.raises(ValueError, match="at least 0.75"):
        validate_taxonomy_mappings(
            [{"id": "CWE-79", "confidence": 0.74, "rationale": "XSS", "evidence": ["artifact:artifacts/proof.txt"]}],
            None,
            ["artifact:artifacts/proof.txt"],
        )
    with pytest.raises(ValueError, match="unknown or deprecated"):
        validate_taxonomy_mappings(
            [{"id": "CWE-999", "confidence": 0.99, "rationale": "No", "evidence": ["artifact:artifacts/proof.txt"]}],
            None,
            ["artifact:artifacts/proof.txt"],
        )
    with pytest.raises(ValueError, match="must reference an artifact"):
        validate_taxonomy_mappings(
            None,
            [{"id": "T1190", "confidence": 0.80, "rationale": "Exploit proof", "evidence": ["artifact:artifacts/not-proof.txt"]}],
            ["artifact:artifacts/proof.txt"],
        )


def test_taxonomy_validation_keeps_confident_grounded_inferences(monkeypatch):
    class Catalog:
        def provenance(self):
            return {"source": "test", "version": "v1"}

        def get(self, kind, identifier):
            records = _catalog_payload()["cwe" if kind == "cwe" else "attack"]
            return next((record for record in records if record["id"] == identifier), None)

    monkeypatch.setattr("modules.config.taxonomy_catalog.get_taxonomy_catalog", lambda: Catalog())
    result = validate_taxonomy_mappings(
        [{"id": "CWE-79", "confidence": 0.95, "rationale": "Stored XSS evidence", "evidence": ["artifact:artifacts/proof.txt"]}],
        [{"id": "T1190", "confidence": 0.81, "rationale": "Observed exploit", "evidence": ["artifact:artifacts/proof.txt"]}],
        ["artifact:artifacts/proof.txt"],
    )

    assert result["cwe"][0]["basis"] == "Inferred"
    assert result["mitre_attack"][0]["confidence_band"] == "Moderate"
    rendered = _format_taxonomy_mappings(result)
    assert "CWE-79" in rendered
    assert "T1190" in rendered
    assert "[Catalog](https://attack.test/T1190)" in rendered
