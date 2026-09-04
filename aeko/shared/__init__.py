"""
Cross-cutting infrastructure, shared by every part of the SDK.

Nothing here imports from `aeko.config` or `aeko.engine`: this package is the
bottom of the dependency graph, which is what lets the engine and the public
facade both log — and both be measured — without either of them importing the
other.
"""

from aeko.shared.colors import BLUE, LIGHT_BLUE, RED, RESET, colorize, success_color
from aeko.shared.context import Flow, current_run
from aeko.shared.event_tracking import (
    EVENT_TRACKING_FLOWS,
    METRICS_ATTR,
    AekoAgentMetrics,
    AekoMetrics,
    AgentCall,
    AgentCallCollector,
    agent_call,
)
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
    "AekoAgentMetrics",
    "AekoFormatter",
    "AekoMetrics",
    "AgentCall",
    "AgentCallCollector",
    "BLUE",
    "EVENT_TRACKING_FLOWS",
    "Flow",
    "ITEM_PREFIX",
    "LIGHT_BLUE",
    "LOGGER_NAME",
    "LOG_PREFIX",
    "METRICS_ATTR",
    "Processing",
    "RED",
    "RESET",
    "agent_call",
    "colorize",
    "configure_logging",
    "current_run",
    "log_failure",
    "log_success",
    "processing",
    "record_agent_call",
    "success_color",
]
