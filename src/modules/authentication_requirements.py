"""Protocol-neutral task authentication requirements and credential selection."""

from collections.abc import Callable, Iterable
from itertools import combinations
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthenticationRequirement(BaseModel):
    """Requested identities, separate from established authentication contexts."""

    model_config = ConfigDict(extra="forbid")
    comparison: Literal["none", "account", "role", "tenant"] = "none"
    roles: list[str] = Field(default_factory=list)

    @field_validator("comparison", mode="before")
    @classmethod
    def normalize_comparison(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return {"single": "none", "no_comparison": "none", "cross_account": "account",
                "cross_role": "role", "cross_tenant": "tenant"}.get(normalized, normalized)

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, roles: list[str]) -> list[str]:
        if any(not role.strip() for role in roles):
            raise ValueError("authentication roles must be non-empty")
        return list(dict.fromkeys(role.strip() for role in roles))


class AuthenticationSelectionError(ValueError):
    """The eligible pool cannot satisfy the declared identities."""


def select_authentication_credentials(
    records: Iterable[dict[str, Any]],
    targets: Iterable[str],
    requirement: AuthenticationRequirement,
    *,
    is_valid: Callable[[dict[str, Any]], bool],
    preferred_ids: Iterable[str] = (),
    frozen_ids: Iterable[str] = (),
) -> tuple[dict[str, Any], ...]:
    """Select the smallest qualifying context set, preferring already valid sessions."""

    preferred = set(preferred_ids)
    frozen = set(frozen_ids)
    unique = {
        str(record["credential_id"]): record
        for record in records
        if record.get("credential_id") and record.get("status") not in {"invalid", "revoked", "retired"}
    }
    if frozen:
        if not frozen.issubset(unique):
            raise AuthenticationSelectionError("frozen_credentials_unavailable")
        return tuple(unique[credential_id] for credential_id in sorted(frozen))
    assigned_targets = tuple(dict.fromkeys(targets))
    candidates = sorted(
        (record for record in unique.values() if record["target"] in assigned_targets
         and (not requirement.roles or record.get("role", "") in requirement.roles)),
        key=lambda record: (
            not is_valid(record), str(record["credential_id"]) not in preferred, str(record["credential_id"]),
        ),
    )
    selected = []
    for target in assigned_targets:
        pool = [
            record for record in candidates
            if record["target"] == target
            and (not requirement.roles or record.get("role", "") in requirement.roles)
        ]
        if requirement.comparison == "none":
            for role in requirement.roles or [None]:
                match = next((record for record in pool if role is None or record.get("role", "") == role), None)
                if match is None:
                    raise AuthenticationSelectionError(
                        "no_eligible_credentials" if role is None else "required_role_credentials_unavailable",
                    )
                selected.append(match)
            continue
        field = {"account": "account_label", "role": "role", "tenant": "tenant_label"}[requirement.comparison]
        pair = next((
            (left, right) for left, right in combinations(pool, 2)
            if left.get(field) and right.get(field) and left[field] != right[field]
            and set(requirement.roles).issubset({left.get("role", ""), right.get("role", "")})
        ), None)
        if pair is None:
            raise AuthenticationSelectionError(f"{requirement.comparison}_comparison_credentials_unavailable")
        selected.extend(pair)
    return tuple(selected)
