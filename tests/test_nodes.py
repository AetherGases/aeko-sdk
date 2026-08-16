from langchain_core.messages import HumanMessage

from system.engine.graph import nodes
from system.engine.graph.state import create_initial_state


class _FakeAgent:
    def __init__(self, output: str):
        self.output = output
        self.last_input = None

    def invoke(self, input_):
        self.last_input = input_
        return {"output": self.output}


def _state_with(**overrides):
    state = create_initial_state("Pergunta de teste")
    state.update(overrides)
    return state


def test_specialist_node_factory_returns_independent_closures(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Analista de Poluentes": _FakeAgent("Analise A.\nNext agent: Nenhum"),
        "Analista de Gases Verdes": _FakeAgent("Analise B.\nNext agent: Nenhum"),
    })

    node_a = nodes._specialist_node_factory("Analista de Poluentes")
    node_b = nodes._specialist_node_factory("Analista de Gases Verdes")

    assert callable(node_a)
    assert callable(node_b)
    assert node_a is not node_b

    result_a = node_a(_state_with())
    result_b = node_b(_state_with())

    assert result_a["previous_agents"] == {"Analista de Poluentes": "Analise A.\nNext agent: Nenhum"}
    assert result_b["previous_agents"] == {"Analista de Gases Verdes": "Analise B.\nNext agent: Nenhum"}


def test_specialist_node_returns_previous_agents_and_pending_agents_delta(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Analista de Poluentes": _FakeAgent("Analise concluida.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    node = nodes._specialist_node_factory("Analista de Poluentes")
    result = node(_state_with())

    assert result["previous_agents"] == {"Analista de Poluentes": "Analise concluida.\nNext agent: Orquestrador"}
    assert result["pending_agents"] == [{"agent": "Analista de Poluentes", "is_still_pending": False}]
    assert result["next_agent"] == {"agent": "Orquestrador", "message": "Analise concluida.\nNext agent: Orquestrador"}
    assert result["messages"] == [{"role": "assistant", "content": "Analise concluida.\nNext agent: Orquestrador"}]


def test_non_specialist_node_does_not_touch_previous_or_pending_agents(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
        "FAQ": _FakeAgent("dummy"),
    })

    node = nodes._non_specialist_node_factory("Roteador")
    result = node(_state_with())

    assert "previous_agents" not in result
    assert "pending_agents" not in result
    assert result["next_agent"] == {"agent": "FAQ", "message": "Direcionando.\nNext agent: FAQ"}


def test_next_agent_is_none_when_marker_absent(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "FAQ": _FakeAgent("Resposta sem marcador de proximo agente."),
    })

    node = nodes._non_specialist_node_factory("FAQ")
    result = node(_state_with())

    assert result["next_agent"] is None


def test_next_agent_is_none_when_nenhum(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Coordenador de Melhoria Contínua": _FakeAgent("Plano finalizado.\nNext agent: Nenhum"),
    })

    node = nodes._specialist_node_factory("Coordenador de Melhoria Contínua")
    result = node(_state_with())

    assert result["next_agent"] is None


def test_next_agent_raises_for_unknown_agent_name(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Agente Inexistente"),
    })

    node = nodes._non_specialist_node_factory("Roteador")

    try:
        node(_state_with())
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_node_passes_state_messages_to_agent(monkeypatch):
    fake_agent = _FakeAgent("Ok.\nNext agent: Nenhum")
    monkeypatch.setattr(nodes, "_AGENTS", {"FAQ": fake_agent})

    node = nodes._non_specialist_node_factory("FAQ")
    state = _state_with(messages=[HumanMessage(content="Oi")])
    node(state)

    assert fake_agent.last_input == {"messages": state["messages"]}


def test_module_level_nodes_are_created_and_callable():
    non_specialist_nodes = [
        nodes.roteador_node,
        nodes.faq_node,
        nodes.orquestrador_node,
        nodes.guardrail_node,
    ]
    specialist_nodes = [
        nodes.analista_inventarios_node,
        nodes.analista_poluentes_node,
        nodes.analista_gases_verdes_node,
        nodes.coordenador_melhoria_node,
    ]

    all_nodes = non_specialist_nodes + specialist_nodes

    assert all(callable(node) for node in all_nodes)
    assert len(set(id(node) for node in all_nodes)) == len(all_nodes)
