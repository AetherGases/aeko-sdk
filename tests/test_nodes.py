from langchain_core.messages import HumanMessage

from aeko.engine.graph import nodes
from aeko.engine.graph.state import create_initial_state


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


# --- _build_context_message ---------------------------------------------


def test_build_context_message_uses_only_initial_question_when_no_prior_agents():
    state = create_initial_state("Pergunta de teste")

    message = nodes._build_context_message(state)

    assert isinstance(message, HumanMessage)
    assert message.content == "Pergunta de teste"


def test_build_context_message_includes_previous_agents_findings():
    state = _state_with(previous_agents={"Analista de Poluentes": "Emissoes criticas de NOx."})

    message = nodes._build_context_message(state)

    assert "Pergunta de teste" in message.content
    assert "Análises recebidas até agora:" in message.content
    assert "- Analista de Poluentes: Emissoes criticas de NOx." in message.content


def test_build_context_message_includes_latest_guardrail_feedback():
    state = _state_with(guard_rail_requested_changes=["Tom acusatorio, reescrever de forma tecnica."])

    message = nodes._build_context_message(state)

    assert "Pergunta de teste" in message.content
    assert (
        "Pontos apontados pelo Guardrail de Saída na tentativa anterior:\n"
        "Tom acusatorio, reescrever de forma tecnica."
    ) in message.content


def test_build_context_message_omits_guardrail_section_when_no_rejection():
    state = _state_with()

    message = nodes._build_context_message(state)

    assert "Pontos apontados pelo Guardrail de Saída" not in message.content


# --- _build_guardrail_message --------------------------------------------


def test_build_guardrail_message_wraps_orquestrador_draft():
    state = _state_with(previous_agents={"Orquestrador": "Resposta consolidada."})

    message = nodes._build_guardrail_message(state)

    assert isinstance(message, HumanMessage)
    assert message.content == (
        "Revise esta resposta: 'Resposta consolidada.'"
        "\n\nPergunta original do usuário: Pergunta de teste"
    )


def test_build_guardrail_message_handles_missing_draft():
    state = _state_with()

    message = nodes._build_guardrail_message(state)

    assert message.content == (
        "Revise esta resposta: ''"
        "\n\nPergunta original do usuário: Pergunta de teste"
    )


def test_build_guardrail_message_includes_specialist_findings():
    state = _state_with(previous_agents={
        "Analista de Poluentes": "Emissoes criticas de NOx.",
        "Orquestrador": "Resposta consolidada.",
    })

    message = nodes._build_guardrail_message(state)

    assert "Pergunta original do usuário: Pergunta de teste" in message.content
    assert "Análises recebidas:" in message.content
    assert "- Analista de Poluentes: Emissoes criticas de NOx." in message.content
    assert "- Orquestrador:" not in message.content


# --- _invoke_agent --------------------------------------------------------


def test_invoke_agent_sends_a_single_message_to_the_agent(monkeypatch):
    fake_agent = _FakeAgent("Ok.\nNext agent: Nenhum")
    monkeypatch.setattr(nodes, "_AGENTS", {"FAQ": fake_agent})

    message = HumanMessage(content="Oi")
    output, next_agent = nodes._invoke_agent("FAQ", message)

    assert fake_agent.last_input == {"messages": [message]}
    assert output == "Ok.\nNext agent: Nenhum"
    assert next_agent is None


def test_invoke_agent_parses_next_agent_marker(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
        "FAQ": _FakeAgent("dummy"),
    })

    output, next_agent = nodes._invoke_agent("Roteador", HumanMessage(content="Oi"))

    assert next_agent == {"agent": "FAQ", "message": "Direcionando.\nNext agent: FAQ"}


def test_invoke_agent_raises_for_unknown_agent_name(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Agente Inexistente"),
    })

    try:
        nodes._invoke_agent("Roteador", HumanMessage(content="Oi"))
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- _specialist_node_factory ---------------------------------------------


def test_specialist_node_factory_is_non_terminal_by_default(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Analista de Poluentes": _FakeAgent("Analise concluida.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    node = nodes._specialist_node_factory("Analista de Poluentes")
    result = node(_state_with())

    assert result["previous_agents"] == {"Analista de Poluentes": "Analise concluida.\nNext agent: Orquestrador"}
    assert result["pending_agents"] == [{"agent": "Analista de Poluentes", "is_still_pending": False}]
    assert result["next_agent"] == {"agent": "Orquestrador", "message": "Analise concluida.\nNext agent: Orquestrador"}
    assert "messages" not in result


def test_specialist_node_factory_terminal_also_writes_messages(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Coordenador de Melhoria Contínua": _FakeAgent("Plano finalizado.\nNext agent: Nenhum"),
    })

    node = nodes._specialist_node_factory("Coordenador de Melhoria Contínua", terminal=True)
    result = node(_state_with())

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Plano finalizado.\nNext agent: Nenhum",
        "name": "Coordenador de Melhoria Contínua",
    }]
    assert result["previous_agents"] == {"Coordenador de Melhoria Contínua": "Plano finalizado.\nNext agent: Nenhum"}


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


