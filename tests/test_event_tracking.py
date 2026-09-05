"""
Tests for the event tracking a request hands back, from aeko/shared/.

The log tells a terminal how a request went; the event tracking tells a
database the same thing. What is pinned down here is that the two never drift
apart — the agents an `AekoMetrics` lists are the agents the log lists, in the
same order, one entry per call — and that nothing is measured twice: the
elapsed time and the token usage a run already paid for are the ones reported.

The event tracking is deliberately kept *out* of every document the API
persists as-is. It travels alongside the turn and alongside the plan, never
inside them, which is what the shape assertions below exist to hold.
"""

from time import perf_counter

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from aeko import (
    Aeko,
    AekoAgentMetrics,
    AekoAnalysisResponse,
    AekoImprovementPlan,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoMetrics,
    AekoSession,
    AekoUser,
)
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.engine.prompts import PLAN_SECTIONS

from tests.conftest import FakeChatModel, agent_name_from

API_KEY = "fake-api-key"

USER_ID = "64b8f0a1c9e1a2b3c4d5e6f1"
SESSION_ID = "64b8f0a1c9e1a2b3c4d5e6f3"
INVENTORY_ID = 502

# What the API correlates one request by, and the only thing it has to supply
# that the SDK cannot derive for itself.
REQUEST_ID = "req-64b8f0a1c9e1a2b3c4d5e6f9"

QUESTION = "O que e hidrogenio verde?"

INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"

# A chat turn that ends at the FAQ: two agents, one answer.
CHAT_FLOW = {
    "Roteador": "Duvida conceitual.\nNext agent: FAQ",
    "FAQ": "Hidrogenio verde e produzido por eletrolise.\nNext agent: Nenhum",
}

# A chat turn the guardrail never approves, so the same agents are called again
# and again and the turn ends with no answer at all.
REJECTED_FLOW = {
    "Roteador": "Analise tecnica.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "CO2 critico.\nNext agent: Orquestrador",
    "Orquestrador": "Panorama consolidado.\nNext agent: Guardrail de Saída",
    "Guardrail de Saída": "Reprovado. Sem fundamentacao.\nNext agent: Nenhum",
}

REVIEWED_FLOW = {
    "Roteador": "Analise tecnica.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "CO2 critico.\nNext agent: Orquestrador",
    "Orquestrador": "Panorama consolidado.\nNext agent: Guardrail de Saída",
    "Guardrail de Saída": "Aprovado.\nNext agent: Nenhum",
    "Verificador de Resposta": "Aprovado.\nNext agent: Nenhum",
}

PLAN_FIELDS = {
    "defined_problem": "Os fornos a gas natural concentram a emissao de CO2.",
    "method": "Migrar a carga termica para hidrogenio verde.",
    "reasoning": "A combustao e a fonte dominante e o ROI paga a troca.",
}


def as_sections(fields: dict[str, str]) -> str:
    """
    Write plan fields the way the coordinator's prompt tells it to.

    Args:
        fields: Plan field name to the text it should carry.

    Returns:
        str: The answer, as the coordinator would have written it.
    """

    return "\n\n".join(
        f"## {PLAN_SECTIONS[field]}\n{text}" for field, text in fields.items()
    )


INVENTORY_FLOW = {
    "Análista de inventários": "Escopo 1 = 1.200 tCO2e.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "Combustao dominante.\nNext agent: Orquestrador",
    "Coordenador de Melhoria Contínua": as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum",
}

# A report whose coordinator never writes the requested sections, which is what
# `analyze()` raises on — and therefore the only way a report flow ends without
# a response to carry its event tracking back.
MALFORMED_INVENTORY_FLOW = {
    **INVENTORY_FLOW,
    "Coordenador de Melhoria Contínua": "Resposta em prosa solta.\nNext agent: Nenhum",
}


@tool
def consulta_precos(query: str) -> str:
    """Preco medio do hidrogenio verde no mercado spot."""

    return "USD 4,50/kg"


@tool
def consulta_fatores(query: str) -> str:
    """Fator de emissao publicado para o combustivel consultado."""

    return "56,1 kgCO2/GJ"


