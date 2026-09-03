import logging

from modules.tools.semantic_enum import normalize_semantic_enum


def test_normalize_semantic_enum_canonicalizes_aliases_and_logs(caplog):
    with caplog.at_level(logging.INFO):
        value = normalize_semantic_enum(
            " Inventory-Manifest ",
            aliases={"inventory_manifest": "artifact"},
            field_name="output_kind",
            logger=logging.getLogger("test.semantic_enum"),
        )

    assert value == "artifact"
    assert "canonical=artifact" in caplog.text


def test_normalize_semantic_enum_preserves_unknown_and_non_string_values():
    logger = logging.getLogger("test.semantic_enum")

    assert normalize_semantic_enum("unknown value", aliases={}, field_name="kind", logger=logger) == "unknown_value"
    assert normalize_semantic_enum(None, aliases={}, field_name="kind", logger=logger) is None


def test_normalize_semantic_enum_does_not_log_when_value_is_already_canonical(caplog):
    with caplog.at_level(logging.INFO):
        value = normalize_semantic_enum(
            "inventory_manifest",
            aliases={"inventory_manifest": "inventory_manifest"},
            field_name="kind",
            logger=logging.getLogger("test.semantic_enum"),
        )

    assert value == "inventory_manifest"
    assert "Normalized semantic enum" not in caplog.text
