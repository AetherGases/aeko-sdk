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
    CONSOLIDATED_ANSWER,
    FULL_FLOW,
    INITIAL_QUESTION,
    REJECTING_FLOW,
)


STAGED_ACK = '{"staged": true}'

FAILURE_SENTENCE = "Não consegui salvar: {tools}."

REJECTED_THEN_APPROVED = {
    **FULL_FLOW,
    "Guardrail de Saída": [
        "Reprovado. Faltam dados.\nNext agent: Nenhum",
        "Aprovado. Resposta fundamentada.\nNext agent: Nenhum",
    ],
}

FAQ_FLOW = {
    "Roteador": "Pergunta simples.\nNext agent: FAQ",
    "FAQ": "Resposta do FAQ.\nNext agent: Nenhum",
}


class ToolCallingChatModel(FakeChatModel):
    """Scripted model whose `tool_caller` agent calls `save_profile` before answering."""

    tool_caller: str = "Roteador"

    watch: list = []

    writes_seen: list = []

    handoffs: list = []

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> ChatResult:
        """Call the tool on the agent's first model turn, script the rest."""

        agent = agent_name_from(messages)

        self.writes_seen.append(len(self.watch))

        if agent == self.tool_caller and not isinstance(messages[-1], ToolMessage):
            self.handoffs.append(str(messages[-1].content))

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

    def _register(agent="Roteador", is_writing=True, fail_on=()):
        """Register; `fail_on` lists the 1-based real calls that raise."""

        calls = []

        def save_profile(role: str) -> str:
            """Save the user's role."""

            calls.append(role)

            if len(calls) in fail_on:
                raise RuntimeError("boom")

            writes.append(role)

            return "saved"

        tool = StructuredTool.from_function(
            func=save_profile,
            name="save_profile",
            description="Save the user's role.",
            metadata={"is_writing": is_writing, "write_failure_message": FAILURE_SENTENCE},
        )

        AekoMessenger.set_tools({agent: [tool]})

    yield _register

    RUNTIME.agents.clear()


@pytest.fixture
def last_turn():
    """What the last `run_turn` left behind: its run handle and delivered answer."""

    return {}


@pytest.fixture
def run_turn(monkeypatch, writes, last_turn):
    """Run the graph inside a bound run, returning the visited nodes and the model."""

    def _run(responses, tool_caller="Roteador"):
        """Run."""

        llm = ToolCallingChatModel(
            responses=responses, tool_caller=tool_caller, watch=writes, writes_seen=[], handoffs=[]
        )

        monkeypatch.setattr(
            "aeko.engine.agents.agents.create_llms", lambda *a, **k: (llm, llm)
        )

        RUNTIME.agents.clear()

        state = create_initial_state(INITIAL_QUESTION)

        with processing(Flow.CONVERSATIONAL, "test") as run:
            updates = list(
                get_app().stream(
                    state,
                    config={"configurable": {"entry_point": "Roteador"}},
                    stream_mode="updates",
                )
            )

        delivered = [
            message["content"]
            for update in updates
            for change in update.values()
            for message in (change or {}).get("messages", [])
        ]

        last_turn.update(run=run, answer=delivered[-1] if delivered else None)

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


def test_a_write_that_fails_once_is_retried_and_reports_nothing(
    register_writer, writes, run_turn, last_turn
):
    """A write that fails once is retried and reports nothing."""
    register_writer(fail_on={1})

    run_turn(FULL_FLOW)

    assert writes == ["gestor"]

    assert last_turn["run"].event_tracking().error_description is None

    assert last_turn["answer"] == f"{CONSOLIDATED_ANSWER}\nNext agent: Guardrail de Saída"


def test_a_write_that_fails_twice_is_reported_in_the_metrics_and_the_answer(
    register_writer, writes, run_turn, last_turn
):
    """A write that fails twice is reported in the metrics and the answer."""
    register_writer(fail_on={1, 2})

    run_turn(FULL_FLOW)

    assert writes == []

    assert (
        last_turn["run"].event_tracking().error_description
        == "Consolidador: save_profile: boom"
    )

    assert last_turn["answer"].endswith("Não consegui salvar: save_profile.")

    assert "gestor" not in last_turn["answer"]


