from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from aeko.engine.agents.llms import create_llms
from aeko.engine.prompts import PROMPT_SPECS, PromptSpec, build_prompt
from aeko.engine.runtime import RUNTIME

# Agents that only classify, consolidate or review — cheap turns, fast model.
# Everything else is a specialist analyst and gets the high-effort model. The
# response checker belongs here for the same reason the guardrail does: it
# produces no analysis of its own, only a verdict on text it was handed, and it
# runs on every conversational turn, so its cost is paid by all of them.
FAST_AGENTS = (
    "Roteador",
    "FAQ",
    "Orquestrador",
    "Guardrail de Saída",
    "Verificador de Resposta",
)


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


class _AgentCompatibilityWrapper:
    """Keep the SDK's executor-shaped contract over a create_agent graph."""

    def __init__(self, graph: Any, prompt: Any, llm: BaseChatModel, tools: list[Any]):
        self._graph = graph
        self.tools = tools
        self.agent = SimpleNamespace(runnable=SimpleNamespace(steps=[prompt, llm]))

    def invoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._graph.invoke(input, config=config)
        messages = result.get("messages", [])
        output = messages[-1].content if messages else ""
        return {**result, "output": output}


def _build_agent(llm: BaseChatModel, spec: PromptSpec, tools: list[Any]) -> Any:
    """
    Build a tool-calling agent executor from an LLM, a prompt spec and its tools.

    The tools are used twice, from the same declaration: their descriptions are
    rendered into the prompt's "# Ferramentas Disponiveis" section, and the tool
    objects themselves are bound to the agent. That is what keeps the prompt
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

    system_message = prompt.invoke({"messages": []}).messages[0]
    graph = create_agent(llm, tools=lc_tools, system_prompt=system_message)
    return _AgentCompatibilityWrapper(graph, prompt, llm, lc_tools)


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
        dict[str, Any]: The agents, keyed by the exact names the graph
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
