"""Constants used by shared logging, tracking and text-protocol utilities."""

from aeko.shared.context import Flow

LOG_PREFIX = "aeko-sdk"
LOGGER_NAME = "aeko"
ITEM_PREFIX = "  - "
MODULE_KEY = "aeko_module"
COLOR_KEY = "aeko_color"
METRICS_ATTR = "aeko_metrics"
ROUTING_MARKER = "Next agent: "
RESET = "\033[0m"
RED = "\033[31m"
BLUE = "\033[34m"
LIGHT_BLUE = "\033[94m"
EVENT_TRACKING_FLOWS = {
    Flow.CONVERSATIONAL: "conversational",
    Flow.REPORT: "analytical",
}
FLOW_COLORS: dict[Flow, str] = {
    Flow.CONVERSATIONAL: LIGHT_BLUE,
    Flow.REPORT: BLUE,
}
