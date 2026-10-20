"""Operation provenance remains explicit across protocols and legacy manifests."""

import copy

import pytest

from modules.tools.inventory_provenance import (
    OPERATION_PROVENANCE_FIELD,
    merge_operation_provenance,
    operation_provenance,
    set_operation_provenance,
)
from modules.tools.memory import _normalize_inventory_interaction


@pytest.mark.parametrize("operations", [["GET", "POST"], ["connect", "send"], ["read", "write"]])
@pytest.mark.parametrize("aggregate", [None, "inferred", "observed"])
def test_legacy_provenance_is_read_without_rewriting(operations, aggregate):
    interaction = {"operations": operations}
    if aggregate is not None:
        interaction["operation_provenance"] = aggregate
    original = copy.deepcopy(interaction)
    assert operation_provenance(interaction) == dict.fromkeys(operations, aggregate or "unknown")
    assert interaction == original


@pytest.mark.parametrize("operations", [None, []])
def test_legacy_optional_operation_lists_remain_valid_without_rewriting(operations):
    interaction = {"operations": operations}
    assert not _normalize_inventory_interaction("subject", {"interaction": interaction})
    assert operation_provenance(interaction) == {}
    assert interaction == {"operations": operations}


@pytest.mark.parametrize("values,aggregate", [
    ({}, None), ({"read": "observed"}, "observed"),
    ({"read": "observed", "write": "inferred"}, "inferred"),
    ({"read": "inferred"}, "inferred"), ({"read": "unknown"}, None),
    ({"read": "observed", "write": "unknown"}, None),
])
def test_authoritative_map_and_conservative_summary(values, aggregate):
    interaction = {"operation_provenance": "observed"}
    set_operation_provenance(interaction, values)
    assert interaction["operations"] == list(values)
    assert operation_provenance(interaction) == values
    assert interaction.get("operation_provenance") == aggregate
    assert not _normalize_inventory_interaction("subject", {"interaction": interaction})


@pytest.mark.parametrize("value", [None, [], "observed", {}, {"read": "guessed"},
                                      {"read": None}, {"read": []}, {"read": "observed", "write": "inferred"}])
def test_explicit_maps_reject_invalid_values_or_incomplete_operation_coverage(value):
    interaction = {"operations": ["read"], OPERATION_PROVENANCE_FIELD: value}
    with pytest.raises(ValueError, match="cover exactly"):
        _normalize_inventory_interaction("subject", {"interaction": interaction})


@pytest.mark.parametrize("left", ["observed", "inferred", "unknown"])
@pytest.mark.parametrize("right", ["observed", "inferred", "unknown"])
def test_merge_is_independent_of_source_order_and_does_not_upgrade_other_operations(left, right):
    first = {"operations": ["read", "connect"], OPERATION_PROVENANCE_FIELD: {"read": left, "connect": "unknown"}}
    second = {"operations": ["read", "write"], OPERATION_PROVENANCE_FIELD: {"read": right, "write": "inferred"}}
    merged = merge_operation_provenance(first, second)
    assert merged == merge_operation_provenance(second, first)
    assert merged["connect"] == "unknown" and merged["write"] == "inferred"
    assert merged["read"] == next(status for status in ("observed", "inferred", "unknown") if status in {left, right})
