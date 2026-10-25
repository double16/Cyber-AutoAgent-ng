"""Minimum task-scoped identity selection across credential and target types."""

import pytest
from pydantic import ValidationError

from modules.authentication_requirements import (
    AuthenticationRequirement,
    AuthenticationSelectionError,
    select_authentication_credentials,
)


def credential(credential_id, target="https://target.test", role="member", **metadata):
    return {"credential_id": credential_id, "target": target, "role": role, **metadata}


def select(records, comparison="none", roles=(), targets=("https://target.test",), **kwargs):
    return select_authentication_credentials(
        records, targets, AuthenticationRequirement(comparison=comparison, roles=list(roles)),
        is_valid=kwargs.pop("is_valid", lambda _record: False), **kwargs,
    )


@pytest.mark.parametrize("target", ["https://target.test", "ssh://target.test", "/repo/project"])
@pytest.mark.parametrize("credential_type", ["username_password", "api_key", "oauth2_client"])
def test_single_context_per_target_and_required_role(target, credential_type):
    records = [credential("b", target, credential_type=credential_type), credential("a", target),
               credential("admin", target, role="admin"), credential("other", "other-target")]
    assert [item["credential_id"] for item in select(records, roles=["member"], targets=[target])] == ["a"]
    assert len(select(records, roles=["member", "admin"], targets=[target])) == 2
    assert len(select(records, targets=[target])) == 1


@pytest.mark.parametrize(("comparison", "field"), [("account", "account_label"), ("role", "role"),
                                                    ("tenant", "tenant_label")])
def test_comparison_requires_a_qualifying_pair(comparison, field):
    records = [credential("a", **{field: "first"}), credential("b", **{field: "second"}),
               credential("c", **{field: "first"})]
    assert [item["credential_id"] for item in select(records, comparison)] == ["a", "b"]
    with pytest.raises(AuthenticationSelectionError, match="comparison_credentials_unavailable"):
        select([records[0], records[2]], comparison)


def test_account_pair_without_identity_metadata_is_not_invented():
    with pytest.raises(AuthenticationSelectionError):
        select([credential("a"), credential("b")], "account")
    with pytest.raises(AuthenticationSelectionError):
        select([credential("a", account_label="a"), credential("b")], "account")


def test_valid_then_preferred_then_stable_selection_and_deduplication():
    records = [credential("a"), credential("b"), credential("c"), credential("c")]
    assert select(records, preferred_ids=["b"], is_valid=lambda item: item["credential_id"] == "c")[0][
        "credential_id"
    ] == "c"
    assert select(records, preferred_ids=["b"])[0]["credential_id"] == "b"
    assert len(select(records, targets=["https://target.test"] * 2)) == 1


@pytest.mark.parametrize("status", ["invalid", "retired", "revoked"])
def test_ineligible_and_missing_required_role(status):
    assert select([credential("a", status=status), credential("b")])[0]["credential_id"] == "b"
    with pytest.raises(AuthenticationSelectionError, match="required_role"):
        select([credential("a", status=status)], roles=["member"])
    with pytest.raises(AuthenticationSelectionError, match="required_role"):
        select([credential("a")], roles=["admin"])
    with pytest.raises(AuthenticationSelectionError):
        select([credential("a", account_label="a"), credential("b", account_label="b")],
               "account", roles=["member", "admin"])


def test_frozen_ids_preserved_without_session_preference_or_substitution():
    records = [credential("a"), credential("b"), credential("c")]
    assert [item["credential_id"] for item in select(records, frozen_ids=["b", "a"],
                                                     is_valid=lambda _item: True)] == ["a", "b"]
    with pytest.raises(AuthenticationSelectionError, match="frozen"):
        select(records, frozen_ids=["missing"])


def test_requirement_validation():
    assert AuthenticationRequirement(roles=[" member ", "member"]).roles == ["member"]
    for value in [{"comparison": "unknown"}, {"roles": [""]}, {"extra": True}]:
        with pytest.raises(ValidationError):
            AuthenticationRequirement.model_validate(value)
    assert select_authentication_credentials([], [], AuthenticationRequirement(), is_valid=lambda _item: False) == ()


@pytest.mark.parametrize("alias,canonical", [("cross-account", "account"), ("Cross Role", "role"),
                                             ("cross_tenant", "tenant"), ("single", "none")])
def test_comparison_synonyms_normalize_at_boundary(alias, canonical):
    assert AuthenticationRequirement(comparison=alias).comparison == canonical
    with pytest.raises(ValidationError):
        AuthenticationRequirement(comparison=2)


def test_unused_roles_and_targets_are_not_checked_for_session_validity():
    checked = []
    records = [credential("member"), credential("admin", role="admin"), credential("other", target="other")]
    result = select(records, roles=["member"], is_valid=lambda record: checked.append(record["credential_id"]))
    assert checked == ["member"]
    assert result[0]["credential_id"] == "member"