def test_a_persistent_failure_does_not_stop_the_other_writes(
    register_writer, writes, run_turn, last_turn
):
    """A persistent failure does not stop the other writes."""
    register_writer(fail_on={1, 3})

    run_turn(REJECTED_THEN_APPROVED)

    assert writes == ["gestor"]

    assert (
        last_turn["run"].event_tracking().error_description
        == "Consolidador: save_profile: boom"
    )


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


STAGED_BLOCK = "# Escritas Pendentes"


def _system_prompt_of(agent):
    """Render the system prompt the built agent carries."""

    prompt = RUNTIME.agents_for()[agent].agent.runnable.steps[0]

    return prompt.format_messages(messages=[])[0].content


def test_the_prompt_builder_adds_the_staged_block_only_when_told_the_agent_writes():
    """The prompt builder adds the staged block only for agents that write."""
    from aeko.engine.prompts.builder import build_prompt

    def system_of(**kwargs):
        """Render."""

        return build_prompt(agent="X", **kwargs).format_messages(messages=[])[0].content

    assert STAGED_BLOCK in system_of(has_writing_tools=True)

    assert STAGED_BLOCK not in system_of()


def test_only_agents_owning_a_writing_tool_get_the_staged_block(
    register_writer, use_fake_llm
):
    """Only agents owning a writing tool get the staged block."""
    register_writer(agent="Roteador")

    use_fake_llm()

    assert STAGED_BLOCK in _system_prompt_of("Roteador")

    assert STAGED_BLOCK not in _system_prompt_of("FAQ")


def test_a_non_writing_tool_does_not_add_the_staged_block(
    register_writer, use_fake_llm
):
    """A non-writing tool does not add the staged block."""
    register_writer(is_writing=False)

    use_fake_llm()

    assert STAGED_BLOCK not in _system_prompt_of("Roteador")


def test_the_context_message_lists_staged_writes_only_when_there_are_some():
    """The context message lists staged writes only when there are some."""
    from aeko.engine.graph.nodes import _build_context_message

    state = create_initial_state(INITIAL_QUESTION)

    assert "save_profile" not in _build_context_message(state).content

    state["staged_writes"] = [
        {"name": "save_profile", "args": {"role": "gestor"}, "status": "staged"}
    ]

    content = _build_context_message(state).content

    assert "save_profile" in content and "gestor" in content


def test_a_rejected_draft_loops_back_to_a_roteador_that_sees_the_staged_write(
    register_writer, run_turn
):
    """A rejected draft loops back to a Roteador that sees the staged write."""
    register_writer()

    _, llm = run_turn(
        {
            **FULL_FLOW,
            "Guardrail de Saída": [
                "Reprovado. Faltam dados.\nNext agent: Nenhum",
                "Aprovado. Resposta fundamentada.\nNext agent: Nenhum",
            ],
        }
    )

    first, second = llm.handoffs

    assert "save_profile" not in first

    assert "save_profile" in second and "gestor" in second


def test_the_context_message_of_an_agent_that_writes_is_also_built_for_reviewers(
    register_writer,
):
    """A reviewer that owns a writing tool is told about staged writes too."""
    from aeko.engine.graph.nodes import (
        _build_guardrail_message,
        _build_response_check_message,
    )

    register_writer(agent="Guardrail de Saída")

    state = create_initial_state(INITIAL_QUESTION)

    state["staged_writes"] = [
        {"name": "save_profile", "args": {"role": "gestor"}, "status": "staged"}
    ]

    assert "save_profile" in _build_guardrail_message(state).content

    assert "save_profile" not in _build_response_check_message(state).content

    register_writer(agent="Verificador de Resposta")

    assert "save_profile" in _build_response_check_message(state).content
