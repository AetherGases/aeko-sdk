from langchain_core.messages import HumanMessage

from aeko.engine.graph import nodes
from aeko.engine.graph.state import create_initial_state


class _FakeAgent:
    """
    A stand-in for one `AgentExecutor`, taking what a real one takes.

    The `config` is accepted rather than ignored because `_invoke_agent` passes
    one: it is how the per-call event tracking collector reaches the agent's
    own run (see aeko/shared/event_tracking.py). A double that refused it would
    pass while the graph broke.
    """

    def __init__(self, output: str):
        self.output = output
        self.last_input = None
        self.last_config = None

    def invoke(self, input_, config=None):
        self.last_input = input_
        self.last_config = config
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


def test_build_context_message_includes_latest_response_check_feedback():
    state = _state_with(
        response_check_requested_changes=["A resposta cita um numero que ninguem analisou."]
    )

    message = nodes._build_context_message(state)

    assert (
        "Pontos apontados pelo Verificador de Resposta na tentativa anterior:\n"
        "A resposta cita um numero que ninguem analisou."
    ) in message.content


def test_build_context_message_omits_response_check_section_when_no_rejection():
    message = nodes._build_context_message(_state_with())

    assert "Pontos apontados pelo Verificador de Resposta" not in message.content


def test_build_context_message_omits_guardrail_section_when_no_rejection():
    state = _state_with()

    message = nodes._build_context_message(state)

    assert "Pontos apontados pelo Guardrail de Saída" not in message.content


def test_build_context_message_replays_the_whole_history_it_was_given():
    # Trimming is decided once, when the facade seeds the state from the
    # session (see `SESSION_HISTORY_USAGE`), so the node replays whatever it
    # was handed instead of applying a second, competing cut of its own.
    history = "\n".join(f"Usuário: pergunta {n}" for n in range(30))
    state = create_initial_state("Pergunta de teste", history=history)

    message = nodes._build_context_message(state)

    assert "pergunta 0" in message.content
    assert "pergunta 29" in message.content
    assert not hasattr(nodes, "HISTORY_MESSAGE_LIMIT"), (
        "o corte mora na fachada (SESSION_HISTORY_USAGE); o no nao pode ter o seu"
    )


# --- _build_guardrail_message --------------------------------------------


def test_build_guardrail_message_wraps_orquestrador_draft():
    state = _state_with(output="Resposta consolidada.")

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
    state = _state_with(
        output="Resposta consolidada.",
        previous_agents={
            "Analista de Poluentes": "Emissoes criticas de NOx.",
            "Orquestrador": "Resposta consolidada.",
        },
    )

    message = nodes._build_guardrail_message(state)

    assert "Pergunta original do usuário: Pergunta de teste" in message.content
    assert "Análises recebidas:" in message.content
    assert "- Analista de Poluentes: Emissoes criticas de NOx." in message.content
    assert "- Orquestrador:" not in message.content


# --- _invoke_agent --------------------------------------------------------


def test_invoke_agent_sends_a_single_message_to_the_agent(use_agents):
    fake_agent = _FakeAgent("Ok.\nNext agent: Nenhum")
    use_agents({"FAQ": fake_agent})

    message = HumanMessage(content="Oi")
    output, next_agent = nodes._invoke_agent("FAQ", message)

    assert fake_agent.last_input == {"messages": [message]}
    assert output == "Ok.\nNext agent: Nenhum"
    assert next_agent is None


def test_invoke_agent_parses_next_agent_marker(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
        "FAQ": _FakeAgent("dummy"),
    })

    output, next_agent = nodes._invoke_agent("Roteador", HumanMessage(content="Oi"))

    assert next_agent == {"agent": "FAQ", "message": "Direcionando.\nNext agent: FAQ"}


def test_invoke_agent_raises_for_unknown_agent_name(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Agente Inexistente"),
    })

    try:
        nodes._invoke_agent("Roteador", HumanMessage(content="Oi"))
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- _coordenador_melhoria_node --------------------------------------------


COORDINATOR = "Coordenador de Melhoria Contínua"


class _ScriptedAgent:
    """An agent that answers something different on each call."""

    def __init__(self, *outputs: str):
        self.outputs = list(outputs)
        self.inputs = []

    def invoke(self, input_, config=None):
        self.inputs.append(input_)
        return {"output": self.outputs[min(len(self.inputs) - 1, len(self.outputs) - 1)]}


def _rejects(problems: list[str]):
    """A "validate_answer" that complains until the answer says "ok"."""

    return lambda answer: [] if "ok" in answer else problems


def _config(**configurable):
    return {"configurable": configurable}