class ToolCallingFakeModel(FakeChatModel):
    """
    A scripted model that actually makes an agent call a tool before answering.

    `FakeChatModel` never emits a tool call, so every agent above it finishes
    in one turn and no tool is ever executed — which is exactly the case the
    `used_tools` assertions cannot be written against. This one answers with a
    tool call the first time a named agent asks, and with that agent's scripted
    text on the turn after, so the executor's tool loop really runs.

    Attributes:
        tool_calling_agents: Agent name to the tool it should call once.
        tools_emitted: The agents already served a tool call, so each one is
            sent through the loop exactly once and the run still terminates.
    """

    tool_calling_agents: dict[str, str] = {}
    tools_emitted: list[str] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        agent = agent_name_from(messages)
        wanted = self.tool_calling_agents.get(agent)

        if not wanted or agent in self.tools_emitted:
            return super()._generate(messages, stop, run_manager, **kwargs)

        self.tools_emitted.append(agent)

        # Reported like any other call: a turn spent calling a tool is still a
        # turn the run paid tokens for.
        message = AIMessage(
            content="",
            tool_calls=[{"name": wanted, "args": {"query": "h2"}, "id": "call_1"}],
            usage_metadata={
                "input_tokens": self.usage_input_tokens,
                "output_tokens": self.usage_output_tokens,
                "total_tokens": self.usage_input_tokens + self.usage_output_tokens,
            },
            response_metadata={"model_name": self.model},
        )

        return ChatResult(generations=[ChatGeneration(message=message)])


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


def by_name(tracking: AekoMetrics) -> dict[str, AekoAgentMetrics]:
    """
    Index an event tracking's agents by name, keeping the first call of each.

    Args:
        tracking: The event tracking to read.

    Returns:
        dict[str, AekoAgentMetrics]: The agents, keyed by name.
    """

    indexed: dict[str, AekoAgentMetrics] = {}

    for agent in tracking.used_agents:
        indexed.setdefault(agent.name, agent)

    return indexed


@pytest.fixture
def chat(use_fake_llm):
    """
    A configured messenger whose agents answer from a script.

    Returns:
        Callable[[dict[str, str]], AekoMessenger]: Installs the scripted flow
            and returns the messenger to send through.
    """

    def _chat(responses: dict[str, str]) -> AekoMessenger:
        use_fake_llm(responses)
        Aeko.config(API_KEY)
        return AekoMessenger(make_user())

    return _chat


@pytest.fixture
def report(use_fake_llm):
    """
    A configured analyzer whose agents answer from a script.

    Returns:
        Callable[[dict[str, str]], AekoInventoryAnalyzer]: Installs the
            scripted flow and returns the analyzer to run through.
    """

    def _report(responses: dict[str, str]) -> AekoInventoryAnalyzer:
        use_fake_llm(responses)
        Aeko.config(API_KEY)
        return AekoInventoryAnalyzer()

    return _report


@pytest.fixture
def tool_calling_chat(monkeypatch):
    """
    A messenger whose agents really call the tools registered for them.

    Returns:
        Callable[..., AekoMessenger]: Takes the scripted flow and the agent to
            tool mapping, installs both, and returns the messenger.
    """

    def _chat(responses: dict[str, str], calling: dict[str, str]) -> AekoMessenger:
        fake = ToolCallingFakeModel(responses=responses, tool_calling_agents=calling)
        monkeypatch.setattr(
            "aeko.engine.agents.agents.create_llms", lambda *a, **k: (fake, fake)
        )
        from aeko.engine.runtime import RUNTIME

        RUNTIME.agents.clear()
        Aeko.config(API_KEY)
        return AekoMessenger(make_user())

    return _chat


# --- What comes back -----------------------------------------------------


