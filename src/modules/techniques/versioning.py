"""Controller-owned generation compatibility, independent of document format."""

TECHNIQUE_GENERATION_VERSION = 1


def generation_eligible(row: dict) -> bool:
    """Module guidance is maintained separately; generated guidance requires exact compatibility."""
    return row["origin"] != "generated" or row.get("generation_version") == TECHNIQUE_GENERATION_VERSION
