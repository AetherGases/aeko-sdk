from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

from system.engine.agents.agents import create_agents

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

_PERSONA_MARKER = "Voce é o agente: "


class _FakeIdentityChatModel(BaseChatModel):
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        system_content = next(
            (m.content for m in messages if isinstance(m, SystemMessage)), ""
        )
        agent_name = ""
        if _PERSONA_MARKER in system_content:
            agent_name = system_content.split(_PERSONA_MARKER, 1)[1].split(" - ", 1)[0]

        content = f"Eu sou o agente {agent_name}.\nNext agent: Nenhum"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def bind_tools(self, tools, *, tool_choice: str | None = None, **kwargs: Any) -> Runnable:
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-identity-chat-model"


@pytest.fixture(scope="module")
def agents():
    fake_llm = _FakeIdentityChatModel()
    with patch("system.engine.agents.agents.create_llms", return_value=(fake_llm, fake_llm)):
        return create_agents()


def _ask_identity(agent):
    result = agent.invoke({"messages": [HumanMessage(content=IDENTITY_QUESTION)]})
    return result["output"]


def test_create_agents_returns_all_expected_agents(agents):
    assert set(agents.keys()) == set(EXPECTED_AGENTS)


@pytest.mark.parametrize("agent_name, expected_substring", EXPECTED_AGENTS.items())
def test_agent_identifies_itself(agents, agent_name, expected_substring):
    response = _ask_identity(agents[agent_name])

    assert expected_substring in response.lower()