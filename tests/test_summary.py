"""Tests for AekoMessenger.generate_summary and the summary side-content flow."""

from datetime import datetime, timezone


import pytest


import aeko

from aeko import (
    Aeko,
    AekoMessage,
    AekoMessenger,
    AekoMetrics,
    AekoSummaryResponse,
    AekoUser,
    MalformedAgentOutputError,
)

from aeko.config.exceptions import AekoNotConfiguredError

from aeko.side_content.constants import SUMMARIZER_AGENT


API_KEY = "fake-api-key"

REQUEST_ID = "req-64b8f0a1c9e1a2b3c4d5e6f9"


SUBMITTED_AT = datetime(2026, 8, 28, 12, 5, tzinfo=timezone.utc)

SUBMITTED_AT_2 = datetime(2026, 8, 28, 12, 10, tzinfo=timezone.utc)


SUMMARY_TEXT = (
    "O usuário perguntou sobre hidrogênio verde e recebeu uma explicação "
    "sobre produção por eletrólise com energia renovável."
)


SUMMARY_FLOW = {
    SUMMARIZER_AGENT: f"{SUMMARY_TEXT}\nNext agent: Nenhum",
}


EMPTY_SUMMARY_FLOW = {
    SUMMARIZER_AGENT: "Não houve mensagens na janela de conversa.\nNext agent: Nenhum",
}


BLANK_SUMMARY_FLOW = {
    SUMMARIZER_AGENT: "\nNext agent: Nenhum",
}


def make_messages() -> list[AekoMessage]:
    """Make messages."""
    return [
        AekoMessage(
            input="O que é hidrogênio verde?",
            output="É produzido por eletrólise com energia renovável.",
            submitted_at=SUBMITTED_AT,
        ),
        AekoMessage(
            input="E a amônia verde?",
            output="",
            submitted_at=SUBMITTED_AT_2,
        ),
    ]


def make_empty_user() -> AekoUser:
    """Make empty user."""
    return AekoUser(id="", id_external_user=0, role="", usecase="")


@pytest.fixture
def configured():
    """Configured."""
    Aeko.config(API_KEY)


@pytest.fixture
def summary_messenger(configured, use_fake_llm):
    """Summary messenger."""

    def _build(responses=None, user=None, memories=None):
        """Build."""
        llm = use_fake_llm(responses or SUMMARY_FLOW)

        messenger = AekoMessenger(
            user or make_empty_user(),
            memories=memories or [],
        )

        return messenger, llm

    return _build


def test_the_sdk_version_is_3_8_1():
    """The sdk version is 3 8 1."""
    assert aeko.__version__ == "3.8.2"


def test_aeko_summary_response_is_exported():
    """Aeko summary response is exported."""
    assert "AekoSummaryResponse" in aeko.__all__


def test_a_summary_response_carries_the_summary_and_its_metrics():
    """A summary response carries the summary and its metrics."""
    metrics = AekoMetrics(id_request=REQUEST_ID, flow="conversational")

    response = AekoSummaryResponse(summary=SUMMARY_TEXT, aeko_metrics=metrics)

    assert response.summary == SUMMARY_TEXT

    assert response.aeko_metrics.id_request == REQUEST_ID

    assert response.aeko_metrics.flow == "conversational"


def test_generate_summary_requires_configuration():
    """Generate summary requires configuration."""
    messenger = AekoMessenger(make_empty_user(), [])

    with pytest.raises(AekoNotConfiguredError) as raised:
        messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    assert raised.value.aeko_metrics is not None

    assert raised.value.aeko_metrics.id_request == REQUEST_ID

    assert raised.value.aeko_metrics.flow == "conversational"


@pytest.mark.parametrize("invalid", [123, None])
def test_generate_summary_requires_id_request_as_a_string(invalid, summary_messenger):
    """Generate summary requires id request as a string."""
    messenger, _ = summary_messenger()

    with pytest.raises(TypeError, match="id_request"):
        messenger.generate_summary(make_messages(), id_request=invalid)


