from .builder import PromptSpec, build_prompt
from .continuous_improvement_coordinator import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
    PLAN_SECTIONS,
)
from .faq import FAQ_SPEC
from .green_gases_analyst import GREEN_GASES_ANALYST_SPEC
from .orchestrator import ORCHESTRATOR_SPEC
from .output_guardrail import OUTPUT_GUARDRAIL_SPEC
from .pollutants_analyst import POLLUTANTS_ANALYST_SPEC
from .report_analyst import REPORT_ANALYST_SPEC
from .router import ROUTER_SPEC

# Keyed by each spec's own `agent` field, which is exactly the name the graph
# routes by (see aeko/engine/graph/builder.py) and the key `create_agents()`
# registers the agent under. Keeping a single source for that name is what lets
# `AekoMessenger.set_tools()` accept agent names and reach the right prompt.
#
# The specs are the only thing exported: a prompt is always built from one at
# agent-build time (see `_build_agent`), since its tool section depends on the
# tools registered for that agent.
PROMPT_SPECS: dict[str, PromptSpec] = {
    spec.agent: spec
    for spec in (
        ROUTER_SPEC,
        FAQ_SPEC,
        ORCHESTRATOR_SPEC,
        OUTPUT_GUARDRAIL_SPEC,
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
    "ORCHESTRATOR_SPEC",
    "OUTPUT_GUARDRAIL_SPEC",
    "PLAN_SECTIONS",
    "POLLUTANTS_ANALYST_SPEC",
    "PROMPT_SPECS",
    "PromptSpec",
    "REPORT_ANALYST_SPEC",
    "ROUTER_SPEC",
    "build_prompt",
]
