import os

import pytest
from langchain_core.messages import HumanMessage

from system.engine.agents.agents import create_agents

IDENTITY_QUESTION = (
    "Quem e voce? Qual e o seu nome de agente e a sua funcao no ecossistema Aether? "
    "Responda apenas com o seu nome de agente."
)

EXPECTED_AGENTS = {
    "Roteador": "roteador",
    "FAQ": "faq",
    "Orquestrador": "orquestrador",
    "Análista de inventários": "invent",
    "Analista de Poluentes": "poluente",
    "Analista de Gases Verdes": "gases verdes",
    "Coordenador de Melhoria Contínua": "melhoria",
}

requires_gemini_api_key = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is not set; these tests call the real Gemini API.",
)


@pytest.fixture(scope="module")
def agents():
    return create_agents()


def _ask_identity(agent):
    result = agent.invoke({"messages": [HumanMessage(content=IDENTITY_QUESTION)]})
    return result["messages"][-1].content


@requires_gemini_api_key
def test_create_agents_returns_all_expected_agents(agents):
    assert set(agents.keys()) == set(EXPECTED_AGENTS)


@requires_gemini_api_key
@pytest.mark.parametrize("agent_name, expected_substring", EXPECTED_AGENTS.items())
def test_agent_identifies_itself(agents, agent_name, expected_substring):
    response = _ask_identity(agents[agent_name])

    assert expected_substring in response.lower()