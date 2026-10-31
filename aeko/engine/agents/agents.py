"""Build the SDK's LangChain agents from prompt specs and registered tools."""

from dataclasses import replace

from types import SimpleNamespace

from typing import Any


from langchain.agents import create_agent

from langchain.agents.middleware import ToolErrorMiddleware

from langchain_core.language_models import BaseChatModel

from langchain_core.tools import StructuredTool

from langchain_core.tools.simple import Tool as SimpleTool


from aeko.engine.agents.constants import FAST_AGENTS, REVIEWER_AGENTS

from aeko.engine.agents.llms import create_llms

from aeko.engine.agents.writes import is_writing, stage_writes

from aeko.engine.prompts import PROMPT_SPECS, PromptSpec, build_prompt

from aeko.engine.runtime import RUNTIME


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

    return f"{getattr(tool, 'name', type(tool).__name__)} - {getattr(tool, 'description', '')}".rstrip(
        " -"
    )


def _as_structured_tool(tool: Any) -> Any:
    """
    Promote a single-input Tool whose schema has the wrong arity.

    LangChain's `Tool` still enforces one argument at invoke time even when an
    `args_schema` with several fields is attached. The consuming API registers
    tools that way (`update_user_profile` with `role` and `usecase`), so a
    normal model call raises `ToolException` and used to abort the request.
    `StructuredTool` is the same function with the schema actually honoured,
    and keeps the metadata, so an "is_writing" flag still stages its calls.

    Args:
        tool: A LangChain tool, already unwrapped from `AekoTool`.

    Returns:
        Any: A `StructuredTool` when the original was a simple `Tool` with
            zero or 2+ arguments, otherwise the original tool.
    """

    if not isinstance(tool, SimpleTool):
        return tool

    arg_keys = list(getattr(tool, "args", {}) or {})

    if len(arg_keys) == 1:
        return tool

    return StructuredTool.from_function(
        func=tool.func,
        coroutine=tool.coroutine,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=tool.return_direct,
        metadata=tool.metadata,
    )


def _on_tool_error(exc: Exception, request: Any) -> str:
    """
    Turn a tool-execution exception into text the model can recover from.

    Without this, LangGraph's default handler only swallows
    `ToolInvocationError` and re-raises everything else — including a tool
    that raised `ValueError` — which escaped `send_message` as a 500.

    Args:
        exc: The exception raised while running the tool.
        request: The tool-call request LangChain hands the middleware.

    Returns:
        str: An observation naming the tool and the exception type.
    """

    tool_call = getattr(request, "tool_call", None) or {}

    name = tool_call.get("name") if isinstance(tool_call, dict) else None

    detail = str(exc).strip().splitlines()[0][:200]

    return f"Tool `{name or 'tool'}` failed with {type(exc).__name__}: {detail}"


def _to_lc_tool(tool: Any) -> Any:
    """
    Unwrap the LangChain tool an `AekoTool` carries.

    A simple `Tool` whose schema is not a single argument is promoted to a
    `StructuredTool` here, so the bound object can actually accept the dict
    the model sends.

    Args:
        tool: An `AekoTool` wrapper, or an already-bare LangChain tool.

    Returns:
        Any: The tool object to bind to the agent executor.
    """

    return stage_writes(_as_structured_tool(getattr(tool, "tool", tool)))


class _AgentCompatibilityWrapper:
    """Keep the SDK's executor-shaped contract over a create_agent graph."""

    def __init__(self, graph: Any, prompt: Any, llm: BaseChatModel, tools: list[Any]):
        """Store the graph, prompt, model and tools the SDK will invoke through."""

        self._graph = graph

        self.tools = tools

        self.agent = SimpleNamespace(runnable=SimpleNamespace(steps=[prompt, llm]))

    def invoke(
        self, input: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke the agent graph and expose the last message as ``output``."""

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

    lc_tools = [_to_lc_tool(tool) for tool in tools]

    prompt = build_prompt(
        replace(
            spec,
            tools=[_to_prompt_line(tool) for tool in tools],
            has_writing_tools=any(is_writing(tool) for tool in lc_tools),
        )
    )

    system_message = prompt.invoke({"messages": []}).messages[0]

    graph = create_agent(
        llm,
        tools=lc_tools,
        system_prompt=system_message,
        middleware=[ToolErrorMiddleware(_on_tool_error)],
    )

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

    reviewer_llm, _ = create_llms(max_tokens=max_tokens, temperature=0)

    return {
        name: _build_agent(
            reviewer_llm
            if name in REVIEWER_AGENTS
            else fast_llm
            if name in FAST_AGENTS
            else slow_llm,
            spec,
            RUNTIME.tools.get(name, []),
        )
        for name, spec in PROMPT_SPECS.items()
    }
