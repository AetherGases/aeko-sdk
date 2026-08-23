from .builder import PromptSpec, build_prompt
from .continuous_improvement_coordinator import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT,
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
)
from .faq import FAQ_PROMPT, FAQ_SPEC
from .green_gases_analyst import GREEN_GASES_ANALYST_PROMPT, GREEN_GASES_ANALYST_SPEC
from .orchestrator import ORCHESTRATOR_PROMPT, ORCHESTRATOR_SPEC
from .output_guardrail import OUTPUT_GUARDRAIL_PROMPT, OUTPUT_GUARDRAIL_SPEC
from .pollutants_analyst import POLLUTANTS_ANALYST_PROMPT, POLLUTANTS_ANALYST_SPEC
from .report_analyst import REPORT_ANALYST_PROMPT, REPORT_ANALYST_SPEC
from .router import ROUTER_PROMPT, ROUTER_SPEC

# Keyed by each spec's own `agent` field, which is exactly the name the graph
# routes by (see system/engine/graph/builder.py) and the key `create_agents()`
# registers the agent under. Keeping a single source for that name is what lets
# `AekoMessenger.set_tools()` accept agent names and reach the right prompt.
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
    "CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT",
    "CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC",
    "FAQ_PROMPT",
    "FAQ_SPEC",
    "GREEN_GASES_ANALYST_PROMPT",
    "GREEN_GASES_ANALYST_SPEC",
    "ORCHESTRATOR_PROMPT",
    "ORCHESTRATOR_SPEC",
    "OUTPUT_GUARDRAIL_PROMPT",
    "OUTPUT_GUARDRAIL_SPEC",
    "POLLUTANTS_ANALYST_PROMPT",
    "POLLUTANTS_ANALYST_SPEC",
    "PROMPT_SPECS",
    "PromptSpec",
    "REPORT_ANALYST_PROMPT",
    "REPORT_ANALYST_SPEC",
    "ROUTER_PROMPT",
    "ROUTER_SPEC",
    "build_prompt",
]