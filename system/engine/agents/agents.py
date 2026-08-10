from typing import Any
from langgraph.prebuilt import create_react_agent
from system.engine.agents.llms import create_llms
from system.engine.prompts import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT,
    FAQ_PROMPT,
    GREEN_GASES_ANALYST_PROMPT,
    ORCHESTRATOR_PROMPT,
    POLLUTANTS_ANALYST_PROMPT,
    REPORT_ANALYST_PROMPT,
    ROUTER_PROMPT
)

def create_agents() -> dict[str, Any]:
    """
    Create agents for the system.

    Returns:
        dict[str, Agent]: A dictionary containing the created agents.
    """

    agents = {}

    fast_llm, slow_llm = create_llms()

    # Create the agents using the respective prompts

    # Fast llms
    agents["Roteador"] = create_react_agent(model= fast_llm, tools= [], prompt= ROUTER_PROMPT)
    agents["FAQ"] = create_react_agent(model= fast_llm, tools= [], prompt= FAQ_PROMPT)
    agents["Orquestrador"] = create_react_agent(model= fast_llm, tools= [], prompt= ORCHESTRATOR_PROMPT)

    # High-effort llms
    agents["Análista de inventários"] = create_react_agent(model= slow_llm, tools= [], prompt= REPORT_ANALYST_PROMPT)
    agents["Analista de Poluentes"] = create_react_agent(model= slow_llm, tools= [], prompt= POLLUTANTS_ANALYST_PROMPT)
    agents["Analista de Gases Verdes"] = create_react_agent(model= slow_llm, tools= [], prompt= GREEN_GASES_ANALYST_PROMPT)
    agents["Coordenador de Melhoria Contínua"] = create_react_agent(model= slow_llm, tools= [], prompt= CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT)

    return agents

