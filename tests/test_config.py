"""Tests for the public SDK facade in aeko/config/."""

import json

import pytest
from langchain_core.tools import tool

from aeko import (
    Aeko,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoTool,
    ImprovementPlan,
    Message,
    MessageResponse,
    Session,
    User,
)
from aeko.config.exceptions import (
    AekoNotConfiguredError,
    MalformedAgentOutputError,
    SessionNotPreparedError,
    UnknownAgentError,
)
from aeko.engine.runtime import (
    DEFAULT_FAST_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REPORT_MAX_TOKENS,
    RUNTIME,
)

API_KEY = "fake-api-key"

USER_ID = "64b8f0a1c9e1a2b3c4d5e6f1"
SESSION_ID = "64b8f0a1c9e1a2b3c4d5e6f3"
INVENTORY_ID = 502

FAQ_ANSWER = "Hidrogenio verde e produzido por eletrolise com energia renovavel."

CHAT_FLOW = {
    "Roteador": "Duvida conceitual.\nNext agent: FAQ",
    "FAQ": f"{FAQ_ANSWER}\nNext agent: Nenhum",
}

CONSOLIDATED = "Panorama consolidado do seu inventario."

ANALYSIS_FLOW = {
    "Roteador": "Analise tecnica.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "CO2 critico.\nNext agent: Orquestrador",
    "Orquestrador": f"{CONSOLIDATED}\nNext agent: Guardrail de Saída",
    "Guardrail de Saída": "Aprovado.\nNext agent: Nenhum",
}

REJECTED_FLOW = {
    **ANALYSIS_FLOW,
    "Guardrail de Saída": "Reprovado. Sem fundamentacao.\nNext agent: Nenhum",
}

# The coordinator is instructed to answer with exactly these three fields, which
# are then persisted as one "improvement_plan" document.
PLAN_FIELDS = {
    "defined_problem": "Os fornos a gas natural concentram a emissao de CO2 do inventario.",
    "method": "Trocar os queimadores e migrar a carga termica para hidrogenio verde.",
    "reasoning": "A combustao e a fonte dominante, e o ROI de 14 meses paga a substituicao.",
}

INVENTORY_FLOW = {
    "Análista de inventários": "Escopo 1 = 1.200 tCO2e.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "Combustao dominante.\nNext agent: Orquestrador",
    "Coordenador de Melhoria Contínua": (
        json.dumps(PLAN_FIELDS, ensure_ascii=False) + "\nNext agent: Nenhum"
    ),
}

INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"


@tool
def consulta_precos(query: str) -> str:
    """Descricao que a propria tool declara."""

    return ""


def make_user(**overrides) -> User:
    """A user as the API would have read it from the "user" collection."""

    return User.model_validate({
        "_id": USER_ID,
        "id_external_user": 1001,
        "role": "Gestor ambiental da Ceramica X",
        "usecase": "Acompanha a substituicao de gases dos fornos.",
        **overrides,
    })


def make_session(**overrides) -> Session:
    """A conversation as the API would have read it from the "session" collection."""

    return Session.model_validate({
        "_id": SESSION_ID,
        "id_user": USER_ID,
        "name": "Suporte Técnico #12",
        "messages": [],
        **overrides,
    })


@pytest.fixture
def configured():
    Aeko.config(API_KEY)


@pytest.fixture
def messenger(configured, use_fake_llm):
    """A messenger on a fresh session, plus the fake model driving the graph."""

    def _build(responses, session=None, user=None):
        llm = use_fake_llm(responses)
        instance = AekoMessenger()
        instance.prepare(session or make_session(), user or make_user())
        return instance, llm

    return _build


# --- Aeko.config ---------------------------------------------------------


def test_starts_unconfigured():
    assert Aeko.is_configured() is False


def test_config_records_the_api_key():
    Aeko.config(API_KEY)

    assert Aeko.is_configured() is True
    assert RUNTIME.require_api_key() == API_KEY


@pytest.mark.parametrize("invalid", ["", None, 123])
def test_config_rejects_an_invalid_api_key(invalid):
    with pytest.raises(AekoNotConfiguredError):
        Aeko.config(invalid)


def test_config_keeps_the_default_models_when_not_overridden():
    Aeko.config(API_KEY)

    assert RUNTIME.fast_model == DEFAULT_FAST_MODEL
    assert RUNTIME.max_tokens == DEFAULT_MAX_TOKENS
    assert RUNTIME.report_max_tokens == DEFAULT_REPORT_MAX_TOKENS


