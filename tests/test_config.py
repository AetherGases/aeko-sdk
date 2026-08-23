"""Tests for the public SDK facade in aeko/config/."""

import pytest
from langchain_core.tools import tool

from aeko import (
    Aeko,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoTool,
    InventoryAnalysisResponse,
    MessageResponse,
    SessionInfo,
)
from aeko.config.exceptions import (
    AekoNotConfiguredError,
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

IMPROVEMENT_PLAN = "Plano: trocar queimadores, ROI de 14 meses."

INVENTORY_FLOW = {
    "Análista de inventários": "Escopo 1 = 1.200 tCO2e.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "Combustao dominante.\nNext agent: Orquestrador",
    "Coordenador de Melhoria Contínua": f"{IMPROVEMENT_PLAN}\nNext agent: Nenhum",
}

INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"


@tool
def consulta_precos(query: str) -> str:
    """Descricao que a propria tool declara."""

    return ""


@pytest.fixture
def configured():
    Aeko.config(API_KEY)


@pytest.fixture
def messenger(configured, use_fake_llm):
    """A messenger on a fresh session, plus the fake model driving the graph."""

    def _build(responses, session_id="sess-1", user_info="Gestor ambiental da Ceramica X.",
               history=None):
        llm = use_fake_llm(responses)
        instance = AekoMessenger()
        instance.prepare(session_id, user_info, history=history)
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
    from aeko.engine.graph import nodes

    use_fake_llm(CHAT_FLOW)
    nodes._get_agents()
    assert nodes._AGENT_CACHE, "os agentes deveriam ter sido construidos"

    Aeko.config("outra-chave")

    assert not nodes._AGENT_CACHE, "reconfigurar precisa invalidar os agentes em cache"


# --- set_tools -----------------------------------------------------------


def test_set_tools_registers_tools_per_agent():
    AekoMessenger.set_tools({"FAQ": [AekoTool(tool=consulta_precos, description="Consulta.")]})

    assert [t.name for t in RUNTIME.tools_for("FAQ")] == ["consulta_precos"]
    assert RUNTIME.tools_for("Roteador") == []


def test_set_tools_rejects_an_unknown_agent():
    with pytest.raises(UnknownAgentError) as exc:
        AekoMessenger.set_tools({"Agente Inexistente": [consulta_precos]})

    assert "Roteador" in str(exc.value), "o erro deve listar os nomes validos"


def test_set_tools_normalizes_bare_tools():
    AekoMessenger.set_tools({"FAQ": [consulta_precos]})

    registered = RUNTIME.tools_for("FAQ")[0]

    assert isinstance(registered, AekoTool)
    assert registered.to_prompt_line() == "consulta_precos - Descricao que a propria tool declara."


def test_set_tools_prefers_the_description_given_by_the_caller():
    AekoMessenger.set_tools({
        "FAQ": [AekoTool(tool=consulta_precos, description="Consulta o preco medio.")],
    })

    assert RUNTIME.tools_for("FAQ")[0].to_prompt_line() == (
        "consulta_precos - Consulta o preco medio."
    )


def test_set_tools_is_global(configured, use_fake_llm):
    from aeko.engine.graph import nodes

    use_fake_llm(CHAT_FLOW)
    AekoMessenger().prepare("sess-tools", "Usuario")
    nodes._get_agents()

    AekoMessenger.set_tools({"FAQ": [consulta_precos]})

    assert not nodes._AGENT_CACHE, "registrar tools precisa invalidar os agentes em cache"
    assert RUNTIME.tools_for("FAQ"), "as tools valem para o processo, nao para uma instancia"


def test_registered_tools_reach_the_agent_that_answers(messenger):
    AekoMessenger.set_tools({
        "FAQ": [AekoTool(tool=consulta_precos, description="Consulta o preco medio.")],
    })
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")

    assert "consulta_precos - Consulta o preco medio." in llm.system_prompt_for("FAQ")


# --- prepare -------------------------------------------------------------


def test_prepare_returns_a_session_handle(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)

    session = AekoMessenger().prepare("sess-1", "Gestor ambiental.")

    assert isinstance(session, SessionInfo)
    assert (session.session_id, session.user_info, session.turns) == (
        "sess-1", "Gestor ambiental.", 0
    )


def test_prepare_hydrates_a_session_from_external_history(configured, use_fake_llm):
    use_fake_llm(CHAT_FLOW)

    session = AekoMessenger().prepare("sess-remota", "Gestor", history=[
        {"role": "user", "content": "pergunta antiga"},
        {"role": "assistant", "content": "resposta antiga"},
    ])

    assert session.turns == 2


def test_send_message_requires_a_prepared_session(configured):
    with pytest.raises(SessionNotPreparedError):
        AekoMessenger().send_message("oi")


# --- send_message --------------------------------------------------------


def test_send_message_requires_configuration():
    instance = AekoMessenger()
    instance.prepare("sess-1", "Gestor")

    with pytest.raises(AekoNotConfiguredError):
        instance.send_message("oi")


def test_send_message_returns_the_final_answer(messenger):
    instance, _ = messenger(CHAT_FLOW)

    response = instance.send_message("O que e hidrogenio verde?")

    assert isinstance(response, MessageResponse)
    assert response.answer == FAQ_ANSWER
    assert response.session_id == "sess-1"


def test_answer_is_free_of_the_routing_marker(messenger):
    instance, _ = messenger(ANALYSIS_FLOW)

    response = instance.send_message("Quais os riscos do meu inventario?")

    assert response.answer == CONSOLIDATED
    assert "Next agent" not in response.answer


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


def test_user_info_reaches_the_agents(messenger):
    instance, llm = messenger(CHAT_FLOW, user_info="Gestor da Ceramica X, 1.600 tCO2e.")

    instance.send_message("O que e hidrogenio verde?")

    assert "Ceramica X" in llm.prompt_for("Roteador")


def test_previous_turns_reach_the_agents(messenger):
    instance, llm = messenger(CHAT_FLOW)

    instance.send_message("O que e hidrogenio verde?")
    instance.send_message("E a amonia verde?")

    last_prompt = llm.prompt_for("FAQ")

    assert "Histórico da conversa" in last_prompt
    assert "O que e hidrogenio verde?" in last_prompt
    assert FAQ_ANSWER in last_prompt


def test_a_resumed_session_carries_its_history_into_the_first_message(messenger):
    instance, llm = messenger(CHAT_FLOW, history=[
        {"role": "user", "content": "pergunta de ontem"},
        {"role": "assistant", "content": "resposta de ontem"},
    ])

    instance.send_message("E hoje?")

    assert "pergunta de ontem" in llm.prompt_for("Roteador")


def test_a_rejected_draft_produces_no_answer(messenger):
    instance, _ = messenger(REJECTED_FLOW)

    response = instance.send_message("Quais os riscos do meu inventario?")

    assert response.answer == ""
    assert response.approved is False
    assert response.guardrail_retries > 0


def test_a_rejected_turn_does_not_pollute_the_history(messenger):
    instance, llm = messenger(REJECTED_FLOW)

    instance.send_message("Quais os riscos do meu inventario?")
    instance.send_message("E agora?")

    assert "Histórico da conversa" not in llm.prompt_for("Roteador")


def test_sessions_are_isolated_from_each_other(configured, use_fake_llm):
    llm = use_fake_llm(CHAT_FLOW)

    first = AekoMessenger()
    first.prepare("sess-a", "Usuario A")
    first.send_message("primeira pergunta")

    second = AekoMessenger()
    second.prepare("sess-b", "Usuario B")
    second.send_message("outra pergunta")

    assert "primeira pergunta" not in llm.prompt_for("Roteador")


# --- AekoInventoryAnalyzer -----------------------------------------------


def test_analyze_returns_the_improvement_plan(configured, use_fake_llm):
    use_fake_llm(INVENTORY_FLOW)

    report = AekoInventoryAnalyzer().analyze(INVENTORY_MD)

    assert isinstance(report, InventoryAnalysisResponse)
    assert report.answer == IMPROVEMENT_PLAN
    assert "Next agent" not in report.answer


def test_analyze_enters_through_the_inventory_analyst(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)

    report = AekoInventoryAnalyzer().analyze(INVENTORY_MD)

    assert llm.agents_called()[0] == "Análista de inventários"
    assert "Roteador" not in llm.agents_called()
    assert report.agents_called[0] == "Análista de inventários"


def test_analyze_forwards_the_inventory_to_the_first_agent(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)

    AekoInventoryAnalyzer().analyze(INVENTORY_MD)

    assert INVENTORY_MD in llm.prompt_for("Análista de inventários")


def test_analyze_without_context_reports_it(configured, use_fake_llm):
    use_fake_llm(INVENTORY_FLOW)

    report = AekoInventoryAnalyzer().analyze(INVENTORY_MD)

    assert report.context_used is False


def test_set_context_reaches_the_agents(configured, use_fake_llm):
    llm = use_fake_llm(INVENTORY_FLOW)
    analyzer = AekoInventoryAnalyzer()
    analyzer.set_context("Relatorio 2022: 2.100 tCO2e, foco em fornos.")

    report = analyzer.analyze(INVENTORY_MD)

    assert report.context_used is True
    assert "Relatorio 2022" in llm.prompt_for("Análista de inventários")


def test_analyze_uses_the_report_token_cap(configured, monkeypatch):
    from tests.conftest import FakeChatModel
    from aeko.engine.graph import nodes

    caps = []
    fake = FakeChatModel(responses=INVENTORY_FLOW)

    def _spy(api_key=None, **kwargs):
        caps.append(kwargs.get("max_tokens"))
        return fake, fake

    monkeypatch.setattr("aeko.engine.agents.agents.create_llms", _spy)
    nodes.reset_agents()

    AekoInventoryAnalyzer().analyze(INVENTORY_MD)

    assert caps == [DEFAULT_REPORT_MAX_TOKENS]

    nodes.reset_agents()


def test_send_message_uses_the_conversational_token_cap(configured, monkeypatch):
    from tests.conftest import FakeChatModel
    from aeko.engine.graph import nodes

    caps = []
    fake = FakeChatModel(responses=CHAT_FLOW)

    def _spy(api_key=None, **kwargs):
        caps.append(kwargs.get("max_tokens"))
        return fake, fake

    monkeypatch.setattr("aeko.engine.agents.agents.create_llms", _spy)
    nodes.reset_agents()

    instance = AekoMessenger()
    instance.prepare("sess-cap", "Gestor")
    instance.send_message("O que e hidrogenio verde?")

    assert caps == [None], "o fluxo conversacional usa o teto padrao, nao o de relatorio"

    nodes.reset_agents()