def test_a_chat_response_carries_its_event_tracking(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    assert isinstance(response.aeko_metrics, AekoMetrics)


def test_an_analysis_carries_both_the_plan_and_its_event_tracking(report):
    response = report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    assert isinstance(response, AekoAnalysisResponse)
    assert isinstance(response.plan, AekoImprovementPlan)
    assert isinstance(response.aeko_metrics, AekoMetrics)
    assert response.plan.defined_problem == PLAN_FIELDS["defined_problem"]


def test_both_flows_echo_the_request_id_back(chat, report):
    answered = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )
    analyzed = report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    assert answered.aeko_metrics.id_request == REQUEST_ID
    assert analyzed.aeko_metrics.id_request == REQUEST_ID


def test_neither_flow_runs_without_a_request_id(chat, report):
    # Keyword-only and required: an event tracking nobody can correlate is not
    # worth persisting, so the SDK refuses to produce one.
    with pytest.raises(TypeError):
        chat(CHAT_FLOW).send_message(QUESTION, make_session())

    with pytest.raises(TypeError):
        report(INVENTORY_FLOW).analyze(
            INVENTORY_MD, id_external_inventory=INVENTORY_ID
        )


def test_the_event_tracking_stays_out_of_the_persisted_documents(chat, report):
    session = make_session()
    answered = chat(CHAT_FLOW).send_message(QUESTION, session, id_request=REQUEST_ID)
    analyzed = report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    # One entry of "session.messages" and one "improvement_plan" document,
    # neither of which the collections have a metrics field for.
    assert "aeko_metrics" not in answered.message.model_dump()
    assert "aeko_metrics" not in session.messages[-1].model_dump()
    assert "aeko_metrics" not in analyzed.plan.model_dump(by_alias=True)


# --- The flow it belongs to ----------------------------------------------


