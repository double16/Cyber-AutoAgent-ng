"""Protocol-neutral provenance for individual inventory operations."""

from typing import Any

OPERATION_PROVENANCE_FIELD = "operation_provenance_by_operation"
_PROVENANCE_RANK = {"unknown": 0, "inferred": 1, "observed": 2}


def operation_provenance(interaction: dict[str, Any]) -> dict[str, str]:
    """Read explicit operation provenance, falling back only for legacy records."""

    operations = interaction.get("operations") or []
    if OPERATION_PROVENANCE_FIELD not in interaction:
        return {operation: interaction.get("operation_provenance", "unknown") for operation in operations}
    values = interaction[OPERATION_PROVENANCE_FIELD]
    if (
        not isinstance(values, dict) or set(values) != set(operations)
        or any(not isinstance(value, str) or value not in _PROVENANCE_RANK for value in values.values())
    ):
        raise ValueError(
            "operation_provenance_by_operation must cover exactly the listed operations "
            "with observed, inferred, or unknown values"
        )
    return dict(values)


def set_operation_provenance(interaction: dict[str, Any], values: dict[str, str]) -> None:
    """Store authoritative per-operation values and a conservative legacy summary."""

    interaction["operations"] = list(values)
    interaction[OPERATION_PROVENANCE_FIELD] = dict(values)
    operation_provenance(interaction)
    statuses = set(values.values())
    if not statuses or "unknown" in statuses:
        interaction.pop("operation_provenance", None)
    else:
        interaction["operation_provenance"] = "inferred" if "inferred" in statuses else "observed"


def merge_operation_provenance(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, str]:
    """Combine independent observations without upgrading unrelated operations."""

    result = operation_provenance(existing)
    for operation, provenance in operation_provenance(incoming).items():
        result[operation] = max(result.get(operation, "unknown"), provenance, key=_PROVENANCE_RANK.__getitem__)
    return result
