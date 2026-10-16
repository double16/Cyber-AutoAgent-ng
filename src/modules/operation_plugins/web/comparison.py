"""Explicit HTTP assertions for deterministic comparison acceptance.

Absent or unmatched assertions require semantic review; capture availability is insufficient.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ResponseAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: str = Field(min_length=1)
    status: int = Field(ge=100, le=599)
    body_contains: str = Field(min_length=1)
    content_type: str = Field(min_length=1)


class HTTPComparisonContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    url: str
    method: Literal["GET", "HEAD"]
    prerequisite_evidence_refs: list[str] = Field(min_length=1)
    assertions: list[ResponseAssertion] = Field(min_length=2)


def comparison_contract_matches(
    value: Any, captures: dict[str, Any], *, prerequisite_refs: set[str], identities: set[str],
) -> bool:
    """Accept only a complete explicit contract over the same request and required identities."""

    try:
        contract = HTTPComparisonContract.model_validate(value)
    except ValidationError:
        return False
    if captures.get("coverage_gaps") or not set(contract.prerequisite_evidence_refs).issubset(prerequisite_refs):
        return False
    if {assertion.identity for assertion in contract.assertions} != identities:
        return False
    if len(contract.assertions) != len(identities):
        return False
    responses = [
        *captures.get("responses", []),
        *({**response, "credential_id": "anonymous"} for response in captures.get("unauthenticated_responses", [])),
    ]
    if len(responses) != len(contract.assertions):
        return False
    for assertion in contract.assertions:
        matches = [response for response in responses if response.get("credential_id") == assertion.identity]
        if len(matches) != 1:
            return False
        response = matches[0]
        headers = {key.lower(): str(value).lower() for key, value in response.get("headers", {}).items()}
        if (
            response.get("url") != contract.url or response.get("method") != contract.method
            or response.get("status") != assertion.status
            or assertion.body_contains not in response.get("body", "")
            or headers.get("content-type", "").split(";", 1)[0].strip() != assertion.content_type.lower()
        ):
            return False
    return True


class InventoryResponseAssertion(ResponseAssertion):
    identity: str = Field(pattern=r"^(anonymous|account:[12]|role:.+)$")


class InventoryHTTPComparisonContract(HTTPComparisonContract):
    """Explicit expected behavior supplied by an inventory producer, before credential selection."""

    comparison: Literal["none", "account", "role"]
    assertions: list[InventoryResponseAssertion] = Field(min_length=1)


def inventory_comparison_contract(items: list[dict[str, Any]], item_ids: list[str]) -> dict[str, Any] | None:
    """Validate per-interaction assertions without deriving expected behavior from observations."""

    contracts = []
    for item in items:
        if item.get("id") not in item_ids or item.get("kind") != "endpoint":
            continue
        interaction = item.get("attributes", {}).get("interaction", {})
        value = interaction.get("extensions", {}).get("http_comparison")
        if value is None:
            continue
        contract = InventoryHTTPComparisonContract.model_validate(value)
        if (
            interaction.get("interface") != "http" or contract.url != item.get("value")
            or contract.method not in interaction.get("operations", [])
            or interaction.get("operation_provenance") != "observed"
        ):
            raise ValueError("HTTP comparison assertions require the same concrete URL and an observed operation")
        contracts.append(contract.model_dump())
    if len(contracts) > 1:
        raise ValueError("HTTP comparison task requires one unambiguous interaction contract")
    return contracts[0] if contracts else None


def bind_comparison_contract(
    value: Any, *, comparison: str, credential_roles: dict[str, str], include_anonymous: bool,
) -> dict[str, Any] | None:
    """Resolve declared account slots or roles to the exact authorized identities for this task."""

    try:
        template = InventoryHTTPComparisonContract.model_validate(value)
    except ValidationError:
        return None
    if template.comparison != comparison:
        return None
    identities = list(credential_roles)
    bound = []
    for assertion in template.assertions:
        selector = assertion.identity
        if selector == "anonymous":
            matches = ["anonymous"] if include_anonymous else []
        elif selector.startswith("account:"):
            index = int(selector.split(":", 1)[1]) - 1
            matches = identities[index:index + 1]
        else:
            role = selector.split(":", 1)[1]
            matches = [identity for identity, assigned_role in credential_roles.items() if assigned_role == role]
        # A role assertion explicitly applies to every selected account in that role.
        if not matches:
            return None
        bound.extend({**assertion.model_dump(), "identity": identity} for identity in matches)
    return {**template.model_dump(exclude={"comparison", "assertions"}), "assertions": bound}
