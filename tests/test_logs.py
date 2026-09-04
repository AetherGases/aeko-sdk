"""
Tests for the observability package in aeko/shared/.

Three things are being pinned down here. The line's shape, which is exactly
three brackets and nothing more. Its color, since the color is what a reader
actually reads a log stream by. And, above all, that one request writes one
record: everything a request went through is listed inside that single record,
and nothing at all is written while it is still running.

The colors are asserted as the raw ANSI escapes rather than through the
constants alone, so a palette edited by accident fails here instead of quietly
repainting production logs.
"""

import io
import logging
import re
from datetime import datetime, timedelta

import pytest
from langchain_core.tools import tool

from aeko import Aeko, AekoInventoryAnalyzer, AekoMessenger, AekoSession, AekoUser
from aeko.config.exceptions import (
    AekoNotConfiguredError,
    MalformedAgentOutputError,
    UnknownAgentError,
)
from aeko.engine.prompts import PLAN_SECTIONS
from aeko.shared import (
    BLUE,
    ITEM_PREFIX,
    LIGHT_BLUE,
    LOGGER_NAME,
    RED,
    RESET,
    Flow,
    configure_logging,
    current_run,
    log_failure,
    log_success,
    processing,
    record_agent_call,
)

API_KEY = "fake-api-key"

# What an ANSI sequence opens with, for the tests asserting a line has none.
ESCAPE = "\x1b["

USER_ID = "64b8f0a1c9e1a2b3c4d5e6f1"
SESSION_ID = "64b8f0a1c9e1a2b3c4d5e6f3"
INVENTORY_ID = 502

# What the API correlates one request by, and the only thing it has to supply
# that the SDK cannot derive for itself.
REQUEST_ID = "req-64b8f0a1c9e1a2b3c4d5e6f9"

INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"

QUESTION = "O que e hidrogenio verde?"

# A chat turn that ends at the FAQ: two agents, one answer.
CHAT_FLOW = {
    "Roteador": "Duvida conceitual.\nNext agent: FAQ",
    "FAQ": "Hidrogenio verde e produzido por eletrolise.\nNext agent: Nenhum",
}