def test_config_overrides_models_and_caps():
    Aeko.config(API_KEY, fast_model="rapido", slow_model="lento", max_tokens=2048,
                report_max_tokens=16384)

    assert (RUNTIME.fast_model, RUNTIME.slow_model) == ("rapido", "lento")
    assert (RUNTIME.max_tokens, RUNTIME.report_max_tokens) == (2048, 16384)


def test_reset_clears_the_configuration():
    Aeko.config(API_KEY, fast_model="rapido")

    Aeko.reset()

    assert Aeko.is_configured() is False
    assert RUNTIME.fast_model == DEFAULT_FAST_MODEL


def test_reconfiguring_rebuilds_the_agents(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    RUNTIME.agents_for()
    assert RUNTIME.agents, "os agentes deveriam ter sido construidos"

    Aeko.config("outra-chave")

    assert not RUNTIME.agents, "reconfigurar precisa invalidar os agentes em cache"


def test_configuring_any_setting_invalidates_the_agents(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    RUNTIME.agents_for()

    RUNTIME.configure(slow_model="outro-lento")

    assert not RUNTIME.agents, "o RUNTIME e a unica fonte: reconfigura-lo invalida os agentes"


def test_assigning_a_setting_directly_also_invalidates_the_agents(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    RUNTIME.agents_for()

    RUNTIME.slow_model = "outro-lento"

    assert not RUNTIME.agents, "a invalidacao vale para qualquer escrita, nao so via configure()"


class _ClearedRightAfterWrite(dict):
    """
    Registro que se esvazia logo apos ser escrito.

    Reproduz de forma deterministica a janela concorrente real: outro thread
    escreve no runtime (o que dispara `agents.clear()`) entre o momento em que
    `agents_for()` guarda os agentes recem-construidos e o momento em que os
    devolve.
    """

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.clear()


def test_agents_for_survives_an_invalidation_right_after_the_build(configured, use_fake_llm,
                                                                   monkeypatch):
    use_fake_llm(CHAT_FLOW)
    # monkeypatch para o registro voltar a ser um dict comum depois do teste.
    monkeypatch.setattr(RUNTIME, "agents", _ClearedRightAfterWrite())

    agents = RUNTIME.agents_for()

    assert agents, "perder a corrida custa uma reconstrucao, nao um KeyError"
    assert not RUNTIME.agents, "a invalidacao concorrente continua valendo"


def test_configure_rejects_an_unknown_setting():
    with pytest.raises(AttributeError):
        RUNTIME.configure(modelo_rapido="rapido")


def test_agents_are_built_once_per_token_cap(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)

    conversational = RUNTIME.agents_for()

    assert RUNTIME.agents_for() is conversational, "o mesmo teto reaproveita os agentes"
    assert RUNTIME.agents_for(RUNTIME.report_max_tokens) is not conversational
    assert set(RUNTIME.agents) == {RUNTIME.max_tokens, RUNTIME.report_max_tokens}


def test_agents_for_defaults_to_the_conversational_cap(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)

    RUNTIME.agents_for()

    assert list(RUNTIME.agents) == [DEFAULT_MAX_TOKENS]


# --- set_tools -----------------------------------------------------------


def test_set_tools_registers_tools_per_agent():
    AekoMessenger.set_tools({"FAQ": [AekoTool(tool=consulta_precos, description="Consulta.")]})

    assert [t.name for t in RUNTIME.tools["FAQ"]] == ["consulta_precos"]
    assert "Roteador" not in RUNTIME.tools


def test_set_tools_rejects_an_unknown_agent():
    with pytest.raises(UnknownAgentError) as exc:
        AekoMessenger.set_tools({"Agente Inexistente": [consulta_precos]})

    assert "Roteador" in str(exc.value), "o erro deve listar os nomes validos"


def test_set_tools_normalizes_bare_tools():
    AekoMessenger.set_tools({"FAQ": [consulta_precos]})

    registered = RUNTIME.tools["FAQ"][0]

    assert isinstance(registered, AekoTool)
    assert registered.to_prompt_line() == "consulta_precos - Descricao que a propria tool declara."


def test_set_tools_prefers_the_description_given_by_the_caller():
    AekoMessenger.set_tools({
        "FAQ": [AekoTool(tool=consulta_precos, description="Consulta o preco medio.")],
    })

    assert RUNTIME.tools["FAQ"][0].to_prompt_line() == (
        "consulta_precos - Consulta o preco medio."
    )


def test_set_tools_is_global(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    AekoMessenger().prepare(make_session(), make_user())
    RUNTIME.agents_for()

    AekoMessenger.set_tools({"FAQ": [consulta_precos]})

    assert not RUNTIME.agents, "registrar tools precisa invalidar os agentes em cache"
    assert RUNTIME.tools["FAQ"], "as tools valem para o processo, nao para uma instancia"


def test_registered_tools_reach_the_agent_that_answers(messenger):
    AekoMessenger.set_tools({
        "FAQ": [AekoTool(tool=consulta_precos, description="Consulta o preco medio.")],
    })
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")

    assert "consulta_precos - Consulta o preco medio." in llm.system_prompt_for("FAQ")


def test_the_agents_are_told_to_consult_the_user_memories(messenger):
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")

    assert "memórias do usuário" in llm.system_prompt_for("FAQ"), (
        "as memorias chegam por tool registrada pela API, e o prompt precisa manda-la consultar"
    )


# --- prepare -------------------------------------------------------------


def test_prepare_takes_the_session_and_the_user_documents(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    session = make_session()

    prepared = AekoMessenger().prepare(session, make_user())

    assert prepared is session, "a conversa e o proprio documento, nao uma copia"


def test_prepare_resumes_a_session_from_its_persisted_messages(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)
    session = make_session(messages=[
        {"input": "pergunta antiga", "output": "resposta antiga"},
    ])

    prepared = AekoMessenger().prepare(session, make_user())

    assert len(prepared.messages) == 1
    assert isinstance(prepared.messages[0], Message)


def test_send_message_requires_a_prepared_session(configured):
    with pytest.raises(SessionNotPreparedError):
        AekoMessenger().send_message("oi")


# --- send_message --------------------------------------------------------


def test_send_message_requires_configuration():
    instance = AekoMessenger()
    instance.prepare(make_session(), make_user())

    with pytest.raises(AekoNotConfiguredError):
        instance.send_message("oi")


def test_send_message_returns_a_message_ready_to_persist(messenger):
    instance, _ = messenger(CHAT_FLOW)

    response = instance.send_message("O que e hidrogenio verde?")

    assert isinstance(response, MessageResponse)
    assert isinstance(response.message, Message)
    assert response.message.input == "O que e hidrogenio verde?"
    assert response.message.output == FAQ_ANSWER


def test_the_response_says_which_session_and_user_it_belongs_to(messenger):
    instance, _ = messenger(CHAT_FLOW)

    response = instance.send_message("O que e hidrogenio verde?")

    assert response.id_session == SESSION_ID
    assert response.id_user == USER_ID


def test_the_persisted_message_mirrors_the_collection(messenger):
    instance, _ = messenger(CHAT_FLOW)

    response = instance.send_message("O que e hidrogenio verde?")

    assert set(response.message.model_dump()) == {
        "input", "output", "submitted_at", "llm", "input_tokens", "output_tokens",
    }


def test_the_message_records_what_the_run_consumed(messenger):
    instance, llm = messenger(CHAT_FLOW)

    message = instance.send_message("O que e hidrogenio verde?").message

    # The chat flow calls the Roteador and then the FAQ.
    assert message.input_tokens == 2 * llm.usage_input_tokens
    assert message.output_tokens == 2 * llm.usage_output_tokens
    assert message.llm == llm.model


def test_a_provider_that_reports_no_usage_leaves_the_counters_zeroed(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW, usage_input_tokens=0, usage_output_tokens=0)
    instance = AekoMessenger()
    instance.prepare(make_session(), make_user())

    message = instance.send_message("O que e hidrogenio verde?").message

    assert (message.input_tokens, message.output_tokens) == (0, 0)


def test_the_answered_turn_is_appended_to_the_session(messenger):
    session = make_session()
    instance, _ = messenger(CHAT_FLOW, session=session)

    response = instance.send_message("O que e hidrogenio verde?")

    assert session.messages == [response.message]
    assert session.updated_at == response.message.submitted_at


def test_answer_is_free_of_the_routing_marker(messenger):
    instance, _ = messenger(ANALYSIS_FLOW)

    response = instance.send_message("Quais os riscos do meu inventario?")

    assert response.message.output == CONSOLIDATED
    assert "Next agent" not in response.message.output


def test_response_reports_the_agents_that_contributed(messenger):
    instance, _ = messenger(ANALYSIS_FLOW)

    response = instance.send_message("Quais os riscos do meu inventario?")

    assert "Analista de Poluentes" in response.agents_called
    assert response.approved is True
    assert response.guardrail_retries == 0


def test_a_terminal_agent_without_analysis_is_still_reported(messenger):
    instance, _ = messenger(CHAT_FLOW)

    response = instance.send_message("O que e hidrogenio verde?")

    # The FAQ answers directly and never writes to "previous_agents".
    assert response.agents_called == ["FAQ"]


def test_the_user_role_and_usecase_reach_the_agents(messenger):
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")

    prompt = llm.prompt_for("Roteador")

    assert "Gestor ambiental da Ceramica X" in prompt
    assert "substituicao de gases dos fornos" in prompt


def test_the_identifiers_never_reach_the_agents(messenger):
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")

    prompt = llm.prompt_for("Roteador")

    assert USER_ID not in prompt, "o _id e do banco, nao do modelo"
    assert "1001" not in prompt, "o id_external_user tambem nao diz nada ao modelo"


def test_previous_turns_reach_the_agents(messenger):
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")
    instance.send_message("E a amonia verde?")

    last_prompt = llm.prompt_for("FAQ")

    assert "Histórico da conversa" in last_prompt
    assert "O que e hidrogenio verde?" in last_prompt
    assert FAQ_ANSWER in last_prompt


def test_a_resumed_session_carries_its_history_into_the_first_message(messenger):
    session = make_session(messages=[
        {"input": "pergunta de ontem", "output": "resposta de ontem"},
    ])
    instance, llm = messenger(CHAT_FLOW, session=session)

    instance.send_message("E hoje?")

    prompt = llm.prompt_for("Roteador")

    assert "pergunta de ontem" in prompt
    assert "resposta de ontem" in prompt


def test_a_rejected_draft_produces_no_answer(messenger):
    instance, _ = messenger(REJECTED_FLOW)

    response = instance.send_message("Quais os riscos do meu inventario?")

    assert response.message.output == ""
    assert response.approved is False
    assert response.guardrail_retries > 0


def test_a_rejected_turn_does_not_pollute_the_history(messenger):
    session = make_session()
    instance, llm = messenger(REJECTED_FLOW, session=session)

    instance.send_message("Quais os riscos do meu inventario?")
    instance.send_message("E agora?")

    assert session.messages == [], "uma resposta reprovada nao vira contexto da proxima"
    assert "Histórico da conversa" not in llm.prompt_for("Roteador")


def test_sessions_are_isolated_from_each_other(configured, use_fake_llm):
    llm = use_fake_llm(CHAT_FLOW)

    first = AekoMessenger()
    first.prepare(make_session(id="sess-a"), make_user())
    first.send_message("primeira pergunta")

    second = AekoMessenger()
    second.prepare(make_session(id="sess-b"), make_user())
    second.send_message("outra pergunta")

    assert "primeira pergunta" not in llm.prompt_for("Roteador")


# --- AekoInventoryAnalyzer -----------------------------------------------


def test_analyze_returns_an_improvement_plan(configured, use_fake_llm):
    use_fake_llm(INVENTORY_FLOW)

    plan = AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert isinstance(plan, ImprovementPlan)
    assert plan.defined_problem == PLAN_FIELDS["defined_problem"]
    assert plan.method == PLAN_FIELDS["method"]
    assert plan.reasoning == PLAN_FIELDS["reasoning"]


def test_the_plan_is_tied_to_the_analyzed_inventory(configured, use_fake_llm):
    use_fake_llm(INVENTORY_FLOW)

    plan = AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert plan.id_external_inventory == INVENTORY_ID
    assert plan.id is None, "o _id e gerado pelo banco, nunca pelo SDK"
    assert plan.updated_at.tzinfo is not None


def test_the_plan_mirrors_the_collection(configured, use_fake_llm):
    use_fake_llm(INVENTORY_FLOW)

    plan = AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert set(plan.model_dump(by_alias=True)) == {
        "_id", "id_external_inventory", "defined_problem", "method", "reasoning",
        "updated_at",
    }


def test_the_model_cannot_smuggle_fields_into_the_plan(configured, use_fake_llm):
    smuggled = {**PLAN_FIELDS, "_id": "forjado", "id_external_inventory": 999}
    use_fake_llm({
        **INVENTORY_FLOW,
        "Coordenador de Melhoria Contínua": (
            json.dumps(smuggled, ensure_ascii=False) + "\nNext agent: Nenhum"
        ),
    })

    plan = AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert plan.id is None
    assert plan.id_external_inventory == INVENTORY_ID


def test_a_plan_wrapped_in_a_code_fence_is_still_read(configured, use_fake_llm):
    fenced = "```json\n" + json.dumps(PLAN_FIELDS, ensure_ascii=False) + "\n```"
    use_fake_llm({
        **INVENTORY_FLOW,
        "Coordenador de Melhoria Contínua": f"{fenced}\nNext agent: Nenhum",
    })

    plan = AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert plan.method == PLAN_FIELDS["method"]


def test_an_answer_in_prose_is_refused(configured, use_fake_llm):
    use_fake_llm({
        **INVENTORY_FLOW,
        "Coordenador de Melhoria Contínua": (
            "Plano: trocar queimadores, ROI de 14 meses.\nNext agent: Nenhum"
        ),
    })

    with pytest.raises(MalformedAgentOutputError):
        AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)


@pytest.mark.parametrize("missing", ["defined_problem", "method", "reasoning"])
def test_an_incomplete_plan_is_refused(configured, use_fake_llm, missing):
    incomplete = {key: value for key, value in PLAN_FIELDS.items() if key != missing}
    use_fake_llm({
        **INVENTORY_FLOW,
        "Coordenador de Melhoria Contínua": (
            json.dumps(incomplete, ensure_ascii=False) + "\nNext agent: Nenhum"
        ),
    })

    with pytest.raises(MalformedAgentOutputError) as exc:
        AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert missing in str(exc.value), "o erro deve dizer qual campo faltou"


def test_analyze_enters_through_the_inventory_analyst(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)

    AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert llm.agents_called()[0] == "Análista de inventários"
    assert "Roteador" not in llm.agents_called()


def test_analyze_forwards_the_inventory_to_the_first_agent(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)

    AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert INVENTORY_MD in llm.prompt_for("Análista de inventários")


def test_set_context_reaches_the_agents(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)
    analyzer = AekoInventoryAnalyzer()
    analyzer.set_context("Relatorio 2022: 2.100 tCO2e, foco em fornos.")

    analyzer.analyze(INVENTORY_MD, INVENTORY_ID)

    assert "Relatorio 2022" in llm.prompt_for("Análista de inventários")


def test_analyze_uses_the_report_token_cap(configured, monkeypatch):
    from tests.conftest import FakeChatModel

    caps = []
    fake = FakeChatModel(responses=INVENTORY_FLOW)

    def _spy(api_key=None, **kwargs):
        caps.append(kwargs.get("max_tokens"))
        return fake, fake

    monkeypatch.setattr("aeko.engine.agents.agents.create_llms", _spy)
    RUNTIME.agents.clear()

    AekoInventoryAnalyzer().analyze(INVENTORY_MD, INVENTORY_ID)

    assert caps == [DEFAULT_REPORT_MAX_TOKENS]
    assert list(RUNTIME.agents) == [DEFAULT_REPORT_MAX_TOKENS]


def test_send_message_uses_the_conversational_token_cap(configured, monkeypatch):
    from tests.conftest import FakeChatModel

    caps = []
    fake = FakeChatModel(responses=CHAT_FLOW)

    def _spy(api_key=None, **kwargs):
        caps.append(kwargs.get("max_tokens"))
        return fake, fake

    monkeypatch.setattr("aeko.engine.agents.agents.create_llms", _spy)
    RUNTIME.agents.clear()

    instance = AekoMessenger()
    instance.prepare(make_session(), make_user())
    instance.send_message("O que e hidrogenio verde?")

    assert caps == [DEFAULT_MAX_TOKENS], (
        "o fluxo conversacional usa o teto padrao, nao o de relatorio"
    )
    assert list(RUNTIME.agents) == [DEFAULT_MAX_TOKENS]