def test_the_coordinator_answers_once_when_nothing_validates_it(use_agents):
    agent = _ScriptedAgent("Plano finalizado.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    result = nodes._coordenador_melhoria_node(
        _state_with(), _config(entry_point=nodes.INVENTORY_ENTRY_POINT)
    )

    assert len(agent.inputs) == 1
    assert result["messages"] == [{
        "role": "assistant",
        "content": "Plano finalizado.\nNext agent: Nenhum",
        "name": COORDINATOR,
    }]
    assert result["previous_agents"] == {COORDINATOR: "Plano finalizado.\nNext agent: Nenhum"}
    assert result["pending_agents"] == [{"agent": COORDINATOR, "is_still_pending": False}]


def test_an_ill_formed_answer_is_sent_back_to_the_coordinator(use_agents):
    agent = _ScriptedAgent("Prosa solta.\nNext agent: Nenhum", "ok, corrigido.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    result = nodes._coordenador_melhoria_node(
        _state_with(),
        _config(
            entry_point=nodes.INVENTORY_ENTRY_POINT,
            validate_answer=_rejects(["Falta a seção X."]),
        ),
    )

    assert len(agent.inputs) == 2, "o no deve pedir a correcao ao proprio coordenador"
    assert result["messages"][0]["content"] == "ok, corrigido.\nNext agent: Nenhum"


def test_the_correction_request_carries_the_problems_and_the_refused_answer(use_agents):
    agent = _ScriptedAgent("Prosa solta.\nNext agent: Nenhum", "ok.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    nodes._coordenador_melhoria_node(
        _state_with(), _config(validate_answer=_rejects(["Falta a seção Método."]))
    )

    retry = agent.inputs[1]["messages"][0].content
    assert "Falta a seção Método." in retry
    assert "Prosa solta." in retry, "o agente precisa ver o que escreveu"
    assert "Pergunta de teste" in retry, "e o pedido original, para poder reescrever"


def test_the_coordinator_gives_up_after_the_retry_cap(use_agents):
    agent = _ScriptedAgent("Prosa solta.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    result = nodes._coordenador_melhoria_node(
        _state_with(),
        _config(
            entry_point=nodes.INVENTORY_ENTRY_POINT,
            validate_answer=_rejects(["Falta tudo."]),
        ),
    )

    assert len(agent.inputs) == nodes.PLAN_FORMAT_MAX_RETRIES + 1
    assert result["messages"][0]["content"] == "Prosa solta.\nNext agent: Nenhum", (
        "a ultima tentativa e registrada; quem le o texto e que decide o erro"
    )