def test_generate_summary_requires_id_request_as_keyword_only(summary_messenger):
    """Generate summary requires id request as keyword only."""
    messenger, _ = summary_messenger()

    with pytest.raises(TypeError):
        messenger.generate_summary(make_messages(), REQUEST_ID)


def test_generate_summary_rejects_non_message_items(summary_messenger):
    """Generate summary rejects non message items."""
    messenger, _ = summary_messenger()

    with pytest.raises(TypeError, match="AekoMessage"):
        messenger.generate_summary(
            [{"input": "oi", "output": "ola"}],
            id_request=REQUEST_ID,
        )


def test_generate_summary_returns_a_summary_response(summary_messenger):
    """Generate summary returns a summary response."""
    messenger, _ = summary_messenger()

    response = messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    assert isinstance(response, AekoSummaryResponse)

    assert response.summary == SUMMARY_TEXT


def test_generate_summary_metrics_on_success(summary_messenger):
    """Generate summary metrics on success."""
    messenger, _ = summary_messenger()

    response = messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    assert response.aeko_metrics.id_request == REQUEST_ID

    assert response.aeko_metrics.error_description is None

    assert response.aeko_metrics.flow == "conversational"

    assert response.aeko_metrics.latency >= 0

    assert [agent.name for agent in response.aeko_metrics.used_agents] == [
        SUMMARIZER_AGENT
    ]


def test_generate_summary_works_with_an_empty_user_and_no_memories(summary_messenger):
    """Generate summary works with an empty user and no memories."""
    messenger, _ = summary_messenger()

    response = messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    assert response.summary == SUMMARY_TEXT


def test_generate_summary_renders_messages_in_order(summary_messenger):
    """Generate summary renders messages in order."""
    messenger, llm = summary_messenger()

    messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    _, prompt = llm.calls[-1]

    assert "O que é hidrogênio verde?" in prompt

    assert "É produzido por eletrólise com energia renovável." in prompt

    assert str(SUBMITTED_AT) in prompt or "2026-08-28" in prompt

    assert "E a amônia verde?" in prompt

    turn_two = prompt.split("Turno 2")[-1]

    assert "Assistente:" not in turn_two


def test_generate_summary_still_runs_for_an_empty_message_list(summary_messenger):
    """Generate summary still runs for an empty message list."""
    messenger, llm = summary_messenger(EMPTY_SUMMARY_FLOW)

    response = messenger.generate_summary([], id_request=REQUEST_ID)

    assert response.summary

    assert llm.calls[-1][0] == SUMMARIZER_AGENT


def test_generate_summary_rejects_a_blank_agent_answer(summary_messenger):
    """Generate summary rejects a blank agent answer."""
    messenger, _ = summary_messenger(BLANK_SUMMARY_FLOW)

    with pytest.raises(MalformedAgentOutputError) as raised:
        messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    tracking = raised.value.aeko_metrics

    assert tracking.id_request == REQUEST_ID

    assert tracking.flow == "conversational"

    assert tracking.error_description == (f"MalformedAgentOutputError: {raised.value}")


def test_a_failed_summary_carries_metrics_on_the_exception(
    summary_messenger, monkeypatch
):
    """A failed summary carries metrics on the exception."""
    messenger, _ = summary_messenger()

    def explode(*args, **kwargs):
        """Explode."""
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "aeko.config.messenger.generate_conversation_summary",
        explode,
    )

    with pytest.raises(RuntimeError, match="model unavailable") as raised:
        messenger.generate_summary(make_messages(), id_request=REQUEST_ID)

    tracking = raised.value.aeko_metrics

    assert isinstance(tracking, AekoMetrics)

    assert tracking.id_request == REQUEST_ID

    assert tracking.flow == "conversational"

    assert tracking.error_description == f"RuntimeError: {raised.value}"
