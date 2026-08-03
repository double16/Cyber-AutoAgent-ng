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


def test_catalog_ranks_command_injection_specific_taxonomy_before_generic_matches(tmp_path):
    catalog = TaxonomyCatalog(cache_dir=tmp_path)
    catalog._data = {
        "version": "test-v1",
        "cwe": [
            {"id": "CWE-77", "name": "Command Injection", "description": "generic command injection"},
            {"id": "CWE-78", "name": "OS Command Injection", "description": "OS command execution"},
            {"id": "CWE-88", "name": "Argument Injection", "description": "argument delimiter injection"},
            {"id": "CWE-999", "name": "Unrelated", "description": "command execution injection"},
        ],
        "attack": [
            {"id": "T1055", "name": "Process Injection", "description": "injection"},
            {"id": "T1059", "name": "Command and Scripting Interpreter", "description": "command execution"},
            {"id": "T1059.004", "name": "Unix Shell", "description": "shell command execution"},
            {"id": "T1190", "name": "Exploit Public-Facing Application", "description": "exploit application"},
        ],
    }
    finding = {"title": "Command Injection", "metadata": {"technique": "command_injection"}}

    assert [item["id"] for item in catalog.candidates(finding, "cwe", limit=3)] == [
        "CWE-78",
        "CWE-77",
        "CWE-88",
    ]
    assert [item["id"] for item in catalog.candidates(finding, "attack", limit=3)] == [
        "T1059.004",
        "T1190",
        "T1059",
    ]


@pytest.mark.parametrize(
    ("technique", "expected"),
    [
        ("sql_injection", "CWE-89"),
        ("stored_xss", "CWE-79"),
        ("path_traversal", "CWE-22"),
        ("local_file_inclusion", "CWE-98"),
        ("ssrf", "CWE-918"),
        ("xxe", "CWE-611"),
        ("csrf", "CWE-352"),
        ("idor", "CWE-639"),
        ("ssti", "CWE-1336"),
        ("unsafe_deserialization", "CWE-502"),
        ("unrestricted_file_upload", "CWE-434"),
        ("open_redirect", "CWE-601"),
        ("ldap_injection", "CWE-90"),
        ("xpath_injection", "CWE-643"),
        ("nosql_injection", "CWE-943"),
        ("prototype_pollution", "CWE-1321"),
    ],
)
def test_catalog_seeds_common_injection_and_xss_weaknesses(tmp_path, technique, expected):
    catalog = TaxonomyCatalog(cache_dir=tmp_path)
    catalog._data = {
        "version": "test-v1",
        "cwe": [
            {"id": expected, "name": technique, "description": technique},
            {"id": "CWE-999", "name": "Unrelated", "description": technique},
        ],
        "attack": [],
    }

    candidates = catalog.candidates(
        {"title": technique, "metadata": {"technique": technique}},
        "cwe",
        limit=1,
    )

    assert candidates[0]["id"] == expected


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("directory traversal", "CWE-22"),
        ("local file inclusion", "CWE-98"),
        ("remote file inclusion", "CWE-98"),
        ("server-side request forgery", "CWE-918"),
        ("XML external entity", "CWE-611"),
        ("cross-site request forgery", "CWE-352"),
        ("broken object level authorization", "CWE-639"),
        ("server-side template injection", "CWE-1336"),
    ],
)
def test_catalog_seeds_common_vulnerability_aliases(tmp_path, phrase, expected):
    catalog = TaxonomyCatalog(cache_dir=tmp_path)
    catalog._data = {
        "version": "test-v1",
        "cwe": [
            {"id": expected, "name": phrase, "description": phrase},
            {"id": "CWE-999", "name": "Unrelated", "description": phrase},
        ],
        "attack": [],
    }

    candidates = catalog.candidates({"title": phrase}, "cwe", limit=1)

    assert candidates[0]["id"] == expected


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
