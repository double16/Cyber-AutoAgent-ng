"""Validated retrieval settings and comparable search representations."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from modules.techniques.documents import TechniqueDocument


class RetrievalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_limit: int = Field(default=10, ge=1, le=50)
    trigger_limit: int = Field(default=20, ge=1, le=50)
    representation: Literal["full", "summary"] = "full"

    @classmethod
    def from_environment(cls):
        return cls(**{
            field: os.environ[name]
            for field, name in {
                "document_limit": "TECHNIQUE_DOCUMENT_LIMIT",
                "trigger_limit": "TECHNIQUE_TRIGGER_LIMIT",
                "representation": "TECHNIQUE_SEARCH_REPRESENTATION",
            }.items() if name in os.environ
        })


def search_text(document: TechniqueDocument, representation: str) -> str:
    if representation == "full":
        return document.markdown
    if representation != "summary":
        raise ValueError("Unknown technique search representation")
    metadata = document.metadata
    return "\n".join([
        metadata.title, metadata.purpose, *metadata.applicability,
        *[f"{trigger.when}\n{trigger.request}" for trigger in metadata.triggers],
    ])