# --- _non_specialist_node_factory ------------------------------------------


def test_non_specialist_node_factory_is_non_terminal_by_default(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
        "FAQ": _FakeAgent("dummy"),
    })

    node = nodes._non_specialist_node_factory("Roteador")
    result = node(_state_with())

    assert "previous_agents" not in result
    assert "pending_agents" not in result
    assert "messages" not in result
    assert result["next_agent"] == {"agent": "FAQ", "message": "Direcionando.\nNext agent: FAQ"}


def test_non_specialist_node_factory_terminal_writes_messages(monkeypatch):
    fake_agent = _FakeAgent("Resposta direta ao usuario.\nNext agent: Nenhum")
    monkeypatch.setattr(nodes, "_AGENTS", {"FAQ": fake_agent})

    node = nodes._non_specialist_node_factory("FAQ", terminal=True)
    result = node(_state_with())

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Resposta direta ao usuario.\nNext agent: Nenhum",
        "name": "FAQ",
    }]
    assert result["next_agent"] is None


# --- _roteador_node ---------------------------------------------------------


def test_roteador_node_overrides_premature_orquestrador_routing(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Only text.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    result = nodes._roteador_node(_state_with())

    assert result["next_agent"] == {
        "agent": "FAQ",
        "message": "Only text.\nNext agent: Orquestrador",
    }


def test_roteador_node_allows_orquestrador_when_previous_agents_exist(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(previous_agents={"Analista de Gases Verdes": "Recomendo hidrogenio verde."})
    result = nodes._roteador_node(state)

    assert result["next_agent"] == {"agent": "Orquestrador", "message": "Direcionando.\nNext agent: Orquestrador"}


def test_roteador_node_never_writes_messages(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "FAQ": _FakeAgent("dummy"),
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
    })

    result = nodes._roteador_node(_state_with())

    assert "messages" not in result
    assert "previous_agents" not in result


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


def test_faq_node_sends_a_message_built_from_the_initial_question(monkeypatch):
    fake_agent = _FakeAgent("Ok.\nNext agent: Nenhum")
    monkeypatch.setattr(nodes, "_AGENTS", {"FAQ": fake_agent})

    node = nodes._non_specialist_node_factory("FAQ", terminal=True)
    state = create_initial_state("Oi, tudo bem?")
    node(state)

    assert fake_agent.last_input == {"messages": [HumanMessage(content="Oi, tudo bem?")]}


# --- _orquestrador_node -----------------------------------------------------


def test_orquestrador_node_records_draft_in_previous_agents_not_messages(monkeypatch):
    fake_agent = _FakeAgent("Resposta consolidada.\nNext agent: Guardrail de Saída")
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Orquestrador": fake_agent,
        "Guardrail de Saída": _FakeAgent("dummy"),
    })

    result = nodes._orquestrador_node(_state_with())

    assert result["previous_agents"] == {"Orquestrador": "Resposta consolidada.\nNext agent: Guardrail de Saída"}
    assert "messages" not in result
    assert result["next_agent"] == {
        "agent": "Guardrail de Saída",
        "message": "Resposta consolidada.\nNext agent: Guardrail de Saída",
    }


def test_orquestrador_node_uses_previous_agents_findings_as_context(monkeypatch):
    fake_agent = _FakeAgent("dummy.\nNext agent: Nenhum")
    monkeypatch.setattr(nodes, "_AGENTS", {"Orquestrador": fake_agent})

    state = _state_with(previous_agents={"Analista de Poluentes": "Emissoes criticas."})
    nodes._orquestrador_node(state)

    sent_message = fake_agent.last_input["messages"][0]
    assert "Analista de Poluentes" in sent_message.content
    assert "Emissoes criticas." in sent_message.content


# --- _guardrail_node ---------------------------------------------------------


def test_guardrail_node_promotes_orquestrador_draft_on_approval(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Guardrail de Saída": _FakeAgent("Aprovado. Tudo certo.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(previous_agents={"Orquestrador": "Resposta consolidada para o usuario."})
    result = nodes._guardrail_node(state)

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Resposta consolidada para o usuario.",
        "name": "Orquestrador",
    }]
    assert result["guard_rail_approved"] is True
    assert "guard_rail_requested_changes" not in result
    assert "guard_rail_retries" not in result


def test_guardrail_node_does_not_touch_messages_on_rejection(monkeypatch):
    monkeypatch.setattr(nodes, "_AGENTS", {
        "Guardrail de Saída": _FakeAgent("Reprovado. Falta fundamentacao.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(previous_agents={"Orquestrador": "Resposta consolidada."}, guard_rail_retries=0)
    result = nodes._guardrail_node(state)

    assert "messages" not in result
    assert result["guard_rail_approved"] is False
    assert result["guard_rail_requested_changes"] == ["Reprovado. Falta fundamentacao.\nNext agent: Orquestrador"]
    assert result["guard_rail_retries"] == 1


# --- module-level nodes -------------------------------------------------------


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
