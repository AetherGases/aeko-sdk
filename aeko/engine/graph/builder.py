from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from typing import Any

from aeko.engine.graph import nodes
from aeko.engine.graph.state import AetherGraphState

GUARD_RAIL_MAX_RETRIES = 3

# The report flow, as the graph knows it: entered here by `AekoInventoryAnalyzer`
# (which owns the same name as `INVENTORY_ENTRY_POINT` — the engine cannot import
# it from the SDK facade without a cycle), moving only between these analysts and
# ending at this coordinator. Every other agent belongs to the conversational
# flow, which ends at the guardrail this one is built never to reach.
INVENTORY_ENTRY_POINT = "Análista de inventários"
REPORT_FLOW_ANALYSTS = ("Analista de Poluentes", "Analista de Gases Verdes")
IMPROVEMENT_COORDINATOR = "Coordenador de Melhoria Contínua"


def _entry_router(state: AetherGraphState, config: RunnableConfig) -> str:
    """
    Decide which node the graph run should start at.

    Args:
        state: The current graph state (unused, kept for the router signature).
        config: The run's configuration, optionally carrying an "entry_point".

    Returns:
        str: The configured entry point, defaulting to "Roteador".
    """

    return config.get("configurable", {}).get("entry_point", "Roteador")


def _route_by_next_agent(default: str):
    """
    Build a router that forwards to the agent named in the state's "next_agent".

    Args:
        default: The node to route to when no "next_agent" is set.

    Returns:
        Callable[[AetherGraphState], str]: A routing function for conditional edges.
    """

    def route(state: AetherGraphState) -> str:
        next_agent = state.get("next_agent")
        return next_agent["agent"] if next_agent else default

    return route


def _route_from_analyst(default: str, *, current: str = ""):
    """
    Build a router used after an analyst node runs.

    Behaves like `_route_by_next_agent`, except that a run which entered through
    the inventory analyst may only ever move on to another analyst of the report
    flow. Any other target means the analysis is finished, and the run goes to
    the improvement coordinator.

    Each agent names its own successor in its own text, so nothing but this
    stops one of them from pointing at an agent that belongs to the
    conversational flow — the same reason `_roteador_node` overrides the
    router's choice in code. Left alone, such a target is not merely the wrong
    node: it is one the edge's path map does not carry, so the whole run dies on
    a `KeyError` after every analyst has already been paid for.

    An agent naming itself is read as naming nothing: no prompt offers an agent
    its own name and no edge carries one, so it is a non-answer rather than a
    hop, and it takes the same default an empty handoff takes.

    Args:
        default: The node to route to when no "next_agent" is set.
        current: The node this edge leaves from, so its own name can be
            recognized as a non-answer.

    Returns:
        Callable[[AetherGraphState, RunnableConfig], str]: A routing function for
            conditional edges.
    """

    def route(state: AetherGraphState, config: RunnableConfig) -> str:
        next_agent = state.get("next_agent")
        target = next_agent["agent"] if next_agent else default

        if target == current:
            target = default

        entry_point = config.get("configurable", {}).get("entry_point", "Roteador")
        if entry_point == INVENTORY_ENTRY_POINT and target not in REPORT_FLOW_ANALYSTS:
            return IMPROVEMENT_COORDINATOR

        return target

    return route


def _route_from_guardrail(state: AetherGraphState) -> str:
    """
    Decide whether the guardrail's verdict ends the run or sends it back for retry.

    Args:
        state: The current graph state, carrying the guardrail's verdict.

    Returns:
        str: END when approved or the retry cap is exceeded, otherwise "Roteador".
    """

    if state["guard_rail_approved"] or state["guard_rail_retries"] > GUARD_RAIL_MAX_RETRIES:
        return END

    return "Roteador"


def build_graph() -> StateGraph:
    """
    Assemble the Aether multi-agent state graph, wiring every node and edge.

    Returns:
        StateGraph: The uncompiled graph, ready for `.compile()`.
    """

    graph = StateGraph(AetherGraphState)

    graph.add_node("Roteador", nodes.roteador_node)
    graph.add_node("FAQ", nodes.faq_node)
    graph.add_node("Orquestrador", nodes.orquestrador_node)
    graph.add_node("Guardrail de Saída", nodes.guardrail_node)
    graph.add_node("Análista de inventários", nodes.analista_inventarios_node)
    graph.add_node("Analista de Poluentes", nodes.analista_poluentes_node)
    graph.add_node("Analista de Gases Verdes", nodes.analista_gases_verdes_node)
    graph.add_node("Coordenador de Melhoria Contínua", nodes.coordenador_melhoria_node)

    graph.add_conditional_edges(
        START,
        _entry_router,
        {
            "Roteador": "Roteador",
            "Análista de inventários": "Análista de inventários",
        },
    )

    graph.add_conditional_edges(
        "Roteador",
        _route_by_next_agent("FAQ"),
        {
            "FAQ": "FAQ",
            "Analista de Poluentes": "Analista de Poluentes",
            "Analista de Gases Verdes": "Analista de Gases Verdes",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
            "Orquestrador": "Orquestrador",
        },
    )

    graph.add_edge("FAQ", END)

    graph.add_conditional_edges(
        "Análista de inventários",
        _route_from_analyst("Analista de Poluentes", current="Análista de inventários"),
        {
            "Analista de Poluentes": "Analista de Poluentes",
            "Analista de Gases Verdes": "Analista de Gases Verdes",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
        },
    )

    graph.add_conditional_edges(
        "Analista de Poluentes",
        _route_from_analyst("Orquestrador", current="Analista de Poluentes"),
        {
            "Analista de Gases Verdes": "Analista de Gases Verdes",
            "Orquestrador": "Orquestrador",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
        },
    )

    graph.add_conditional_edges(
        "Analista de Gases Verdes",
        _route_from_analyst("Orquestrador", current="Analista de Gases Verdes"),
        {
            "Analista de Poluentes": "Analista de Poluentes",
            "Orquestrador": "Orquestrador",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
        },
    )

    graph.add_edge("Coordenador de Melhoria Contínua", END)

    graph.add_edge("Orquestrador", "Guardrail de Saída")

    graph.add_conditional_edges(
        "Guardrail de Saída",
        _route_from_guardrail,
        {
            "Roteador": "Roteador",
            END: END,
        },
    )

    return graph


AETHER_GRAPH = build_graph()


def get_app() -> Any:
    """
    Compile the graph.

    The conversation is supplied by the caller on every run — `AekoMessenger`
    seeds it from the session the API hands over — so the graph is compiled
    without a checkpointer.

    Returns:
        Any: The compiled graph, ready to `.invoke()`.
    """

    return AETHER_GRAPH.compile()
