import unicodedata

ROUTING_MARKER = "Next agent: "


def strip_routing_marker(text: str) -> str:
    """
    Remove the agents' internal handoff marker from a user-facing answer.

    Every agent ends its output with a "Next agent: <name>" line, which is how
    the graph routes (see `_invoke_agent` in aeko/engine/graph/nodes.py). The
    graph stores that raw output verbatim, so the marker has to be stripped
    before the text is handed to whoever consumes the SDK — it is protocol
    between agents, not part of the answer.

    Args:
        text: The raw agent output.

    Returns:
        str: The answer without the trailing marker.
    """

    return text.split(ROUTING_MARKER)[0].rstrip()


def _comparable(label: str) -> str:
    """
    Reduce a heading to the form section labels are compared in.

    Accents and case are dropped because they are the two things a model varies
    without meaning to ("## RACIOCINIO" for "## Raciocínio"), and neither
    carries any information the caller asked to be told apart.

    Args:
        label: A heading, already stripped of its "#" markers.

    Returns:
        str: The comparable form.
    """

    decomposed = unicodedata.normalize("NFKD", label.strip())

    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _heading_in(line: str) -> str | None:
    """
    Read the heading a line declares, if it declares one.

    Args:
        line: One line of an agent's answer.

    Returns:
        str | None: The heading's text without its "#" markers, or None when
            the line is not a heading.
    """

    stripped = line.strip()

    if not stripped.startswith("#"):
        return None

    return stripped.lstrip("#").strip()


def parse_sections(text: str, labels: dict[str, str]) -> dict[str, str]:
    """
    Split an agent's answer into the sections its prompt told it to write.

    The same idea as `strip_routing_marker`: the agent is instructed to emit
    literal markers and the SDK reads them back, rather than asking the model
    to serialize a structure it can get subtly wrong. Only the requested labels
    open a section, so a heading the agent invented mid-answer stays part of
    the text it appears in instead of truncating it.

    Interpreting the text is all this does. Whether a missing section is an
    error, and what the sections become afterwards, belongs to the caller that
    knows what the text was supposed to describe.

    Args:
        text: The agent's answer, already stripped of its routing marker.
        labels: Mapping of the name to return each section under, to the exact
            heading the agent was told to write it under.

    Returns:
        dict[str, str]: The sections that were found, keyed by `labels`' keys.
            A label the answer never used is absent from the result; anything
            written before the first section is dropped, since it belongs to no
            section. Repeated headings continue the section they name rather
            than replacing it.
    """

    wanted = {_comparable(label): name for name, label in labels.items()}

    found: dict[str, list[str]] = {}
    current: str | None = None

    for line in text.splitlines():
        heading = _heading_in(line)
        name = wanted.get(_comparable(heading)) if heading is not None else None

        if name is not None:
            current = name
            found.setdefault(name, [])
        elif current is not None:
            found[current].append(line)

    return {name: "\n".join(lines).strip() for name, lines in found.items()}
