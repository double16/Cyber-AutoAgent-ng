"""Self-contained technique document contract and stable source identities."""

import hashlib
import json
import re
import uuid
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

NAMESPACE = uuid.UUID("3af79399-88c9-4c69-ac54-617df1e1351b")
CONTEXTS = ("reconnaissance", "hypothesis_generation", "vulnerability_verification", "execution", "analysis")


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    phases: list[str] = Field(min_length=1)
    when: str = Field(min_length=1, max_length=1000)
    request: str = Field(min_length=1, max_length=1000)
    signals_any: list[str] = Field(default_factory=list)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str
    title: str = Field(min_length=1)
    retrieved_at: str = ""
    selection_reason: str = ""

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not re.match(r"^https?://[^\s/]+", value):
            raise ValueError("Source URLs must use HTTP or HTTPS")
        return value


class Metadata(BaseModel):
    """Authored metadata; lifecycle keys are added by the controller when rendering."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: int = 1
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=2000)
    applicability: list[str] = Field(min_length=1)
    phases: list[str] = Field(min_length=1)
    triggers: list[Trigger] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    active: StrictBool = True
    source_coverage: str = "Module-authored guidance"

    @field_validator("schema_version")
    @classmethod
    def known_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Unsupported technique schema version")
        return value


class TechniqueDocument:
    def __init__(self, markdown: str) -> None:
        if len(markdown) > 48_000:
            raise ValueError("Technique exceeds the 48,000 character document limit")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.+)\Z", markdown, re.DOTALL)
        if match is None:
            raise ValueError("Technique requires YAML frontmatter and a Markdown body")
        self.metadata = Metadata.model_validate(yaml.safe_load(match[1]))
        self.body = match[2].strip()
        if not self.body:
            raise ValueError("Technique body is empty")
        self.markdown = markdown
        self.sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    def render(self, lifecycle: dict[str, Any]) -> str:
        metadata = self.metadata.model_dump()
        metadata.update(lifecycle)
        return f"---\n{yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)}---\n\n{self.body}\n"


def revision_id(lineage: str, digest: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{lineage}:{digest}"))


def json_payload(label: str, value: Any) -> str:
    """Serialize exact-text model data without punctuation or whitespace normalization."""
    return json.dumps({label: value}, ensure_ascii=False)


def optional_records_payload(label: str, rows: list[dict[str, Any]], max_chars: int) -> str:
    """Fit optional context by dropping whole records, preserving every retained native value."""
    retained = list(rows)
    while True:
        serialized = json_payload(label, retained)
        if len(serialized) <= max_chars:
            return serialized
        if not retained:
            raise ValueError("Technique input contract cannot fit the optional-record envelope")
        retained.pop()
