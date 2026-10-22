"""Prompt specs for every agent the graph can route to."""

from .builder import PromptSpec, build_prompt
from .constants import PLAN_SECTIONS
from .continuous_improvement_coordinator import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
    INVENTORY_PAYLOAD_INSTRUCTIONS,
)
from .faq import FAQ_SPEC
from .green_gases_analyst import GREEN_GASES_ANALYST_SPEC
from .orchestrator import ORCHESTRATOR_SPEC
from .output_guardrail import OUTPUT_GUARDRAIL_SPEC
from .pollutants_analyst import POLLUTANTS_ANALYST_SPEC
from .report_analyst import REPORT_ANALYST_SPEC
from .response_checker import RESPONSE_CHECKER_SPEC
from .router import ROUTER_SPEC

PROMPT_SPECS: dict[str, PromptSpec] = {
    spec.agent: spec
    for spec in (
        ROUTER_SPEC,
        FAQ_SPEC,
        ORCHESTRATOR_SPEC,
        OUTPUT_GUARDRAIL_SPEC,
        RESPONSE_CHECKER_SPEC,
        REPORT_ANALYST_SPEC,
        POLLUTANTS_ANALYST_SPEC,
        GREEN_GASES_ANALYST_SPEC,
        CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
    )
}

AGENT_NAMES: tuple[str, ...] = tuple(PROMPT_SPECS)

__all__ = [
    "AGENT_NAMES",
    "CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC",
    "FAQ_SPEC",
    "GREEN_GASES_ANALYST_SPEC",
    "INVENTORY_PAYLOAD_INSTRUCTIONS",
    "ORCHESTRATOR_SPEC",
    "OUTPUT_GUARDRAIL_SPEC",
    "PLAN_SECTIONS",
    "POLLUTANTS_ANALYST_SPEC",
    "PROMPT_SPECS",
    "PromptSpec",
    "REPORT_ANALYST_SPEC",
    "RESPONSE_CHECKER_SPEC",
    "ROUTER_SPEC",
    "build_prompt",
]
