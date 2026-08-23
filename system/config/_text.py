ROUTING_MARKER = "Next agent: "


def strip_routing_marker(text: str) -> str:
    """
    Remove the agents' internal handoff marker from a user-facing answer.

    Every agent ends its output with a "Next agent: <name>" line, which is how
    the graph routes (see `_invoke_agent` in system/engine/graph/nodes.py). The
    graph stores that raw output verbatim, so the marker has to be stripped
    before the text is handed to whoever consumes the SDK — it is protocol
    between agents, not part of the answer.

    Args:
        text: The raw agent output.

    Returns:
        str: The answer without the trailing marker.
    """

    return text.split(ROUTING_MARKER)[0].rstrip()
