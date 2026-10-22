"""Execution-context binding for the request currently being processed."""

from __future__ import annotations


from contextvars import ContextVar, Token

from enum import Enum

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from aeko.shared.logger import Processing


class Flow(Enum):
    """
    The kind of processing a run belongs to.

    The value is the wording the log line is written with — it opens the
    "... processing finished in ..." header — so a flow reads the same wherever
    it is named. It is also what picks the shade of blue a successful line is
    printed in (see `aeko.shared.colors`).
    """

    CONVERSATIONAL = "Conversational"

    REPORT = "Report"


_CURRENT_RUN: ContextVar["Processing | None"] = ContextVar("aeko_run", default=None)


def current_run() -> "Processing | None":
    """
    Read the processing the current execution context belongs to.

    Returns:
        Processing | None: The run in progress, or None outside any — which is
            how something reported from outside a request is dropped rather
            than attributed to whatever ran last.
    """

    return _CURRENT_RUN.get()


def bind_run(run: "Processing") -> Token:
    """
    Make `run` the processing this execution context reports into.

    Args:
        run: The processing that is starting.

    Returns:
        Token: The token to hand back to `unbind_run` when it ends.
    """

    return _CURRENT_RUN.set(run)


def unbind_run(token: Token) -> None:
    """
    Restore whatever processing was current before `bind_run` was called.

    Restoring the previous value rather than clearing it is what keeps a nested
    processing — should one ever be added — from detaching the run it was
    started inside of.

    Args:
        token: The token `bind_run` returned.
    """

    _CURRENT_RUN.reset(token)