def test_a_run_without_a_validator_never_retries(use_agents):
    agent = _ScriptedAgent("Prosa solta.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    nodes._coordenador_melhoria_node(_state_with(), _config(max_tokens=None))

    assert len(agent.inputs) == 1, "o fluxo de chat tambem passa por aqui"


def test_the_coordinator_does_not_deliver_its_own_answer_in_the_chat_flow(use_agents):
    """
    In a chat the plan is a finding for the Orquestrador, not the answer.

    Writing it to "messages" here would deliver the coordinator's fixed
    sections to the user as they are, and would do it without either reviewer
    ever seeing them.
    """

    agent = _ScriptedAgent("## Problema definido\nForno ineficiente.\nNext agent: Nenhum")
    use_agents({COORDINATOR: agent})

    result = nodes._coordenador_melhoria_node(_state_with())

    assert "messages" not in result
    assert result["previous_agents"] == {
        COORDINATOR: "## Problema definido\nForno ineficiente.\nNext agent: Nenhum"
    }


# --- _specialist_node_factory ---------------------------------------------


def test_specialist_node_factory_is_non_terminal_by_default(use_agents):
    use_agents({
        "Analista de Poluentes": _FakeAgent("Analise concluida.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    node = nodes._specialist_node_factory("Analista de Poluentes")
    result = node(_state_with())

    assert result["previous_agents"] == {"Analista de Poluentes": "Analise concluida.\nNext agent: Orquestrador"}
    assert result["pending_agents"] == [{"agent": "Analista de Poluentes", "is_still_pending": False}]
    assert result["next_agent"] == {"agent": "Orquestrador", "message": "Analise concluida.\nNext agent: Orquestrador"}
    assert "messages" not in result


def test_specialist_node_factory_terminal_also_writes_messages(use_agents):
    use_agents({
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


def test_specialist_node_factory_returns_independent_closures(use_agents):
    use_agents({
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


def test_non_specialist_node_factory_is_non_terminal_by_default(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
        "FAQ": _FakeAgent("dummy"),
    })

    node = nodes._non_specialist_node_factory("Roteador")
    result = node(_state_with())

    assert "previous_agents" not in result
    assert "pending_agents" not in result
    assert "messages" not in result
    assert result["next_agent"] == {"agent": "FAQ", "message": "Direcionando.\nNext agent: FAQ"}


def test_non_specialist_node_factory_terminal_writes_messages(use_agents):
    fake_agent = _FakeAgent("Resposta direta ao usuario.\nNext agent: Nenhum")
    use_agents({"FAQ": fake_agent})

    node = nodes._non_specialist_node_factory("FAQ", terminal=True)
    result = node(_state_with())

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Resposta direta ao usuario.\nNext agent: Nenhum",
        "name": "FAQ",
    }]
    assert result["next_agent"] is None


# --- _roteador_node ---------------------------------------------------------


def test_roteador_node_overrides_premature_orquestrador_routing(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Only text.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    result = nodes._roteador_node(_state_with())

    assert result["next_agent"] == {
        "agent": "FAQ",
        "message": "Only text.\nNext agent: Orquestrador",
    }


def test_roteador_node_allows_orquestrador_when_previous_agents_exist(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(previous_agents={"Analista de Gases Verdes": "Recomendo hidrogenio verde."})
    result = nodes._roteador_node(state)

    assert result["next_agent"] == {"agent": "Orquestrador", "message": "Direcionando.\nNext agent: Orquestrador"}


def test_roteador_node_never_writes_messages(use_agents):
    use_agents({
        "FAQ": _FakeAgent("dummy"),
        "Roteador": _FakeAgent("Direcionando.\nNext agent: FAQ"),
    })

    result = nodes._roteador_node(_state_with())

    assert "messages" not in result
    assert "previous_agents" not in result


def test_next_agent_is_none_when_marker_absent(use_agents):
    use_agents({
        "FAQ": _FakeAgent("Resposta sem marcador de proximo agente."),
    })

    node = nodes._non_specialist_node_factory("FAQ")
    result = node(_state_with())

    assert result["next_agent"] is None


def test_next_agent_is_none_when_nenhum(use_agents):
    use_agents({
        "Coordenador de Melhoria Contínua": _FakeAgent("Plano finalizado.\nNext agent: Nenhum"),
    })

    node = nodes._specialist_node_factory("Coordenador de Melhoria Contínua")
    result = node(_state_with())

    assert result["next_agent"] is None


def test_next_agent_raises_for_unknown_agent_name(use_agents):
    use_agents({
        "Roteador": _FakeAgent("Direcionando.\nNext agent: Agente Inexistente"),
    })

    node = nodes._non_specialist_node_factory("Roteador")

    try:
        node(_state_with())
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_faq_node_sends_a_message_built_from_the_initial_question(use_agents):
    fake_agent = _FakeAgent("Ok.\nNext agent: Nenhum")
    use_agents({"FAQ": fake_agent})

    node = nodes._non_specialist_node_factory("FAQ", terminal=True)
    state = create_initial_state("Oi, tudo bem?")
    node(state)

    assert fake_agent.last_input == {"messages": [HumanMessage(content="Oi, tudo bem?")]}


# --- _orquestrador_node -----------------------------------------------------


def test_orquestrador_node_records_draft_in_previous_agents_not_messages(use_agents):
    fake_agent = _FakeAgent("Resposta consolidada.\nNext agent: Guardrail de Saída")
    use_agents({
        "Orquestrador": fake_agent,
        "Guardrail de Saída": _FakeAgent("dummy"),
    })

    result = nodes._orquestrador_node(_state_with())

    assert result["previous_agents"] == {"Orquestrador": "Resposta consolidada.\nNext agent: Guardrail de Saída"}
    assert result["output"] == "Resposta consolidada.\nNext agent: Guardrail de Saída", (
        "o rascunho candidato vive no estado ate o ultimo revisor aprova-lo"
    )
    assert "messages" not in result
    assert result["next_agent"] == {
        "agent": "Guardrail de Saída",
        "message": "Resposta consolidada.\nNext agent: Guardrail de Saída",
    }


def test_orquestrador_node_uses_previous_agents_findings_as_context(use_agents):
    fake_agent = _FakeAgent("dummy.\nNext agent: Nenhum")
    use_agents({"Orquestrador": fake_agent})

    state = _state_with(previous_agents={"Analista de Poluentes": "Emissoes criticas."})
    nodes._orquestrador_node(state)

    sent_message = fake_agent.last_input["messages"][0]
    assert "Analista de Poluentes" in sent_message.content
    assert "Emissoes criticas." in sent_message.content


# --- _guardrail_node ---------------------------------------------------------


def test_guardrail_node_approves_without_delivering_the_draft(use_agents):
    """
    Approving is not delivering: the response checker still has to see it.

    Writing to "messages" here would hand the user an answer the checker was
    about to reject, since the channel is what the facade reads the delivered
    answer from.
    """

    use_agents({
        "Guardrail de Saída": _FakeAgent("Aprovado. Tudo certo.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(output="Resposta consolidada para o usuario.")
    result = nodes._guardrail_node(state)

    assert "messages" not in result
    assert result["guard_rail_approved"] is True
    assert "guard_rail_requested_changes" not in result
    assert "guard_rail_retries" not in result


def test_guardrail_node_does_not_touch_messages_on_rejection(use_agents):
    use_agents({
        "Guardrail de Saída": _FakeAgent("Reprovado. Falta fundamentacao.\nNext agent: Orquestrador"),
        "Orquestrador": _FakeAgent("dummy"),
    })

    state = _state_with(output="Resposta consolidada.", guard_rail_retries=0)
    result = nodes._guardrail_node(state)

    assert "messages" not in result
    assert result["guard_rail_approved"] is False
    assert result["guard_rail_requested_changes"] == ["Reprovado. Falta fundamentacao.\nNext agent: Orquestrador"]
    assert result["guard_rail_retries"] == 1


# --- _build_response_check_message -------------------------------------------


def test_build_response_check_message_pairs_the_question_with_the_answer():
    state = _state_with(output="Resposta consolidada para o usuario.")

    message = nodes._build_response_check_message(state)

    assert isinstance(message, HumanMessage)
    assert message.content == (
        "Pergunta original do usuário: Pergunta de teste"
        "\n\nResposta gerada: 'Resposta consolidada para o usuario.'"
    )


def test_build_response_check_message_includes_the_findings_behind_the_answer():
    state = _state_with(
        output="Resposta consolidada.",
        previous_agents={
            "Analista de Poluentes": "Emissoes criticas de NOx.",
            "Orquestrador": "Resposta consolidada.",
        },
    )

    message = nodes._build_response_check_message(state)

    assert "Análises recebidas:" in message.content
    assert "- Analista de Poluentes: Emissoes criticas de NOx." in message.content
    assert "- Orquestrador:" not in message.content, (
        "o rascunho ja esta na mensagem; repeti-lo como analise seria fundamenta-lo em si mesmo"
    )


def test_build_response_check_message_handles_a_missing_answer():
    message = nodes._build_response_check_message(_state_with())

    assert "Resposta gerada: ''" in message.content


# --- _verificador_resposta_node ----------------------------------------------


def test_response_check_node_delivers_the_answer_on_approval(use_agents):
    use_agents({
        "Verificador de Resposta": _FakeAgent("Aprovado. Fiel ao pedido.\nNext agent: Nenhum"),
    })

    state = _state_with(output="Resposta consolidada para o usuario.")
    result = nodes._verificador_resposta_node(state)

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Resposta consolidada para o usuario.",
        "name": "Orquestrador",
    }]
    assert result["response_check_approved"] is True
    assert "response_check_requested_changes" not in result
    assert "response_check_retries" not in result


def test_response_check_node_holds_back_the_answer_on_rejection(use_agents):
    use_agents({
        "Roteador": _FakeAgent("dummy"),
        "Verificador de Resposta": _FakeAgent(
            "Reprovado. Cita um dado que ninguem analisou.\nNext agent: Roteador"
        ),
    })

    state = _state_with(output="Resposta consolidada.", response_check_retries=0)
    result = nodes._verificador_resposta_node(state)

    assert "messages" not in result
    assert result["response_check_approved"] is False
    assert result["response_check_requested_changes"] == [
        "Reprovado. Cita um dado que ninguem analisou.\nNext agent: Roteador"
    ]
    assert result["response_check_retries"] == 1


def test_response_check_node_records_itself_among_the_agents(use_agents):
    use_agents({
        "Verificador de Resposta": _FakeAgent("Aprovado.\nNext agent: Nenhum"),
    })

    result = nodes._verificador_resposta_node(_state_with(output="Resposta."))

    assert "Verificador de Resposta" in result["previous_agents"]


def test_response_check_node_reviews_what_would_be_delivered(use_agents):
    fake_agent = _FakeAgent("Aprovado.\nNext agent: Nenhum")
    use_agents({"Verificador de Resposta": fake_agent})

    nodes._verificador_resposta_node(_state_with(output="Resposta consolidada."))

    sent = fake_agent.last_input["messages"][0]

    assert "Pergunta de teste" in sent.content
    assert "Resposta consolidada." in sent.content


# --- module-level nodes -------------------------------------------------------


def test_module_level_nodes_are_created_and_callable():
    non_specialist_nodes = [
        nodes.roteador_node,
        nodes.faq_node,
        nodes.orquestrador_node,
        nodes.guardrail_node,
        nodes.verificador_resposta_node,
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
