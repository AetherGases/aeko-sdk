"""Deterministic extraction of inventory JSON from coordinator answers."""

import json
import re
import unicodedata

from aeko.config._text import strip_routing_marker
from aeko.engine.prompts import PLAN_SECTIONS

_INVENTORY_BLOCK = re.compile(r"```inventory\s*\n(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_PYTHON_NONE = re.compile(r"\bNone\b")
_PYTHON_TRUE = re.compile(r"\bTrue\b")
_PYTHON_FALSE = re.compile(r"\bFalse\b")


def _comparable(label: str) -> str:
    decomposed = unicodedata.normalize("NFKD", label.strip())
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def _heading_in(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    return stripped.lstrip("#").strip()


def _repair_serialization(raw: str) -> str:
    text = _TRAILING_COMMA.sub(r"\1", raw)
    text = _PYTHON_NONE.sub("null", text)
    text = _PYTHON_TRUE.sub("true", text)
    text = _PYTHON_FALSE.sub("false", text)
    return text


def _load_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(_repair_serialization(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _tail_after_reasoning(text: str) -> str:
    wanted = {_comparable(label): name for name, label in PLAN_SECTIONS.items()}
    collecting = False
    tail_lines: list[str] = []

    for line in text.splitlines():
        heading = _heading_in(line)
        name = wanted.get(_comparable(heading)) if heading is not None else None

        if name == "reasoning":
            collecting = True
            tail_lines = []
            continue
        if name is not None:
            collecting = False
            continue
        if collecting:
            tail_lines.append(line)

    return "\n".join(tail_lines).strip()


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                remainder = text[end:].strip()
                if remainder and "{" in remainder:
                    return None
                return text[start:end]
    return None


def salvage_inventory_payload(answer: str) -> dict | None:
    """
    Recover a structured inventory object from a coordinator answer without LLM calls.

    The coordinator may embed JSON in a dedicated ```inventory fence or, when that
    fence is missing, as a single object after the plan's reasoning section. Only
    minimal serialization repairs are applied before parsing.

    Args:
        answer: The coordinator's raw answer, with or without a routing marker.

    Returns:
        dict | None: The parsed inventory object when salvage succeeds, otherwise
            None when no payload is found or it cannot be recovered.
    """

    match = _INVENTORY_BLOCK.search(answer)
    if match is not None:
        return _load_object(match.group(1).strip())

    plan_text = strip_routing_marker(answer)
    tail = _tail_after_reasoning(plan_text)
    if not tail:
        return None

    snippet = _extract_json_object(tail)
    if snippet is None:
        return None
    return _load_object(snippet)
