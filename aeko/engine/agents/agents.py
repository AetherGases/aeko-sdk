from dataclasses import replace
from typing import Any

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import MessagesPlaceholder

from aeko.engine.agents.llms import create_llms
from aeko.engine.prompts import PROMPT_SPECS, PromptSpec, build_prompt
from aeko.engine.runtime import RUNTIME

# create_tool_calling_agent requires the prompt to reserve a slot for the
# tool-call loop's scratchpad; the shared prompt specs don't have one.
AGENT_SCRATCHPAD = MessagesPlaceholder(variable_name="agent_scratchpad")

# Agents that only classify, consolidate or review — cheap turns, fast model.
# Everything else is a specialist analyst and gets the high-effort model.
FAST_AGENTS = ("Roteador", "FAQ", "Orquestrador", "Guardrail de Saída")


def _to_prompt_line(tool: Any) -> str:
    """
    Render one registered tool as a line of the prompt's tool section.

    Args:
        tool: An `AekoTool`, or any object exposing `.name`/`.description`.

    Returns:
        str: The "<name> - <description>" line the prompt spec expects.
    """

    if hasattr(tool, "to_prompt_line"):
        return tool.to_prompt_line()

    return f"{getattr(tool, 'name', type(tool).__name__)} - {getattr(tool, 'description', '')}".rstrip(" -")


def _to_lc_tool(tool: Any) -> Any:
    """
    Unwrap the LangChain tool an `AekoTool` carries.

    Args:
        tool: An `AekoTool` wrapper, or an already-bare LangChain tool.

    Returns:
        Any: The tool object to bind to the agent executor.
    """

    return getattr(tool, "tool", tool)


def _build_agent(llm: BaseChatModel, spec: PromptSpec, tools: list[Any]) -> AgentExecutor:
    """
    Build a tool-calling agent executor from an LLM, a prompt spec and its tools.

    The tools are used twice, from the same declaration: their descriptions are
    rendered into the prompt's "# Ferramentas Disponiveis" section, and the tool
    objects themselves are bound to the executor. That is what keeps the prompt
    from advertising a tool the agent cannot actually call.

    Args:
        llm: The chat model backing the agent.
        spec: The agent's prompt spec, whose `tools` field is replaced by the
            rendered descriptions of `tools`.
        tools: The tools registered for this agent.

    Returns:
        AgentExecutor: An agent executor ready to be invoked.
    """

    prompt = build_prompt(replace(spec, tools=[_to_prompt_line(tool) for tool in tools]))
    lc_tools = [_to_lc_tool(tool) for tool in tools]

    tool_calling_agent = create_tool_calling_agent(llm, lc_tools, prompt + AGENT_SCRATCHPAD)
    return AgentExecutor(agent=tool_calling_agent, tools=lc_tools)


def create_agents(max_tokens: int | None = None) -> dict[str, Any]:
    """
    Create every agent of the system, wiring each one to its registered tools.

    Both the tools and the credentials come from the runtime, which is the only
    place they are configured (see `AekoMessenger.set_tools()` and
    `Aeko.config()`).

    Args:
        max_tokens: The output token cap the agents are built with. Defaults to
            the configured conversational one — this is how the inventory report
            flow gets more room than a chat answer.

    Returns:
        dict[str, AgentExecutor]: The agents, keyed by the exact names the graph
            routes by.

    Raises:
        AekoNotConfiguredError: If no API key was configured.
    """

    fast_llm, slow_llm = create_llms(max_tokens=max_tokens)

    return {
        name: _build_agent(
            fast_llm if name in FAST_AGENTS else slow_llm, spec, RUNTIME.tools.get(name, [])
        )
        for name, spec in PROMPT_SPECS.items()
    }
