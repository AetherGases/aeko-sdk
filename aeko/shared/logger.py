from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from time import perf_counter
from typing import Any, IO, Iterator

from aeko.shared.colors import RED, colorize, success_color
from aeko.shared.context import Flow, bind_run, current_run, unbind_run
from aeko.shared.event_tracking import (
    EVENT_TRACKING_FLOWS,
    METRICS_ATTR,
    AekoMetrics,
    AgentCall,
)

# The bracket every line of this SDK opens with, so a consuming API can tell
# our lines from its own at a glance.
LOG_PREFIX = "aeko-sdk"

# The logger the whole SDK emits through. A named logger of our own, never the
# root one: a library that configures the root logger reconfigures logging for
# the application that imported it, which is not ours to do.
LOGGER_NAME = "aeko"

# What each line of a record's detail list is opened with. The indent is what
# keeps a multi-line record readable as one event rather than as several.
ITEM_PREFIX = "  - "

# The record attributes carrying what the format needs and the stdlib record
# has no field for. Set through `extra=` at emission, never read from anywhere
# else, so a record reaching our formatter from outside this module still
# formats — with defaults — instead of raising.
MODULE_KEY = "aeko_module"
COLOR_KEY = "aeko_color"


class AekoFormatter(logging.Formatter):
    """
    Render a record as one Aeko log line, colored by what it reports.

    The color is applied here rather than baked into the message so the record
    itself keeps carrying plain text: anything reading the log
    programmatically — a test, a log shipper, a consumer's own handler — sees
    the description and not a string wrapped in escape codes.

    A record may describe a whole request, in which case its message carries
    the detail list under the header, already indented. It is still one record,
    written in one color, in a single call to the handler.

    Attributes:
        colors: Whether to paint the line at all. False renders the exact same
            text without escapes, which is what a file or a piped stdout gets.
    """

    def __init__(self, *, colors: bool = True):
        """
        Build a formatter.

        Args:
            colors: Whether the rendered lines carry ANSI escapes.
        """

        super().__init__()
        self.colors = colors

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """
        Render a record's timestamp down to the millisecond.

        Args:
            record: The record being formatted.
            datefmt: A strftime format, honoured when given for the sake of
                anyone reconfiguring this formatter the stdlib way.

        Returns:
            str: The timestamp as it appears in the third bracket.
        """

        moment = datetime.fromtimestamp(record.created)

        if datefmt:
            return moment.strftime(datefmt)

        return moment.isoformat(sep=" ", timespec="milliseconds")

    def format(self, record: logging.LogRecord) -> str:
        """
        Render one record as "[aeko-sdk] [module] [datetime] description".

        Args:
            record: The record to render.

        Returns:
            str: The finished line, or the header line followed by its indented
                detail list when the record carries one.
        """

        module = getattr(record, MODULE_KEY, record.name)
        color = getattr(record, COLOR_KEY, "") if self.colors else ""

        line = (
            f"[{LOG_PREFIX}] [{module}] [{self.formatTime(record)}] {record.getMessage()}"
        )

        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"

        return colorize(line, color)


def _wants_colors(stream: IO[str]) -> bool:
    """
    Decide whether a stream should be written to in color.

    Args:
        stream: The stream the handler writes to.

    Returns:
        bool: True only for an interactive terminal. A redirected stdout or a
            log file would otherwise be filled with escape codes nothing there
            is going to interpret.
    """

    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        # A closed stream, or one that is not a real file object: assume the
        # escapes would not be understood rather than risk writing them.
        return False


def configure_logging(*, stream: IO[str] | None = None, level: int = logging.INFO,
                      colors: bool | None = None) -> logging.Logger:
    """
    Install the SDK's own handler on the SDK's own logger.

    Replaces any handler a previous call installed, so calling this again is
    how a consuming API redirects the logs or changes their level. Nothing
    outside the "aeko" logger is touched, and propagation to the root logger is
    turned off, so an application that configured logging its own way does not
    receive every line twice.

    Args:
        stream: Where the lines go. Defaults to stderr, which is where
            diagnostics belong and where they will not corrupt a program whose
            stdout is data.
        level: The lowest level to emit.
        colors: Whether to paint the lines, or None to paint them only when the
            stream is an interactive terminal.

    Returns:
        logging.Logger: The configured logger.
    """

    stream = sys.stderr if stream is None else stream
    logger = logging.getLogger(LOGGER_NAME)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AekoFormatter(colors=_wants_colors(stream) if colors is None else colors)
    )

    for installed in list(logger.handlers):
        logger.removeHandler(installed)

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    return logger


