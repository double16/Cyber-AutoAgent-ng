"""Module-owned, advisory research source preferences."""

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


class SourcePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["append", "replace"] = "append"
    sites: list[str] = Field(default_factory=list)


def resolve_sources(loader, module: str, *, contributors: list[str] | None = None) -> list[str]:
    """Merge each inheritance branch independently, preserving declared order."""

    def visit(name, stack):
        if name in stack:
            return []
        root = loader._find_module_dir(name)
        if root is None:
            return []
        inherited = []
        for parent in loader._get_extend_list(root):
            inherited.extend(visit(parent, stack | {name}))
        try:
            config = SourcePreferences.model_validate(loader._read_module_yaml(root).get("technique_sources", {}))
            if contributors is not None and "technique_sources" in loader._read_module_yaml(root):
                if name not in contributors:
                    contributors.append(name)
            sites = []
            for value in config.sites:
                if any(character.isspace() for character in value):
                    raise ValueError(f"Invalid site URL: {value}")
                url = urlsplit(value)
                url.port  # Validate malformed and out-of-range ports.
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.username
                    or url.password
                    or "?" in value
                    or "#" in value
                ):
                    raise ValueError(f"Invalid site URL: {value}")
                sites.append(urlunsplit((url.scheme, url.netloc.lower(), url.path.rstrip("/"), "", "")))
            return list(dict.fromkeys((inherited if config.mode == "append" else []) + sites))
        except ValueError as error:
            raise ValueError(f"Module {name}: invalid technique_sources: {error}") from error

    return visit(module, set())
