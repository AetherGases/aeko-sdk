"""
Writing tools stage their calls during a run and the Consolidador commits them.

Whole-graph runs against a scripted model, with a spy standing in for the real
write: what matters is when the spy was actually hit, and what the agent saw.
"""

from typing import Any

import pytest

from langchain_core.messages import AIMessage, ToolMessage

from langchain_core.outputs import ChatGeneration, ChatResult

from langchain_core.tools.structured import StructuredTool


from aeko import Aeko, AekoMessenger

from aeko.engine.graph.builder import get_app

from aeko.engine.graph.state import create_initial_state

from aeko.engine.runtime import RUNTIME

from aeko.shared import Flow, processing


from tests.conftest import FakeChatModel, agent_name_from

from tests.test_graph_e2e import (
    CHECKER_REJECTING_FLOW,
    FULL_FLOW,
    INITIAL_QUESTION,
    REJECTING_FLOW,
)


STAGED_ACK = '{"staged": true}'

FAQ_FLOW = {
    "Roteador": "Pergunta simples.\nNext agent: FAQ",
    "FAQ": "Resposta do FAQ.\nNext agent: Nenhum",
}


class ToolCallingChatModel(FakeChatModel):
    """Scripted model whose `tool_caller` agent calls `save_profile` before answering."""

    tool_caller: str = "Roteador"

    watch: list = []

    writes_seen: list = []

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> ChatResult:
        """Call the tool on the agent's first model turn, script the rest."""

        agent = agent_name_from(messages)

        self.writes_seen.append(len(self.watch))

        if agent == self.tool_caller and not isinstance(messages[-1], ToolMessage):
            call = {
                "name": "save_profile",
                "args": {"role": "gestor"},
                "id": "call-1",
                "type": "tool_call",
            }

            return ChatResult(
                generations=[
                    ChatGeneration(message=AIMessage(content="", tool_calls=[call]))
                ]
            )

        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def writes():
    """The roles the spy writing tool was really asked to save."""

    return []


@pytest.fixture
def register_writer(writes, monkeypatch):
    """Register the spy as a tool of an agent, flagged writing or not."""

    Aeko.config("fake-api-key")

    def _register(agent="Roteador", is_writing=True):
        """Register."""

        def save_profile(role: str) -> str:
            """Save the user's role."""

            writes.append(role)

            return "saved"

        tool = StructuredTool.from_function(
            func=save_profile,
            name="save_profile",
            description="Save the user's role.",
            metadata={"is_writing": is_writing},
        )

        AekoMessenger.set_tools({agent: [tool]})

    yield _register

    RUNTIME.agents.clear()


@pytest.fixture
def run_turn(monkeypatch, writes):
    """Run the graph inside a bound run, returning the visited nodes and the model."""

    def _run(responses, tool_caller="Roteador"):
        """Run."""

        llm = ToolCallingChatModel(
            responses=responses, tool_caller=tool_caller, watch=writes, writes_seen=[]
        )

        monkeypatch.setattr(
            "aeko.engine.agents.agents.create_llms", lambda *a, **k: (llm, llm)
        )

        RUNTIME.agents.clear()

        state = create_initial_state(INITIAL_QUESTION)

        with processing(Flow.CONVERSATIONAL, "test"):
            updates = list(
                get_app().stream(
                    state,
                    config={"configurable": {"entry_point": "Roteador"}},
                    stream_mode="updates",
                )
            )

        return [name for update in updates for name in update], llm

    return _run


def test_an_approved_turn_writes_once_and_only_when_consolidating(
    register_writer, writes, run_turn
):
    """An approved turn writes once and only when consolidating."""
    register_writer()

    visited, llm = run_turn(FULL_FLOW)

    assert writes == ["gestor"]

    assert set(llm.writes_seen) == {0}, "nenhuma escrita enquanto os agentes trabalham"

    assert visited[-2:] == ["Verificador de Resposta", "Consolidador"]

    assert STAGED_ACK in llm.prompt_for("Roteador"), "o agente recebe o ack de staged"


def test_a_guardrail_cap_commits_nothing(register_writer, writes, run_turn):
    """A guardrail cap commits nothing."""
    register_writer()

    visited, _ = run_turn(REJECTING_FLOW)

    assert writes == []

    assert "Consolidador" not in visited


def test_a_checker_cap_commits_nothing(register_writer, writes, run_turn):
    """A Verificador cap commits nothing."""
    register_writer()

    visited, _ = run_turn(CHECKER_REJECTING_FLOW)

    assert writes == []

    assert "Consolidador" not in visited


def test_intents_of_every_attempt_are_committed_when_a_retry_is_approved(
    register_writer, writes, run_turn
):
    """Intents of every attempt are committed when a retry is approved."""
    register_writer()

    rejected_then_approved = {
        **FULL_FLOW,
        "Guardrail de Saída": [
            "Reprovado. Faltam dados.\nNext agent: Nenhum",
            "Aprovado. Resposta fundamentada.\nNext agent: Nenhum",
        ],
    }

    run_turn(rejected_then_approved)

    assert writes == ["gestor", "gestor"]


def test_the_faq_ending_commits_nothing(register_writer, writes, run_turn):
    """The FAQ ending commits nothing."""
    register_writer(agent="FAQ")

    visited, _ = run_turn(FAQ_FLOW, tool_caller="FAQ")

    assert writes == []

    assert "Consolidador" not in visited


def test_a_writing_tool_called_outside_a_run_executes_immediately(
    register_writer, writes, use_fake_llm
):
    """A writing tool called outside a run executes immediately."""
    register_writer()

    use_fake_llm()

    tool = RUNTIME.agents_for()["Roteador"].tools[0]

    assert tool.invoke({"role": "gestor"}) == "saved"

    assert writes == ["gestor"]


def test_the_staging_wrapper_keeps_the_tool_contract(register_writer, use_fake_llm):
    """The staging wrapper keeps the tool contract."""
    register_writer()

    use_fake_llm()

    tool = RUNTIME.agents_for()["Roteador"].tools[0]

    assert tool.name == "save_profile"

    assert tool.description == "Save the user's role."

    assert set(tool.args) == {"role"}


def test_a_tool_not_flagged_as_writing_runs_during_the_turn(
    register_writer, writes, run_turn
):
    """A tool not flagged as writing runs during the turn."""
    register_writer(is_writing=False)

    visited, llm = run_turn(FULL_FLOW)

    assert writes == ["gestor"]

    assert STAGED_ACK not in llm.prompt_for("Roteador")