def _logger() -> logging.Logger:
    """
    Return the SDK's logger, installing the default handler on first use.

    Installed here rather than at import time so merely importing the SDK
    changes nothing about the process's logging: a consumer calling
    `configure_logging()` first keeps its own choice, and one that never logs
    never gets a handler at all.

    Returns:
        logging.Logger: The logger every emission goes through.
    """

    logger = logging.getLogger(LOGGER_NAME)

    if not logger.handlers:
        return configure_logging()

    return logger


def _emit(level: int, module: str, description: str, color: str) -> None:
    """
    Write one record, tagging it with everything the formatter needs.

    Args:
        level: The logging level to emit at.
        module: The module bracket's content.
        description: The header, plus its indented detail list when there is one.
        color: The ANSI escape the formatter should paint it with.
    """

    _logger().log(
        level,
        description,
        extra={MODULE_KEY: module, COLOR_KEY: color},
    )


def log_success(module: str, description: str, *, flow: Flow | None = None) -> None:
    """
    Report something that went right, in the blue its flow is written in.

    Args:
        module: The module bracket's content, e.g. "messenger".
        description: What happened.
        flow: The flow this belongs to, or None for SDK configuration, which
            belongs to no processing.
    """

    _emit(logging.INFO, module, description, success_color(flow))


def log_failure(module: str, description: str, *, flow: Flow | None = None) -> None:
    """
    Report something that went wrong, in red whatever the flow.

    Args:
        module: The module bracket's content.
        description: What failed.
        flow: The flow this belongs to, kept for symmetry with `log_success`:
            a failure is red either way, but the caller should not have to know
            that to report one.
    """

    _emit(logging.ERROR, module, description, RED)


def record_agent_call(agent_name: str, seconds: float) -> None:
    """
    Record that an agent was called, for the current request's log to list.

    Nothing is written here. The agents a request called are one item of the
    single record it writes when it ends, which is what keeps a request to one
    log line however many agents it went through.

    A call made outside any processing is dropped rather than attributed to
    whatever ran last: the graph can be driven directly, and a run nobody
    opened has no log to belong to.

    Records the name and the duration alone. What an agent *cost* — its tokens,
    its model, the tools it called — is collected by `agent_call()`, which is
    what the graph invokes agents through; this stays for anyone reporting a
    call they only timed.

    Args:
        agent_name: The agent, under the exact name the graph routes it by.
        seconds: How long the invocation took.
    """

    run = current_run()

    if run is not None:
        run.agent_called(AgentCall(name=agent_name, seconds=seconds))


def _as_millis(seconds: float) -> str:
    """
    Render a duration the way the agent list reports it.

    Args:
        seconds: The duration.

    Returns:
        str: The duration in whole milliseconds.
    """

    return f"{seconds * 1000:.0f}ms"


