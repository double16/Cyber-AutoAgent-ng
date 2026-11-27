"""Explicit module policy for reusable methodology, separate from execution restrictions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TechniquePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["append", "replace"] = "append"
    restrictions: list[str] = Field(default_factory=list)


def resolve_policy(loader, module: str) -> list[dict[str, str]]:
    """Resolve declared branches independently and retain each restriction's origin."""
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
            policy = TechniquePolicy.model_validate(loader._read_module_yaml(root).get("technique_policy", {}))
            rows = inherited if policy.mode == "append" else []
            for restriction in policy.restrictions:
                if not restriction.strip() or len(restriction) > 2000:
                    raise ValueError("Restrictions require 1–2,000 non-whitespace characters")
                row = {"module": name, "restriction": restriction.strip()}
                if row not in rows:
                    rows.append(row)
            return rows
        except ValueError as error:
            raise ValueError(f"Module {name}: invalid technique_policy: {error}") from error

    return visit(module, set())
