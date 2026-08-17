"""Globally available inventory-manifest conversion tool.

The implementation remains at its original plugin path for backwards-compatible imports while this module exposes
the converter through the operation-wide core tool catalog.
"""

from modules.operation_plugins.web.tools.recon_inventory_manifest import (
    recon_output_to_inventory_manifest,
    records_to_inventory_manifest,
    resolve_inventory_target,
    write_inventory_manifest,
)

__all__ = [
    "recon_output_to_inventory_manifest",
    "records_to_inventory_manifest",
    "resolve_inventory_target",
    "write_inventory_manifest",
]
