"""Regression cover for tool failures that used to abort the whole request.

A consumer-registered langchain `Tool` with more than one argument is still a
single-input tool. When the model calls it with a dict of fields, LangChain
raises `ToolException` and, without handling, that exception escaped
`send_message` as an HTTP 500. A tool that raises on purpose must also come
back as an observation the agent can recover from, not as a crashed run.
"""

from typing import Any

import pytest

from langchain_core.messages import AIMessage, ToolMessage

from langchain_core.outputs import ChatGeneration, ChatResult

from langchain_core.tools import Tool, create_schema_from_function

from langchain_core.tools.structured import StructuredTool


from aeko import Aeko, AekoMessenger, AekoSession, AekoUser

from aeko.engine.agents.agents import _to_lc_tool

from aeko.engine.runtime import RUNTIME


from tests.conftest import FakeChatModel, agent_name_from


API_KEY = "fake-api-key"

USER_ID = "64b8f0a1c9e1a2b3c4d5e6f1"

SESSION_ID = "64b8f0a1c9e1a2b3c4d5e6f3"

REQUEST_ID = "req-64b8f0a1c9e1a2b3c4d5e6f9"

FAQ_ANSWER = "Perfil atualizado para gestor de auditoria."

CHAT_FLOW = {
    "Roteador": "O usuario pediu para atualizar o perfil.\nNext agent: FAQ",
    "FAQ": f"{FAQ_ANSWER}\nNext agent: Nenhum",
}

TOOL_CALL = {
    "name": "update_user_profile",
    "args": {"role": "gestor", "usecase": "auditoria"},
    "id": "call-profile-1",
    "type": "tool_call",
}


class ToolCallingChatModel(FakeChatModel):
    """Scripted model that calls a tool from FAQ, then answers after the tool."""

    def _generate(
        self,
        messages,
        stop=None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a tool call on FAQ's first turn and text everywhere else."""

        agent = agent_name_from(messages)

        last = messages[-1] if messages else None

        if agent == "FAQ" and not isinstance(last, ToolMessage):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content="", tool_calls=[dict(TOOL_CALL)])
                    )
                ]
            )

        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def tool_calling_llm(monkeypatch):
    """Install the tool-calling fake under the whole agent stack."""

    def _use(responses: dict[str, str] | None = None) -> ToolCallingChatModel:
        """Use."""
        fake = ToolCallingChatModel(responses=responses or CHAT_FLOW)

        monkeypatch.setattr(
            "aeko.engine.agents.agents.create_llms", lambda *a, **k: (fake, fake)
        )

        RUNTIME.agents.clear()

        return fake

    yield _use

    RUNTIME.agents.clear()


@pytest.fixture
def configured():
    """Configured."""
    Aeko.config(API_KEY)


def make_user() -> AekoUser:
    """A user as the API would have read it from the user collection."""

    return AekoUser.model_validate(
        {
            "_id": USER_ID,
            "id_external_user": 1001,
            "role": "Gestor ambiental da Ceramica X",
            "usecase": "Acompanha a substituicao de gases dos fornos.",
        }
    )


def make_session() -> AekoSession:
    """A conversation as the API would have read it from the session collection."""

    return AekoSession.model_validate(
        {
            "_id": SESSION_ID,
            "id_user": USER_ID,
            "name": "Suporte Tecnico #12",
            "messages": [],
        }
    )


def _profile_tool(func) -> Tool:
    """Build the consumer-shaped single-input Tool the API actually registers."""

    return Tool(
        name="update_user_profile",
        description="Update the user's role and usecase.",
        func=func,
        args_schema=create_schema_from_function("update_user_profile", func),
    )


def _send_profile_turn():
    """Send the profile-update question through the messenger."""

    return AekoMessenger(make_user()).send_message(
        "Atualize meu perfil para gestor de auditoria.",
        make_session(),
        id_request=REQUEST_ID,
    )


def update_user_profile(role: str | None = None, usecase: str | None = None) -> str:
    """Update role and/or usecase for an existing Mongo user."""

    return f"updated role={role} usecase={usecase}"


def boom_user_profile(role: str | None = None, usecase: str | None = None) -> str:
    """Refuse the profile update the way a domain service would."""

    raise ValueError("role and usecase are both empty")


def test_a_multi_arg_tool_is_promoted_to_a_structured_tool():
    """A multi arg tool is promoted to a structured tool."""
    promoted = _to_lc_tool(_profile_tool(update_user_profile))

    assert isinstance(promoted, StructuredTool)

    assert promoted.name == "update_user_profile"

    assert set(promoted.args) == {"role", "usecase"}

    assert promoted.invoke({"role": "gestor", "usecase": "auditoria"}) == (
        "updated role=gestor usecase=auditoria"
    )


def test_a_multi_arg_tool_call_does_not_abort_the_turn(
    configured, tool_calling_llm
):
    """A multi arg tool call does not abort the turn."""
    calls: list[tuple[str | None, str | None]] = []

    def record_profile(
        role: str | None = None, usecase: str | None = None
    ) -> str:
        """Record the arguments the agent actually passed."""

        calls.append((role, usecase))

        return f"updated role={role} usecase={usecase}"

    AekoMessenger.set_tools({"FAQ": [_profile_tool(record_profile)]})

    tool_calling_llm()

    response = _send_profile_turn()

    assert calls == [("gestor", "auditoria")]

    assert response.message.output == FAQ_ANSWER


def test_a_tool_that_raises_is_recovered_from_instead_of_crashing(
    configured, tool_calling_llm
):
    """A tool that raises is recovered from instead of crashing."""
    AekoMessenger.set_tools({"FAQ": [_profile_tool(boom_user_profile)]})

    tool_calling_llm()

    response = _send_profile_turn()

    assert response.message.output == FAQ_ANSWER
