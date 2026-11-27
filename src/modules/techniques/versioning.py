"""Controller-owned generation compatibility, independent of document format."""

TECHNIQUE_GENERATION_VERSION = 1


def generation_eligible(row: dict) -> bool:
    """Module guidance is maintained separately; generated guidance requires exact compatibility."""
    if row["origin"].startswith("module:") and not row.get("current_revision", True):
        return False
    return row["origin"] != "generated" or row.get("generation_version") == TECHNIQUE_GENERATION_VERSION