class Processing:
    """
    One request in progress, and everything its single log line will report.

    Yielded by `processing()`. Nothing is written while a request runs: what is
    reported through this handle is accumulated and rendered once, when the
    request ends, as one record — a header saying how it went and how long it
    took, followed by an indented list of what it did.

    The same accumulation is what the request's `AekoMetrics` is built from
    (see `event_tracking`), so the log a person reads and the row the API
    persists are two renderings of one account rather than two accounts.

    Attributes:
        flow: The flow this request belongs to.
        module: The module bracket its record is written under.
        id_request: What the API correlates this request by, echoed into its
            event tracking. Empty for a run opened outside the public facade,
            which nobody is going to persist.
    """

    def __init__(self, flow: Flow, module: str, id_request: str = ""):
        """
        Open a handle on a request that is starting.

        Args:
            flow: The flow this request belongs to.
            module: The module bracket its record is written under.
            id_request: The API's id for this request, if it supplied one.
        """

        self.flow = flow
        self.module = module
        self.id_request = id_request
        self._items: list[tuple[str, str]] = []
        self._agents: list[AgentCall] = []
        self._failure: str | None = None
        self._started = perf_counter()

        # Both frozen by `_close`, so the event tracking a caller reads after
        # the request ended reports the request's own duration and outcome
        # rather than however long it took them to ask.
        self._latency: int | None = None
        self._error_description: str | None = None

    def item(self, label: str, value: Any) -> None:
        """
        Add one line to this request's detail list.

        Items are listed in the order they are added, above the agents, which
        are always reported last.

        Args:
            label: What the line is about, e.g. "session".
            value: What to report. Rendered with `str()`.
        """

        self._items.append((label, str(value)))

    def agent_called(self, call: AgentCall) -> None:
        """
        Record one agent invocation, to be listed among the others.

        An agent called more than once — the guardrail's retry loop calls
        several of them again — is listed once per call, in call order, so the
        loop is visible in the line rather than hidden by it.

        Args:
            call: The invocation, with whatever was measured about it. The log
                line reads its name and duration; its event tracking reads the
                rest.
        """

        self._agents.append(call)

    def fail(self, reason: str) -> None:
        """
        Mark this request as failed without raising.

        Some requests end badly with no exception to show for it — a
        conversational turn the guardrail never approved delivers nothing to
        the user, yet returns normally — and those are failures the log has to
        show in red rather than pass off as successes.

        Args:
            reason: Why the request produced nothing.
        """

        self._failure = reason

    @property
    def elapsed(self) -> float:
        """
        Return how long this request has been running, in seconds.

        Returns:
            float: The seconds since the handle was opened.
        """

        return perf_counter() - self._started

    def _details(self) -> str:
        """
        Render the detail list that goes under the header.

        Returns:
            str: One indented line per item, agents last, or an empty string
                when the request has nothing to list.
        """

        lines = [f"{ITEM_PREFIX}{label}: {value}" for label, value in self._items]

        if self._agents:
            called = ", ".join(
                f"{call.name} ({_as_millis(call.seconds)})" for call in self._agents
            )
            lines.append(f"{ITEM_PREFIX}agents: {called}")

        if not lines:
            return ""

        return "\n" + "\n".join(lines)

    def event_tracking(self) -> AekoMetrics:
        """
        Render this request as the row the API persists.

        Built from the same accumulation the log line is written from, so the
        agents it lists are the agents the line lists, in the same order, one
        entry per call. Meant to be read once the request has ended — that is
        when its duration and its outcome are settled — but reading it early
        reports the request as it stands rather than raising.

        Returns:
            AekoMetrics: What this request cost and went through.
        """

        latency = self._latency

        if latency is None:
            latency = round(self.elapsed * 1000)

        return AekoMetrics(
            id_request=self.id_request,
            latency=latency,
            error_description=self._error_description,
            flow=EVENT_TRACKING_FLOWS[self.flow],
            used_agents=[call.to_metrics() for call in self._agents],
        )

    def _close(self, error: BaseException | None = None) -> None:
        """
        Write this request's one and only record, and settle its event tracking.

        Args:
            error: The exception that ended the request, if one did.
        """

        self._latency = round(self.elapsed * 1000)
        elapsed = f"{self.elapsed:.2f}s"
        details = self._details()

        if error is not None:
            reason = f"{type(error).__name__}: {error}"
        else:
            reason = self._failure

        self._error_description = reason

        if reason is not None:
            log_failure(
                self.module,
                f"{self.flow.value} processing failed in {elapsed}: {reason}{details}",
                flow=self.flow,
            )
            return

        log_success(
            self.module,
            f"{self.flow.value} processing finished in {elapsed}{details}",
            flow=self.flow,
        )


@contextmanager
def processing(flow: Flow, module: str, id_request: str = "") -> Iterator[Processing]:
    """
    Report one whole request as a single log record, written when it ends.

    Nothing is written while the request runs. The handle collects what the
    request did — the agents it called included, which reach it through
    `agent_call` without the graph having to know a log exists — and one record
    is written on the way out, saying how the request went, how long it took,
    and what it went through. The caller reads the same account back as an
    `AekoMetrics` (see `Processing.event_tracking`) to hand to the API.

    An exception escaping the body is reported in red and re-raised untouched
    except for the event tracking attached to it: this observes a request, it
    does not change what one does. The attachment is what lets a request that
    raised still be persisted — there is no return value left to carry it, and
    a failed request is the one worth recording most.

    Args:
        flow: The flow this request belongs to, which decides the blue its
            record is written in.
        module: The module bracket the record is written under.
        id_request: The API's id for this request, echoed into its event
            tracking. Empty for a run opened outside the public facade.

    Yields:
        Processing: The handle to report the request through.
    """

    run = Processing(flow, module, id_request)
    token = bind_run(run)

    try:
        try:
            yield run
        except BaseException as error:
            run._close(error)

            try:
                setattr(error, METRICS_ATTR, run.event_tracking())
            except AttributeError:  # pragma: no cover - an exception with __slots__
                pass

            raise

        run._close()
    finally:
        unbind_run(token)
