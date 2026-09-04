"""
Cross-cutting infrastructure, shared by every part of the SDK.

Nothing here imports from `aeko.config` or `aeko.engine`: this package is the
bottom of the dependency graph, which is what lets the engine and the public
facade both log without either of them importing the other.
"""

from aeko.shared.colors import BLUE, LIGHT_BLUE, RED, RESET, colorize, success_color
from aeko.shared.context import Flow, current_run
from aeko.shared.logger import (
    ITEM_PREFIX,
    LOG_PREFIX,
    LOGGER_NAME,
    AekoFormatter,
    Processing,
    configure_logging,
    log_failure,
    log_success,
    processing,
    record_agent_call,
)

__all__ = [
    "AekoFormatter",
    "BLUE",
    "Flow",
    "ITEM_PREFIX",
    "LIGHT_BLUE",
    "LOGGER_NAME",
    "LOG_PREFIX",
    "Processing",
    "RED",
    "RESET",
    "colorize",
    "configure_logging",
    "current_run",
    "log_failure",
    "log_success",
    "processing",
    "record_agent_call",
    "success_color",
]
