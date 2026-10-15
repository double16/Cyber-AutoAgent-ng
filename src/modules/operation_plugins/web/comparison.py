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
