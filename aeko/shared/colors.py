"""ANSI colors for log lines, without a rendering-library dependency."""

from __future__ import annotations


from aeko.shared.constants import BLUE, FLOW_COLORS, LIGHT_BLUE, RED, RESET

from aeko.shared.context import Flow


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
