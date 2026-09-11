"""Refresh the local CWE and MITRE ATT&CK taxonomy cache or bundled snapshot.

Run with ``uv run python -m modules.config.taxonomy_refresh``.  The command
uses the same official sources and atomic write behaviour as report generation.
"""

import argparse

from modules.config.taxonomy_catalog import TaxonomyCatalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-snapshot", action="store_true", help="Atomically replace the bundled fallback snapshot")
    arguments = parser.parse_args()
    catalog = TaxonomyCatalog()
    refreshed = catalog.refresh_snapshot() if arguments.write_snapshot else catalog._refresh()
    if not refreshed:
        return 1
    destination = "bundled snapshot" if arguments.write_snapshot else "local cache"
    print(f"Refreshed {destination}: {len(refreshed['cwe'])} CWE, {len(refreshed['attack'])} ATT&CK records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
