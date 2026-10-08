"""Code-owned declarations for tool result presentation."""

def bounded_inline_result[ToolT](tool: ToolT) -> ToolT:
    """Declare that bounded excerpts should stay inline up to the router ceiling.

    Apply to the registered tool object, after the tool decorator. This declaration
    bypasses the artifact threshold, never the maximum inline character limit.
    """

    tool._cyber_bounded_inline_result = True
    return tool


def has_bounded_inline_result(tool: object) -> bool:
    """Read trusted tool metadata, never declarations from result content."""

    return getattr(tool, "_cyber_bounded_inline_result", False) is True
