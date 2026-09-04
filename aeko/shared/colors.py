from __future__ import annotations

from aeko.shared.context import Flow

# The ANSI escapes the log lines are painted with. Kept as plain constants
# rather than a dependency on a color library: one escape per color is the
# whole of what this needs, and a logging package that pulls in a rendering
# library is a package a consumer cannot install without it.
RESET = "\033[0m"

# Failure, whatever the flow: a run that died is never blue.
RED = "\033[31m"

# Success. The report flow — and SDK configuration, which belongs to no flow —
# take the plain (dark) blue; the conversational flow takes the bright one, so
# a chat request and a report request can be told apart at a glance in one
# stream.
BLUE = "\033[34m"
LIGHT_BLUE = "\033[94m"

# The shade of blue each flow's successful lines are written in. A flow absent
# from here — or no flow at all — falls back to the plain blue.
FLOW_COLORS: dict[Flow, str] = {
    Flow.CONVERSATIONAL: LIGHT_BLUE,
    Flow.REPORT: BLUE,
}


def success_color(flow: Flow | None = None) -> str:
    """
    Return the color a successful line of the given flow is written in.

    Args:
        flow: The flow the line belongs to, or None for a line that belongs to
            no processing — SDK configuration being the one that does.

    Returns:
        str: The ANSI escape to open the line with.
    """

    if flow is None:
        return BLUE

    return FLOW_COLORS.get(flow, BLUE)


def colorize(text: str, color: str) -> str:
    """
    Wrap text in a color, leaving it alone when there is no color to apply.

    Args:
        text: The line to paint.
        color: The ANSI escape to open with, or an empty string to paint
            nothing — which is how a non-terminal stream gets plain text.

    Returns:
        str: The line, colored or verbatim.
    """

    if not color:
        return text

    return f"{color}{text}{RESET}"