# A chat turn the guardrail never approves. It goes through a specialist first
# so the router is allowed to pick the Orquestrador on the retries (see
# `_roteador_node`), which is what lets the rejection actually repeat until the
# cap is exceeded and the turn ends with no answer at all.
REJECTED_FLOW = {
    "Roteador": "Analise tecnica.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": "CO2 critico.\nNext agent: Orquestrador",
    "Orquestrador": "Panorama consolidado.\nNext agent: Guardrail de Saída",
    "Guardrail de Saída": "Reprovado. Sem fundamentacao.\nNext agent: Nenhum",
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

# A report run whose coordinator never writes the requested sections, which is
# what `analyze()` raises on — and therefore what a failed report logs like.
MALFORMED_INVENTORY_FLOW = {
    **INVENTORY_FLOW,
    "Coordenador de Melhoria Contínua": "Resposta em prosa solta.\nNext agent: Nenhum",
}


@tool
def consulta_precos(query: str) -> str:
    """Descricao que a propria tool declara."""

    return ""


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


# A record's first line: the three brackets and the header. There is no fourth
# bracket, and `test_a_record_has_exactly_three_brackets` is what keeps it that
# way — this pattern would happily match one as part of the description.
HEADER = re.compile(
    r"^(?P<color>\x1b\[\d+m)?"
    r"\[(?P<prefix>[^\]]+)\] \[(?P<module>[^\]]+)\] \[(?P<timestamp>[^\]]+)\] "
    r"(?P<description>.*?)"
    r"(?:\x1b\[0m)?$"
)

# One entry of an agents item, e.g. "Roteador (21ms)". The separator is matched
# rather than left to the name, which no agent's name carries anyway.
AGENT = re.compile(r"(?:^|, )(?P<name>[^,]+?) \((?P<millis>\d+)ms\)")


class LoggedRecord:
    """
    One log record, taken apart into the fields it is asserted on.

    A record is a header line plus, for a request, the indented list under it.
    It is written to the handler as one string in one call, and is read back
    here as one object, because that is the property these tests exist to hold:
    one request, one record.

    Attributes:
        header: Its first line, exactly as written.
        color: The ANSI escape it opens with, or an empty string when it
            carries no color at all.
        prefix: The first bracket.
        module: The second bracket.
        timestamp: The third bracket, still as text.
        description: The header, everything after the brackets.
        items: The detail list, label to value, in the order written.
    """

    def __init__(self, header: str, item_lines: list[str]):
        """
        Take one record apart.

        Args:
            header: Its first line.
            item_lines: Its indented list lines, if it has any.

        Raises:
            AssertionError: If the header does not have the specified shape,
                which is itself what pins the format down.
        """

        match = HEADER.match(header)
        assert match, f"linha fora do formato especificado: {header!r}"

        self.header = header
        self.color = match["color"] or ""
        self.prefix = match["prefix"]
        self.module = match["module"]
        self.timestamp = match["timestamp"]
        self.description = match["description"]

        self.items: dict[str, str] = {}
        for line in item_lines:
            label, _, value = line.removeprefix(ITEM_PREFIX).partition(": ")
            self.items[label] = value.removesuffix(RESET)

    @property
    def agents(self) -> list[str]:
        """
        Return the agents this record lists, in call order.

        Returns:
            list[str]: The agent names, one entry per call, so an agent called
                twice appears twice.
        """

        return [match["name"] for match in AGENT.finditer(self.items.get("agents", ""))]

    @property
    def agent_millis(self) -> list[int]:
        """
        Return the durations this record reports for its agents, in call order.

        Returns:
            list[int]: The milliseconds, aligned with `agents`.
        """

        return [
            int(match["millis"]) for match in AGENT.finditer(self.items.get("agents", ""))
        ]

    def __repr__(self) -> str:
        return f"LoggedRecord({self.header!r}, items={self.items!r})"


class LogCapture:
    """
    The records written so far, and the ways a test wants to slice them.

    Attributes:
        stream: The stream the SDK's handler was pointed at.
    """

    def __init__(self, stream: io.StringIO):
        """
        Watch a stream the SDK's handler writes to.

        Args:
            stream: The stream to read the records back from.
        """

        self.stream = stream

    @property
    def records(self) -> list[LoggedRecord]:
        """
        Return every record written so far.

        A line opening with the list indent continues the record above it;
        anything else starts a new one.

        Returns:
            list[LoggedRecord]: The records, in the order they were written.
        """

        records: list[LoggedRecord] = []
        header: str | None = None
        items: list[str] = []

        for line in self.stream.getvalue().splitlines():
            if not line:
                continue

            if line.startswith(ITEM_PREFIX):
                assert header is not None, f"item sem cabecalho: {line!r}"
                items.append(line)
                continue

            if header is not None:
                records.append(LoggedRecord(header, items))

            header, items = line, []

        if header is not None:
            records.append(LoggedRecord(header, items))

        return records

    def of(self, module: str) -> list[LoggedRecord]:
        """
        Return the records written under one module bracket.

        Args:
            module: The module to filter by.

        Returns:
            list[LoggedRecord]: The matching records, in order.
        """

        return [record for record in self.records if record.module == module]

    def one(self, module: str) -> LoggedRecord:
        """
        Return the single record written under one module bracket.

        Args:
            module: The module to filter by.

        Returns:
            LoggedRecord: The record.

        Raises:
            AssertionError: If that module wrote anything other than exactly
                one record, which is the property most of these tests are
                actually about.
        """

        written = self.of(module)
        assert len(written) == 1, f"esperado 1 registro em [{module}], veio {len(written)}"

        return written[0]

    @property
    def text(self) -> str:
        """
        Return everything written, as one blob.

        Returns:
            str: The raw stream contents, for asserting that something is
                absent from the log entirely.
        """

        return self.stream.getvalue()


@pytest.fixture
def logs():
    """
    Point the SDK's logger at a stream this test can read back.

    Colors are forced on rather than left to auto-detection: a StringIO is not
    a terminal, and the colors are part of what these tests exist to check.

    Yields:
        LogCapture: The records written during the test.
    """

    stream = io.StringIO()
    configure_logging(stream=stream, colors=True)

    yield LogCapture(stream)

    # Leave no handler pointing at a stream this test is about to drop: every
    # other test in the suite shares this logger.
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


@pytest.fixture
def chat(logs, use_fake_llm):
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
def report(logs, use_fake_llm):
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


# --- The record's shape --------------------------------------------------


def test_a_record_is_written_as_prefix_module_datetime_and_description(logs):
    log_success("config", "SDK configured")

    record = logs.records[0]

    assert record.prefix == "aeko-sdk"
    assert record.module == "config"
    assert record.description == "SDK configured"


def test_a_record_has_exactly_three_brackets(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    # Every header, request records included: there is no run id, and nothing
    # else has been added behind the timestamp either.
    for record in logs.records:
        bare = record.header.removeprefix(record.color)
        assert re.match(r"^\[[^\]]+\] \[[^\]]+\] \[[^\]]+\] \S", bare)
        assert not record.description.startswith("[")


def test_the_timestamp_is_the_moment_the_record_was_written(logs):
    before = datetime.now()
    log_success("config", "SDK configured")
    after = datetime.now()

    written = datetime.fromisoformat(logs.records[0].timestamp)

    # The bracket is truncated to the millisecond, so a record written at
    # …:00.1239 reads as …:00.123 — a hair *before* the moment it was written.
    assert before - timedelta(milliseconds=1) <= written <= after


def test_the_detail_list_is_written_indented_under_the_header(logs):
    with processing(Flow.CONVERSATIONAL, "messenger") as run:
        run.item("session", SESSION_ID)

    header, item = logs.text.splitlines()

    assert not header.startswith(" ")
    assert item.startswith(f"{ITEM_PREFIX}session: {SESSION_ID}")


def test_a_record_with_nothing_to_list_is_a_single_line(logs):
    with processing(Flow.CONVERSATIONAL, "messenger"):
        pass

    assert len(logs.text.splitlines()) == 1


# --- One request, one record ---------------------------------------------


def test_a_chat_request_writes_exactly_one_record(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert logs.one("messenger")


def test_a_report_request_writes_exactly_one_record(logs, report):
    report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    assert logs.one("inventory")


def test_nothing_is_written_while_the_request_is_still_running(logs):
    with processing(Flow.CONVERSATIONAL, "messenger") as run:
        run.item("session", SESSION_ID)
        record_agent_call("Roteador", 0.021)

        assert logs.text == "", "um pedido em andamento nao deve ter escrito nada ainda"

    assert len(logs.records) == 1


def test_the_agents_a_request_called_write_nothing_of_their_own(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    # The agents are an item of the request's record, never records of theirs.
    assert logs.of("agents") == []


def test_an_agent_called_outside_any_request_is_dropped(logs):
    record_agent_call("Roteador", 0.021)

    assert logs.text == ""


# --- What the list reports -----------------------------------------------


def test_a_chat_request_lists_its_identifiers(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    record = logs.one("messenger")

    assert record.items["session"] == SESSION_ID
    assert record.items["user"] == USER_ID
    assert record.items["input"] == f"{len(QUESTION)} characters, 0 history turns"


def test_a_chat_request_never_logs_the_message_itself(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert QUESTION not in logs.text


def test_a_chat_request_lists_its_agents_in_call_order(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert logs.one("messenger").agents == ["Roteador", "FAQ"]


def test_each_listed_agent_carries_how_long_it_took(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    millis = logs.one("messenger").agent_millis

    assert len(millis) == 2
    assert all(value >= 0 for value in millis)


def test_the_agents_are_listed_last(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert list(logs.one("messenger").items)[-1] == "agents"


def test_an_agent_called_more_than_once_is_listed_once_per_call(logs, chat):
    chat(REJECTED_FLOW).send_message(
        "Compare os escopos.", make_session(), id_request=REQUEST_ID
    )

    agents = logs.one("messenger").agents

    # The guardrail's retry loop runs the same four agents again and again, and
    # the list shows the loop rather than hiding it behind unique names.
    assert agents.count("Roteador") > 1
    assert agents.count("Guardrail de Saída") > 1


def test_a_report_request_lists_its_inventory_and_input(logs, report):
    report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    record = logs.one("inventory")

    assert record.items["inventory"] == str(INVENTORY_ID)
    assert record.items["input"] == f"{len(INVENTORY_MD)} characters"
    assert record.agents[0] == "Análista de inventários"


# --- The colors ----------------------------------------------------------


def test_a_conversational_request_is_light_blue(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert logs.one("messenger").color == LIGHT_BLUE


def test_a_report_request_is_dark_blue(logs, report):
    report(INVENTORY_FLOW).analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    assert logs.one("inventory").color == BLUE


def test_the_two_flows_are_written_in_different_blues():
    assert LIGHT_BLUE != BLUE


def test_a_whole_record_is_written_in_one_color(logs, chat):
    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    block = logs.text.split(logs.one("messenger").header)[1]

    # The color opens on the header and closes once, at the end of the last
    # item — the list is not painted line by line.
    assert block.count(RESET) == 1
    assert block.rstrip("\n").endswith(RESET)
    assert LIGHT_BLUE not in block


def test_a_failure_is_red_whatever_the_flow(logs):
    log_failure("config", "Falhou")
    log_failure("messenger", "Falhou", flow=Flow.CONVERSATIONAL)
    log_failure("inventory", "Falhou", flow=Flow.REPORT)

    assert [record.color for record in logs.records] == [RED, RED, RED]


def test_the_color_never_reaches_the_record_itself(logs, caplog):
    # Attached to the SDK's own logger, not to the root one: the records
    # deliberately never reach the root logger (see the handler tests below),
    # so caplog cannot see them from where pytest installs it.
    logging.getLogger(LOGGER_NAME).addHandler(caplog.handler)

    log_success("config", "SDK configured")

    assert caplog.records[0].message == "SDK configured"
    assert ESCAPE not in caplog.records[0].message


def test_colors_are_left_out_when_the_handler_is_told_to(logs, chat):
    stream = io.StringIO()
    configure_logging(stream=stream, colors=False)

    chat(CHAT_FLOW).send_message(QUESTION, make_session(), id_request=REQUEST_ID)

    assert ESCAPE not in stream.getvalue()
    assert stream.getvalue().startswith("[aeko-sdk] [config] [")


def test_colors_are_left_out_for_a_stream_that_is_not_a_terminal(logs):
    stream = io.StringIO()
    configure_logging(stream=stream)

    log_success("config", "SDK configured")

    assert ESCAPE not in stream.getvalue()


# --- How a request ends --------------------------------------------------


def test_an_exception_is_reported_in_red_and_re_raised_untouched(logs):
    with pytest.raises(ValueError, match="boom"):
        with processing(Flow.REPORT, "inventory"):
            raise ValueError("boom")

    record = logs.one("inventory")

    assert record.color == RED
    assert "Report processing failed in" in record.description
    assert "ValueError: boom" in record.description


def test_a_request_marked_as_failed_is_red_even_without_an_exception(logs, chat):
    chat(REJECTED_FLOW).send_message(
        "Compare os escopos.", make_session(), id_request=REQUEST_ID
    )

    record = logs.one("messenger")

    assert record.color == RED
    assert "no answer approved by the output guardrail" in record.description


def test_a_failed_request_still_lists_what_it_went_through(logs, report):
    with pytest.raises(MalformedAgentOutputError):
        report(MALFORMED_INVENTORY_FLOW).analyze(
            INVENTORY_MD, id_external_inventory=INVENTORY_ID,
            id_request=REQUEST_ID,
        )

    record = logs.one("inventory")

    assert record.color == RED
    assert "MalformedAgentOutputError" in record.description
    assert record.items["inventory"] == str(INVENTORY_ID)
    assert record.agents


def test_the_current_request_is_readable_from_inside_it_only():
    assert current_run() is None

    with processing(Flow.CONVERSATIONAL, "messenger") as run:
        assert current_run() is run

    assert current_run() is None


def test_the_request_is_released_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with processing(Flow.CONVERSATIONAL, "messenger"):
            raise ValueError("boom")

    assert current_run() is None


# --- The handler --------------------------------------------------------


def test_the_sdk_never_logs_through_the_root_logger(logs):
    root = logging.getLogger()
    aeko_logger = logging.getLogger(LOGGER_NAME)

    log_success("config", "SDK configured")

    assert aeko_logger.propagate is False
    assert not any(handler in root.handlers for handler in aeko_logger.handlers)


def test_reconfiguring_replaces_the_handler_instead_of_adding_one(logs):
    stream = io.StringIO()
    configure_logging(stream=stream, colors=True)

    log_success("config", "SDK configured")

    assert len(logging.getLogger(LOGGER_NAME).handlers) == 1
    assert len(stream.getvalue().splitlines()) == 1


def test_the_level_a_consumer_asks_for_is_honoured(logs):
    stream = io.StringIO()
    configure_logging(stream=stream, level=logging.ERROR, colors=True)

    log_success("config", "isto nao deve aparecer")
    log_failure("config", "isto deve aparecer")

    assert "isto nao deve aparecer" not in stream.getvalue()
    assert "isto deve aparecer" in stream.getvalue()


# --- SDK configuration, which is not a request ---------------------------


def test_configuring_the_sdk_is_logged_as_a_single_line_with_no_list(logs):
    Aeko.config(API_KEY, fast_model="modelo-rapido")

    record = logs.one("config")

    assert record.color == BLUE
    assert record.items == {}
    assert record.description.startswith("SDK configured")
    assert "fast_model=modelo-rapido" in record.description


def test_the_api_key_is_never_logged(logs):
    Aeko.config(API_KEY)

    assert API_KEY not in logs.text


def test_a_refused_configuration_is_logged_in_red(logs):
    with pytest.raises(AekoNotConfiguredError):
        Aeko.config("")

    record = logs.one("config")

    assert record.color == RED
    assert record.description == "SDK configuration refused: missing or invalid API key."


def test_resetting_the_sdk_is_logged(logs):
    Aeko.reset()

    record = logs.of("config")[-1]

    assert record.color == BLUE
    assert record.description == "SDK reset to its defaults."


def test_registering_tools_is_logged_without_naming_the_tools(logs):
    AekoMessenger.set_tools({"FAQ": [consulta_precos]})

    record = logs.one("messenger")

    assert record.color == BLUE
    assert "FAQ=1" in record.description
    # Registering a tool is configuration; a tool being used is not logged at
    # all, and its name has no business in the stream either way.
    assert "consulta_precos" not in logs.text


def test_registering_tools_for_an_unknown_agent_is_logged_in_red(logs):
    with pytest.raises(UnknownAgentError):
        AekoMessenger.set_tools({"Agente Inexistente": []})

    record = logs.one("messenger")

    assert record.color == RED
    assert "Agente Inexistente" in record.description


# --- The two flows side by side ------------------------------------------


def test_the_two_flows_are_distinguishable_in_one_stream(logs, use_fake_llm):
    use_fake_llm({**CHAT_FLOW, **INVENTORY_FLOW})
    Aeko.config(API_KEY)

    AekoMessenger(make_user()).send_message(QUESTION, make_session(), id_request=REQUEST_ID)
    AekoInventoryAnalyzer().analyze(
        INVENTORY_MD, id_external_inventory=INVENTORY_ID, id_request=REQUEST_ID
    )

    conversational = logs.one("messenger")
    report_record = logs.one("inventory")

    assert conversational.color == LIGHT_BLUE
    assert report_record.color == BLUE
    assert conversational.description.startswith("Conversational processing")
    assert report_record.description.startswith("Report processing")
