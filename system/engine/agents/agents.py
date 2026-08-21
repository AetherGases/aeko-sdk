from typing import Any

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from system.engine.agents.llms import create_llms
from system.engine.prompts import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT,
    FAQ_PROMPT,
    GREEN_GASES_ANALYST_PROMPT,
    ORCHESTRATOR_PROMPT,
    OUTPUT_GUARDRAIL_PROMPT,
    POLLUTANTS_ANALYST_PROMPT,
    REPORT_ANALYST_PROMPT,
    ROUTER_PROMPT
)

# create_tool_calling_agent requires the prompt to reserve a slot for the
# tool-call loop's scratchpad; the shared X_PROMPT templates don't have one.
AGENT_SCRATCHPAD = MessagesPlaceholder(variable_name="agent_scratchpad")


def _build_agent(llm: BaseChatModel, prompt: ChatPromptTemplate) -> AgentExecutor:
    """
    Build a tool-calling agent executor from an LLM and a prompt template.

    Args:
        llm: The chat model backing the agent.
        prompt: The prompt template driving the agent's behavior.

    Returns:
        AgentExecutor: A tool-less agent executor ready to be invoked.
    """

    tool_calling_agent = create_tool_calling_agent(llm, [], prompt + AGENT_SCRATCHPAD)
    return AgentExecutor(agent=tool_calling_agent, tools=[])


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
    agents["Roteador"] = _build_agent(fast_llm, ROUTER_PROMPT)
    agents["FAQ"] = _build_agent(fast_llm, FAQ_PROMPT)
    agents["Orquestrador"] = _build_agent(fast_llm, ORCHESTRATOR_PROMPT)
    agents["Guardrail de Saída"] = _build_agent(fast_llm, OUTPUT_GUARDRAIL_PROMPT)

    # High-effort llms
    agents["Análista de inventários"] = _build_agent(slow_llm, REPORT_ANALYST_PROMPT)
    agents["Analista de Poluentes"] = _build_agent(slow_llm, POLLUTANTS_ANALYST_PROMPT)
    agents["Analista de Gases Verdes"] = _build_agent(slow_llm, GREEN_GASES_ANALYST_PROMPT)
    agents["Coordenador de Melhoria Contínua"] = _build_agent(slow_llm, CONTINUOUS_IMPROVEMENT_COORDINATOR_PROMPT)

    return agents

