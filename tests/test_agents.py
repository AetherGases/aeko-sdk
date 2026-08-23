import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool

from system.config.dto import AekoTool
from system.engine.agents.agents import FAST_AGENTS, create_agents

from tests.conftest import PERSONA_MARKER

IDENTITY_QUESTION = (
    "Quem e voce? Qual e o seu nome de agente e a sua funcao no ecossistema Aether? "
    "Responda apenas com o seu nome de agente."
)

EXPECTED_AGENTS = {
    "Roteador": "roteador",
    "FAQ": "faq",
    "Orquestrador": "orquestrador",
    "Guardrail de Saída": "guardrail",
    "Análista de inventários": "invent",
    "Analista de Poluentes": "poluente",
    "Analista de Gases Verdes": "gases verdes",
    "Coordenador de Melhoria Contínua": "melhoria",
}


@tool
def consulta_precos(query: str) -> str:
    """Descricao que a propria tool declara."""

    return ""


@pytest.fixture
def identity_llm(use_fake_llm):
    """A fake that answers with whichever agent's prompt it was handed."""

    return use_fake_llm(default_response="Eu sou o agente {agent}.\nNext agent: Nenhum")


@pytest.fixture
def agents(identity_llm):
    return create_agents()


def _ask_identity(agent) -> str:
    result = agent.invoke({"messages": [HumanMessage(content=IDENTITY_QUESTION)]})
    return result["output"]


def _system_prompt(agent) -> str:
    """Render the system message an agent executor would send to its model."""

    prompt = next(
        step for step in agent.agent.runnable.steps if isinstance(step, ChatPromptTemplate)
    )
    return prompt.invoke({"messages": [], "agent_scratchpad": []}).messages[0].content


def _model_of(agent) -> BaseChatModel:
    """Return the chat model an agent executor is wired to."""

    return next(
        step for step in agent.agent.runnable.steps if isinstance(step, BaseChatModel)
    )


def test_create_agents_returns_all_expected_agents(agents):
    assert set(agents.keys()) == set(EXPECTED_AGENTS)


@pytest.mark.parametrize("agent_name, expected_substring", EXPECTED_AGENTS.items())
def test_agent_identifies_itself(agents, agent_name, expected_substring):
    response = _ask_identity(agents[agent_name])

    assert expected_substring in response.lower()


def test_agent_prompts_carry_their_own_persona(agents):
    for agent_name, executor in agents.items():
        assert PERSONA_MARKER + agent_name in _system_prompt(executor)


def test_create_agents_requires_configuration():
    from system.config.exceptions import AekoNotConfiguredError

    with pytest.raises(AekoNotConfiguredError):
        create_agents()


def test_agents_have_no_tools_by_default(agents):
    assert all(executor.tools == [] for executor in agents.values())


def test_registered_tools_are_bound_to_their_agent(identity_llm):
    agents = create_agents({"Analista de Poluentes": [AekoTool(tool=consulta_precos)]})

    assert [t.name for t in agents["Analista de Poluentes"].tools] == ["consulta_precos"]
    assert agents["FAQ"].tools == []


def test_registered_tools_are_described_in_the_prompt(identity_llm):
    agents = create_agents({
        "Analista de Poluentes": [
            AekoTool(tool=consulta_precos, description="Consulta o preco medio de mitigacoes."),
        ],
    })

    prompt = _system_prompt(agents["Analista de Poluentes"])

    assert "consulta_precos - Consulta o preco medio de mitigacoes." in prompt
    assert "consulta_precos" not in _system_prompt(agents["FAQ"])


def test_tool_description_falls_back_to_the_tools_own(identity_llm):
    agents = create_agents({"FAQ": [AekoTool(tool=consulta_precos)]})

    assert "consulta_precos - Descricao que a propria tool declara." in _system_prompt(agents["FAQ"])


def test_bare_langchain_tools_are_accepted(identity_llm):
    agents = create_agents({"FAQ": [consulta_precos]})

    assert [t.name for t in agents["FAQ"].tools] == ["consulta_precos"]
    assert "consulta_precos - Descricao que a propria tool declara." in _system_prompt(agents["FAQ"])


def test_specialists_get_the_slow_model_and_the_rest_the_fast_one(monkeypatch):
    from tests.conftest import FakeChatModel

    fast, slow = FakeChatModel(model="fast"), FakeChatModel(model="slow")
    monkeypatch.setattr(
        "system.engine.agents.agents.create_llms", lambda *a, **k: (fast, slow)
    )

    agents = create_agents()

    for name, executor in agents.items():
        expected = "fast" if name in FAST_AGENTS else "slow"
        assert _model_of(executor).model == expected, name
