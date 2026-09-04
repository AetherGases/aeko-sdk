"""
Regression cover for agents that answer in content blocks.

Since Gemini 3, a model that actually used a tool stops answering with a bare
string and returns a list of content blocks instead — the reasoning it did, then
the text. `AgentExecutor` hands that list straight through as its "output" (see
`parse_ai_message_to_tool_action`, which sets `return_values={"output":
message.content}`), so every `.split()` downstream of an agent died with
`AttributeError: 'list' object has no attribute 'split'`.

It stayed latent until the first tool was registered: with no tools, no agent
ever thinks, and every answer is still a plain string.
"""

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import tool

from aeko import Aeko, AekoMessenger, AekoSession, AekoUser
from aeko.engine._content import text_of
from aeko.engine.graph.nodes import _invoke_agent
from aeko.engine.runtime import RUNTIME

from tests.conftest import FakeChatModel

API_KEY = "fake-api-key"
USER_ID = "64b8f0a1c9e1a2b3c4d5e6f1"
SESSION_ID = "64b8f0a1c9e1a2b3c4d5e6f3"

# What the API correlates one request by, and the only thing it has to supply
# that the SDK cannot derive for itself.
REQUEST_ID = "req-64b8f0a1c9e1a2b3c4d5e6f9"

FAQ_ANSWER = "Hidrogenio verde e produzido por eletrolise com energia renovavel."

# The block an agent's private thinking arrives in. No assertion in this file
# may ever find it in an answer: it is the model reasoning with itself.
THINKING = "O usuario quer uma definicao; vou consultar a tool e resumir."

CHAT_FLOW = {
    "Roteador": "Duvida conceitual.\nNext agent: FAQ",
    "FAQ": f"{FAQ_ANSWER}\nNext agent: Nenhum",
}


@tool
def tavily_map(query: str) -> str:
    """Mapeia fontes sobre o tema consultado."""

    return ""


class ThinkingChatModel(FakeChatModel):
    """
    A scripted model that answers the way a Gemini 3 model that used a tool does.

    Same scripted answers as `FakeChatModel`, re-wrapped as the two content
    blocks the real provider returns, so the whole stack above it is exercised
    against a list instead of a string.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        result = super()._generate(messages, stop, run_manager, **kwargs)
        generation = result.generations[0]

        generation.message = generation.message.model_copy(update={
            "content": [
                {"type": "reasoning", "reasoning": THINKING},
                {"type": "text", "text": generation.message.content},
            ],
        })

        return result


@pytest.fixture
def thinking_llm(monkeypatch):
    """Install the block-answering model under the whole agent stack."""

    def _use(responses: dict[str, str] | None = None) -> ThinkingChatModel:
        fake = ThinkingChatModel(responses=responses or {})
        monkeypatch.setattr(
            "aeko.engine.agents.agents.create_llms", lambda *a, **k: (fake, fake)
        )
        RUNTIME.agents.clear()
        return fake

    yield _use

    RUNTIME.agents.clear()


def make_user() -> AekoUser:
    """A user as the API would have read it from the "user" collection."""

    return AekoUser.model_validate({
        "_id": USER_ID,
        "id_external_user": 1001,
        "role": "Gestor ambiental da Ceramica X",
        "usecase": "Acompanha a substituicao de gases dos fornos.",
    })


def make_session() -> AekoSession:
    """A conversation as the API would have read it from the "session" collection."""

    return AekoSession.model_validate({
        "_id": SESSION_ID,
        "id_user": USER_ID,
        "name": "Suporte Técnico #12",
        "messages": [],
    })


# --- text_of -------------------------------------------------------------


def test_a_string_is_returned_unchanged():
    assert text_of(FAQ_ANSWER) == FAQ_ANSWER


def test_text_blocks_are_concatenated():
    blocks = [{"type": "text", "text": "Hidrogenio verde "}, {"type": "text", "text": "e limpo."}]

    assert text_of(blocks) == "Hidrogenio verde e limpo."


def test_reasoning_blocks_are_dropped():
    # The whole point: thinking is the model talking to itself, and leaking it
    # into an answer would be worse than the crash this replaced.
    blocks = [{"type": "reasoning", "reasoning": THINKING}, {"type": "text", "text": FAQ_ANSWER}]

    assert text_of(blocks) == FAQ_ANSWER


def test_content_with_no_text_becomes_the_empty_string():
    assert text_of([{"type": "reasoning", "reasoning": THINKING}]) == ""
    assert text_of([]) == ""


# --- the graph -----------------------------------------------------------


def test_an_agent_answering_in_blocks_still_routes(configured, thinking_llm):
    # `_invoke_agent` reads the "Next agent: " marker off the output. This is
    # the exact line that raised AttributeError in production.
    thinking_llm(CHAT_FLOW)

    output, next_agent = _invoke_agent("Roteador", HumanMessage(content="O que e H2 verde?"))

    assert output == "Duvida conceitual.\nNext agent: FAQ"
    assert next_agent == {"agent": "FAQ", "message": output}


def test_a_tool_calling_agent_delivers_plain_text(configured, thinking_llm):
    AekoMessenger.set_tools({"FAQ": [tavily_map]})
    thinking_llm(CHAT_FLOW)
    session = make_session()

    response = AekoMessenger(make_user()).send_message(
        "O que e H2 verde?", session, id_request=REQUEST_ID
    )

    assert response.message.output == FAQ_ANSWER
    assert THINKING not in response.message.output
    assert session.messages[-1].output == FAQ_ANSWER


@pytest.fixture
def configured():
    Aeko.config(API_KEY)