def test_a_chat_request_is_tracked_as_conversational(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    assert response.aeko_metrics.flow == "conversational"


def test_a_report_request_is_tracked_as_analytical(report):
    response = report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    assert response.aeko_metrics.flow == "analytical"


# --- Latency -------------------------------------------------------------


def test_the_latency_is_the_whole_request_in_whole_milliseconds(chat):
    messenger = chat(CHAT_FLOW)

    started = perf_counter()
    response = messenger.send_message(QUESTION, make_session(), id_request=REQUEST_ID)
    elapsed_millis = (perf_counter() - started) * 1000

    latency = response.aeko_metrics.latency

    # Whole milliseconds, so a request measured at 23.8ms reports 24 — a hair
    # over what the clock around it saw, and the only slack this allows.
    assert isinstance(latency, int)
    assert 0 <= latency <= elapsed_millis + 1


# --- The agents a request went through -----------------------------------


def test_the_agents_are_listed_in_call_order(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    called = [agent.name for agent in response.aeko_metrics.used_agents]

    assert called == ["Roteador", "FAQ"]


def test_an_agent_called_more_than_once_is_listed_once_per_call(chat):
    with pytest.raises(MalformedAgentOutputError) as raised:
        chat(REJECTED_FLOW).send_message(
            "Compare os escopos.", make_session(), id_request=REQUEST_ID
        )

    called = [agent.name for agent in raised.value.aeko_metrics.used_agents]

    # The guardrail's retry loop runs the same agents again and again, and the
    # event tracking shows the loop rather than collapsing it into unique names
    # — a turn that cost four routings is not a turn that cost one.
    assert called.count("Roteador") > 1
    assert called.count("Guardrail de Saída") > 1


def test_a_report_lists_the_analysts_it_entered_through(report):
    response = report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    called = [agent.name for agent in response.aeko_metrics.used_agents]

    assert called[0] == "Análista de inventários"
    assert "Roteador" not in called


def test_each_agent_carries_the_tokens_its_own_call_consumed(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    llm = FakeChatModel()

    for agent in response.aeko_metrics.used_agents:
        assert agent.input_tokens == llm.usage_input_tokens
        assert agent.output_tokens == llm.usage_output_tokens


def test_each_agent_carries_the_model_that_served_it(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    assert {agent.llm for agent in response.aeko_metrics.used_agents} == {"fake-model"}


def test_what_a_turn_cost_is_reported_here_and_nowhere_else(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    agents = response.aeko_metrics.used_agents

    # The turn is what the user said and what came back, nothing more: a
    # rolled-up copy of these numbers on the persisted entry would be a second
    # record of one fact, free to drift from this one.
    assert set(response.message.model_dump()) == {"input", "output", "submitted_at"}
    assert sum(agent.input_tokens for agent in agents) > 0
    assert sum(agent.output_tokens for agent in agents) > 0


# --- The tools an agent called -------------------------------------------


def test_an_agent_lists_the_tools_it_actually_called(tool_calling_chat):
    AekoMessenger.set_tools({"FAQ": [consulta_precos]})
    messenger = tool_calling_chat(CHAT_FLOW, {"FAQ": "consulta_precos"})

    response = messenger.send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert by_name(response.aeko_metrics)["FAQ"].used_tools == ["consulta_precos"]


def test_a_tool_an_agent_never_called_is_not_listed(tool_calling_chat):
    # Both are registered and both are advertised in the FAQ's prompt; only the
    # one the agent actually reached for is a tool the run used.
    AekoMessenger.set_tools({"FAQ": [consulta_precos, consulta_fatores]})
    messenger = tool_calling_chat(CHAT_FLOW, {"FAQ": "consulta_precos"})

    response = messenger.send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert by_name(response.aeko_metrics)["FAQ"].used_tools == ["consulta_precos"]


def test_an_agent_that_called_no_tool_lists_none(tool_calling_chat):
    AekoMessenger.set_tools({"FAQ": [consulta_precos]})
    messenger = tool_calling_chat(CHAT_FLOW, {"FAQ": "consulta_precos"})

    response = messenger.send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert by_name(response.aeko_metrics)["Roteador"].used_tools == []


def test_a_run_with_no_tools_registered_lists_none(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    assert all(agent.used_tools == [] for agent in response.aeko_metrics.used_agents)


# --- How a request ended -------------------------------------------------


def test_a_request_that_went_well_describes_no_error(chat):
    response = chat(CHAT_FLOW).send_message(
        QUESTION, make_session(), id_request=REQUEST_ID
    )

    assert response.aeko_metrics.error_description is None


def test_a_turn_no_reviewer_approved_carries_its_tracking_on_the_exception(chat):
    # It raises and delivers nothing, so the tracking travels on the exception
    # for the same reason a failed analysis does: there is no return value left.
    with pytest.raises(MalformedAgentOutputError) as raised:
        chat(REJECTED_FLOW).send_message(
            "Compare os escopos.", make_session(), id_request=REQUEST_ID
        )

    tracking = raised.value.aeko_metrics

    assert isinstance(tracking, AekoMetrics)
    assert tracking.id_request == REQUEST_ID
    assert tracking.flow == "conversational"
    assert "no answer approved by the output guardrail" in (
        tracking.error_description or ""
    )


def test_the_response_checker_is_accounted_for_like_any_other_agent(chat):
    response = chat(REVIEWED_FLOW).send_message(
        "Compare os escopos.", make_session(), id_request=REQUEST_ID
    )

    checker = by_name(response.aeko_metrics)["Verificador de Resposta"]

    assert checker.input_tokens > 0
    assert checker.output_tokens > 0


def test_a_failed_analysis_carries_its_event_tracking_on_the_exception(report):
    with pytest.raises(MalformedAgentOutputError) as raised:
        report(MALFORMED_INVENTORY_FLOW).analyze(
            INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
        )

    tracking = raised.value.aeko_metrics

    # There is no return value to hang it on, and a request that failed is the
    # one the API most needs to have persisted.
    assert isinstance(tracking, AekoMetrics)
    assert tracking.id_request == REQUEST_ID
    assert "MalformedAgentOutputError" in (tracking.error_description or "")


def test_the_event_tracking_of_a_failed_request_still_lists_what_it_ran(report):
    with pytest.raises(MalformedAgentOutputError) as raised:
        report(MALFORMED_INVENTORY_FLOW).analyze(
            INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
        )

    tracking = raised.value.aeko_metrics

    assert tracking.flow == "analytical"
    assert tracking.latency >= 0
    assert [agent.name for agent in tracking.used_agents]
