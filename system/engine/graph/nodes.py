from typing import Callable

from system.engine.graph.state import AetherGraphState, NextAgent
from system.engine.agents.agents import create_agents

_AGENTS = create_agents()


def _invoke_agent(agent_name: str, state: AetherGraphState) -> tuple[str, NextAgent | None]:
    output = _AGENTS[agent_name].invoke({"messages": state["messages"]})["output"]

    raw_next = output.split("Next agent: ")[-1].strip() if "Next agent: " in output else ""

    if raw_next in ("", "Nenhum"):
        next_agent = None
    elif raw_next not in _AGENTS:
        raise ValueError(f"Next agent '{raw_next}' is not a valid agent name.")
    else:
        next_agent: NextAgent = {"agent": raw_next, "message": output}

    return output, next_agent


def _specialist_node_factory(agent_name: str) -> Callable[[AetherGraphState], dict]:
    def node(state: AetherGraphState) -> dict:
        output, next_agent = _invoke_agent(agent_name, state)

        return {
            "messages": [{"role": "assistant", "content": output}],
            "previous_agents": {agent_name: output},
            "pending_agents": [{"agent": agent_name, "is_still_pending": False}],
            "next_agent": next_agent,
        }

    return node


def _non_specialist_node_factory(agent_name: str) -> Callable[[AetherGraphState], dict]:
    def node(state: AetherGraphState) -> dict:
        output, next_agent = _invoke_agent(agent_name, state)

        return {
            "messages": [{"role": "assistant", "content": output}],
            "next_agent": next_agent,
        }

    return node


def _guardrail_node(state: AetherGraphState) -> dict:
    output, next_agent = _invoke_agent("Guardrail de Saída", state)

    update = {
        "messages": [{"role": "assistant", "content": output}],
        "next_agent": next_agent,
    }

    if not output.strip().lower().startswith("aprovado"):
        update["guard_rail_requested_changes"] = [output]
        update["guard_rail_retries"] = state["guard_rail_retries"] + 1

    return update


roteador_node = _non_specialist_node_factory("Roteador")
faq_node = _non_specialist_node_factory("FAQ")
orquestrador_node = _non_specialist_node_factory("Orquestrador")
guardrail_node = _guardrail_node

analista_inventarios_node = _specialist_node_factory("Análista de inventários")
analista_poluentes_node = _specialist_node_factory("Analista de Poluentes")
analista_gases_verdes_node = _specialist_node_factory("Analista de Gases Verdes")
coordenador_melhoria_node = _specialist_node_factory("Coordenador de Melhoria Contínua")
