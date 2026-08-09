"""Versioned CWE and MITRE ATT&CK catalog support for report enrichment.

The catalog is deliberately an authority for identifiers and names, not a source
of operation evidence.  A report agent may propose an identifier, but this
module is the only component allowed to accept it for rendering.
"""

import json
import os
import re
import tempfile
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from modules.config.system.logger import get_logger

logger = get_logger("Config.TaxonomyCatalog")

_CATALOG_DIR = Path(__file__).with_name("taxonomy")
_SOURCES = {
    "cwe": "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip",
    "attack": "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack.json",
}
TAXONOMY_CONFIDENCE_THRESHOLD = 0.75
_TAXONOMY_KINDS = (("cwe", "cwe"), ("mitre_attack", "attack"))
_TECHNIQUE_TAXONOMY_SEEDS = {
    "command injection": {
        "cwe": ("CWE-78", "CWE-77", "CWE-88"),
        "attack": ("T1190", "T1059.004", "T1059"),
    },
    "os command injection": {
        "cwe": ("CWE-78", "CWE-77", "CWE-88"),
        "attack": ("T1190", "T1059.004", "T1059"),
    },
    "sql injection": {
        "cwe": ("CWE-89",),
        "attack": (),
    },
    "stored xss": {
        "cwe": ("CWE-79",),
        "attack": (),
    },
    "cross site scripting": {
        "cwe": ("CWE-79",),
        "attack": (),
    },
    "path traversal": {
        "cwe": ("CWE-22",),
        "attack": (),
    },
    "directory traversal": {
        "cwe": ("CWE-22",),
        "attack": (),
    },
    "local file inclusion": {
        "cwe": ("CWE-98",),
        "attack": (),
    },
    "remote file inclusion": {
        "cwe": ("CWE-98",),
        "attack": (),
    },
    "server side request forgery": {
        "cwe": ("CWE-918",),
        "attack": (),
    },
    "ssrf": {
        "cwe": ("CWE-918",),
        "attack": (),
    },
    "xml external entity": {
        "cwe": ("CWE-611",),
        "attack": (),
    },
    "xxe": {
        "cwe": ("CWE-611",),
        "attack": (),
    },
    "cross site request forgery": {
        "cwe": ("CWE-352",),
        "attack": (),
    },
    "csrf": {
        "cwe": ("CWE-352",),
        "attack": (),
    },
    "idor": {
        "cwe": ("CWE-639",),
        "attack": (),
    },
    "broken object level authorization": {
        "cwe": ("CWE-639",),
        "attack": (),
    },
    "server side template injection": {
        "cwe": ("CWE-1336",),
        "attack": (),
    },
    "ssti": {
        "cwe": ("CWE-1336",),
        "attack": (),
    },
    "unsafe deserialization": {
        "cwe": ("CWE-502",),
        "attack": (),
    },
    "unrestricted file upload": {
        "cwe": ("CWE-434",),
        "attack": (),
    },
    "open redirect": {
        "cwe": ("CWE-601",),
        "attack": (),
    },
    "ldap injection": {
        "cwe": ("CWE-90",),
        "attack": (),
    },
    "xpath injection": {
        "cwe": ("CWE-643",),
        "attack": (),
    },
    "nosql injection": {
        "cwe": ("CWE-943",),
        "attack": (),
    },
    "prototype pollution": {
        "cwe": ("CWE-1321",),
        "attack": (),
    },
}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class TaxonomyCatalog:
    """Load normalized taxonomy snapshots with a non-blocking refresh fallback."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        configured = os.getenv("CYBER_TAXONOMY_CACHE_DIR", "").strip()
        self.cache_dir = cache_dir or (Path(configured) if configured else Path.home() / ".cache" / "cyber-autoagent")
        self.cache_file = self.cache_dir / "taxonomy_catalog.json"
        self.snapshot_file = _CATALOG_DIR / "taxonomy_snapshot.json"
        self._data: Optional[Dict[str, Any]] = None
        self._source = "unknown"

    def get_data(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        cached = self._read(self.cache_file)
        if cached and self._is_fresh(self.cache_file):
            self._data, self._source = cached, "cache"
            return cached
        if not _env_bool("CYBER_TAXONOMY_OFFLINE") and _env_bool("CYBER_TAXONOMY_REFRESH", True):
            refreshed = self._refresh()
            if refreshed:
                self._data, self._source = refreshed, "refresh"
                return refreshed
        snapshot = self._read(self.snapshot_file)
        if snapshot:
            self._data, self._source = snapshot, "snapshot"
            return snapshot
        if cached:
            self._data, self._source = cached, "stale_cache"
            return cached
        self._data = {"version": "unavailable", "cwe": [], "attack": []}
        return self._data

    def provenance(self) -> Dict[str, Any]:
        data = self.get_data()
        configured_url = os.getenv("CYBER_TAXONOMY_CATALOG_URL", "").strip()
        refresh_urls = [configured_url] if configured_url else [_SOURCES["cwe"], _SOURCES["attack"]]
        return {
            "source": self._source,
            "version": data.get("version", "unknown"),
            "retrieved_at": data.get("retrieved_at"),
            "configured_refresh_urls": refresh_urls,
        }

    def candidates(self, finding: Dict[str, Any], kind: str, limit: int = 12) -> List[Dict[str, Any]]:
        """Return relevant catalog candidates, favoring high-signal technique labels."""
        data = self.get_data()
        records = data.get(kind, []) if kind in {"cwe", "attack"} else []
        text = " ".join(str(value or "") for value in (
            finding.get("title"), finding.get("content"), finding.get("parsed", {}).get("vulnerability") if isinstance(finding.get("parsed"), dict) else "",
            finding.get("parsed", {}).get("evidence") if isinstance(finding.get("parsed"), dict) else "",
            finding.get("metadata", {}).get("technique") if isinstance(finding.get("metadata"), dict) else "",
        )).lower()
        normalized_text = re.sub(r"[^a-z0-9]+", " ", text.replace("_", " ")).strip()
        tokens = {token for token in normalized_text.split() if len(token) >= 4}
        seeded_scores = {
            identifier: max(
                100 - position
                for position, candidate_id in enumerate(mappings.get(kind, ()))
                if candidate_id == identifier
            )
            for phrase, mappings in _TECHNIQUE_TAXONOMY_SEEDS.items()
            if phrase in normalized_text
            for identifier in mappings.get(kind, ())
        }
        scored = []
        for record in records:
            identifier = str(record.get("id", "")).upper()
            haystack = " ".join(str(record.get(key, "")) for key in ("name", "description", "keywords")).lower()
            score = sum(token in haystack for token in tokens)
            score += seeded_scores.get(identifier, 0)
            if score:
                scored.append((score, identifier, record))
        return [record for _score, _identifier, record in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]

    def get(self, kind: str, identifier: str) -> Optional[Dict[str, Any]]:
        normalized = str(identifier or "").strip().upper()
        for record in self.get_data().get(kind, []):
            if str(record.get("id", "")).upper() == normalized:
                return record
        return None

    def _is_fresh(self, path: Path) -> bool:
        try:
            ttl_days = max(1, int(os.getenv("CYBER_TAXONOMY_REFRESH_DAYS", "30")))
            return time.time() - path.stat().st_mtime < ttl_days * 24 * 3600
        except OSError:
            return False

    @staticmethod
    def _read(path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("cwe"), list) and isinstance(data.get("attack"), list):
                return data
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _refresh(self) -> Optional[Dict[str, Any]]:
        """Refresh official catalogs or a supplied normalized mirror without blocking reports."""
        url = os.getenv("CYBER_TAXONOMY_CATALOG_URL", "").strip()
        source = url or _SOURCES["cwe"]
        try:
            import httpx

            if url:
                response = httpx.get(url, timeout=20.0, follow_redirects=True)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("cwe"), list) or not isinstance(data.get("attack"), list):
                    raise ValueError("catalog response must contain normalized cwe and attack lists")
            else:
                # Reporting must remain usable on isolated assessment networks.
                source = _SOURCES["cwe"]
                cwe_response = httpx.get(_SOURCES["cwe"], timeout=5.0, follow_redirects=True)
                attack_response = httpx.get(_SOURCES["attack"], timeout=5.0, follow_redirects=True)
                cwe_response.raise_for_status()
                source = _SOURCES["attack"]
                attack_response.raise_for_status()
                data = {
                    "version": "official-refresh",
                    "cwe": self._normalize_cwe(cwe_response.content),
                    "attack": self._normalize_attack(attack_response.json()),
                }
                if not data["cwe"] or not data["attack"]:
                    raise ValueError("official catalog refresh contained no normalized records")
            data.setdefault("retrieved_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.cache_dir, delete=False) as temporary:
                json.dump(data, temporary, indent=2, sort_keys=True)
                temporary_path = Path(temporary.name)
            temporary_path.replace(self.cache_file)
            return data
        except Exception as error:
            logger.warning("Unable to refresh taxonomy catalog from %s: %s", source, error)
            return None

    def refresh_snapshot(self) -> Optional[Dict[str, Any]]:
        """Refresh authoritative data and atomically replace the bundled fallback snapshot."""
        data = self._refresh()
        if not data:
            return None
        self.snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.snapshot_file.parent, delete=False) as temporary:
            json.dump(data, temporary, indent=2, sort_keys=True)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.snapshot_file)
        return data

    @staticmethod
    def _normalize_cwe(payload: bytes) -> List[Dict[str, Any]]:
        """Normalize CWE's published XML ZIP without retaining its large source schema."""
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if not xml_names:
                raise ValueError("CWE ZIP has no XML payload")
            source = ElementTree.fromstring(archive.read(xml_names[0]))

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def child_text(element: ElementTree.Element, name: str) -> str:
            for child in element.iter():
                if local_name(child.tag) == name:
                    return " ".join("".join(child.itertext()).split())
            return ""

        records = []
        for item in source.iter():
            if local_name(item.tag) != "Weakness":
                continue
            identifier = str(item.attrib.get("ID") or "").strip()
            if not identifier.isdigit():
                continue
            name = str(item.attrib.get("Name") or "").strip()
            description = child_text(item, "Description")
            status = str(item.attrib.get("Status") or "").lower()
            records.append({
                "id": f"CWE-{identifier}", "name": name, "description": description,
                "keywords": f"{name} {description}",
                "url": f"https://cwe.mitre.org/data/definitions/{identifier}.html",
                "deprecated": status in {"deprecated", "obsolete"},
            })
        return records

    @staticmethod
    def _normalize_attack(source: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize Enterprise ATT&CK STIX attack-pattern objects."""
        records = []
        for item in source.get("objects", []) if isinstance(source, dict) else []:
            if not isinstance(item, dict) or item.get("type") != "attack-pattern":
                continue
            reference = next(
                (ref for ref in item.get("external_references", []) if ref.get("source_name") == "mitre-attack"),
                {},
            )
            identifier = str(reference.get("external_id") or "").upper()
            if not re.fullmatch(r"T\d{4}(?:\.\d{3})?", identifier):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            records.append({
                "id": identifier, "name": name, "description": description,
                "keywords": f"{name} {description}",
                "url": str(reference.get("url") or f"https://attack.mitre.org/techniques/{identifier.replace('.', '/')}/"),
                "deprecated": bool(item.get("revoked") or item.get("x_mitre_deprecated")),
            })
        return records


_catalog: Optional[TaxonomyCatalog] = None


def get_taxonomy_catalog() -> TaxonomyCatalog:
    """Return the process-local taxonomy catalog."""
    global _catalog
    if _catalog is None:
        _catalog = TaxonomyCatalog()
    return _catalog


def validate_taxonomy_mappings(
    cwe_mappings: Any,
    mitre_attack_mappings: Any,
    artifacts: List[str],
    *,
    disallowed_attack_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Validate model-proposed finding mappings against catalog records and evidence.

    The result is safe to persist with a finding candidate.  Catalog data supplies
    names and URLs; the model may supply only an identifier, confidence, rationale,
    and references to the finding's already-validated artifacts.
    """
    if cwe_mappings is None and mitre_attack_mappings is None:
        return {"cwe": [], "mitre_attack": [], "provenance": {}}
    catalog = get_taxonomy_catalog()
    disallowed_attack_ids = {str(identifier).upper() for identifier in disallowed_attack_ids or set()}
    allowed_artifacts = set(artifacts)
    normalized: Dict[str, Any] = {
        "cwe": [],
        "mitre_attack": [],
        "provenance": catalog.provenance(),
    }
    for output_key, catalog_key in _TAXONOMY_KINDS:
        proposed = cwe_mappings if output_key == "cwe" else mitre_attack_mappings
        if proposed is None:
            continue
        if not isinstance(proposed, list):
            raise ValueError(f"{output_key}_mappings must be a list")
        seen: set[str] = set()
        for mapping in proposed:
            if not isinstance(mapping, dict):
                raise ValueError(f"each {output_key} mapping must be an object")
            identifier = str(mapping.get("id") or "").strip().upper()
            if not identifier:
                raise ValueError(f"each {output_key} mapping requires an id")
            if output_key == "mitre_attack" and identifier in disallowed_attack_ids:
                raise ValueError(f"{identifier} is not eligible for this finding's preflight target context")
            if identifier in seen:
                continue
            record = catalog.get(catalog_key, identifier)
            if not record or record.get("deprecated"):
                raise ValueError(f"unknown or deprecated {output_key} identifier: {identifier}")
            confidence = mapping.get("confidence")
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0.0 <= float(confidence) <= 1.0
            ):
                raise ValueError(f"{output_key} confidence must be a number from 0.0 to 1.0")
            if float(confidence) < TAXONOMY_CONFIDENCE_THRESHOLD:
                raise ValueError(
                    f"{output_key} confidence must be at least {TAXONOMY_CONFIDENCE_THRESHOLD:.2f}"
                )
            rationale = str(mapping.get("rationale") or "").strip()
            if not rationale:
                raise ValueError(f"{output_key} mapping rationale is required")
            evidence = mapping.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"{output_key} mapping evidence requires at least one artifact reference")
            evidence = [str(reference).strip() for reference in evidence]
            if any(reference not in allowed_artifacts for reference in evidence):
                raise ValueError(f"{output_key} mapping evidence must reference an artifact passed to store_finding")
            normalized[output_key].append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "url": record["url"],
                    "confidence": float(confidence),
                    "confidence_band": "High" if float(confidence) >= 0.90 else "Moderate",
                    "basis": "Inferred",
                    "rationale": rationale,
                    "evidence": list(dict.fromkeys(evidence)),
                }
            )
            seen.add(identifier)
            if len(normalized[output_key]) == 3:
                break
    return normalized
