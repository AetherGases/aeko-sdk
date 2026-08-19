from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from system.engine.graph import nodes
from system.engine.graph.state import AetherGraphState

GUARD_RAIL_MAX_RETRIES = 3


def _entry_router(state: AetherGraphState, config: RunnableConfig) -> str:
    return config.get("configurable", {}).get("entry_point", "Roteador")


def _route_by_next_agent(default: str):
    def route(state: AetherGraphState) -> str:
        next_agent = state.get("next_agent")
        return next_agent["agent"] if next_agent else default

    return route


def _route_from_analyst(default: str):
    def route(state: AetherGraphState, config: RunnableConfig) -> str:
        next_agent = state.get("next_agent")
        target = next_agent["agent"] if next_agent else default

        entry_point = config.get("configurable", {}).get("entry_point", "Roteador")
        if target == "Orquestrador" and entry_point == "Análista de inventários":
            return "Coordenador de Melhoria Contínua"

        return target

    return route


def _route_from_guardrail(state: AetherGraphState) -> str:
    last_message = state["messages"][-1]["content"]
    approved = last_message.strip().lower().startswith("aprovado")

    if approved or state["guard_rail_retries"] > GUARD_RAIL_MAX_RETRIES:
        return END

    return "Roteador"


def build_graph() -> StateGraph:
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
            "Análista de inventários": "Análista de inventários",
            "Analista de Poluentes": "Analista de Poluentes",
            "Analista de Gases Verdes": "Analista de Gases Verdes",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
            "Orquestrador": "Orquestrador",
        },
    )

    graph.add_edge("FAQ", END)

    graph.add_conditional_edges(
        "Análista de inventários",
        _route_by_next_agent("Analista de Poluentes"),
        {
            "Analista de Poluentes": "Analista de Poluentes",
            "Analista de Gases Verdes": "Analista de Gases Verdes",
        },
    )

    graph.add_conditional_edges(
        "Analista de Poluentes",
        _route_from_analyst("Orquestrador"),
        {
            "Analista de Gases Verdes": "Analista de Gases Verdes",
            "Orquestrador": "Orquestrador",
            "Coordenador de Melhoria Contínua": "Coordenador de Melhoria Contínua",
        },
    )

    graph.add_conditional_edges(
        "Analista de Gases Verdes",
        _route_from_analyst("Orquestrador"),
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
AETHER_APP = AETHER_GRAPH.compile()
