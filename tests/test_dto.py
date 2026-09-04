"""
Tests for the DTOs in aeko/config/dto.py.

The SDK never touches MongoDB — the consuming API does — so the contract these
tests lock in is the hand-off: a raw document goes in, the same document comes
back out, and nothing the database uses for bookkeeping ever reaches a prompt.

The documents below are the collections verbatim, so a schema change on either
side breaks here first.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aeko import (
    AekoAgentMetrics,
    AekoImprovementPlan,
    AekoMessage,
    AekoMessageResponse,
    AekoMetrics,
    AekoSession,
    AekoUser,
    AekoUserMemory,
)
from aeko.config.dto import LOG_ONLY_FIELDS

USER_DOC = {
    "_id": "64b8f0a1c9e1a2b3c4d5e6f1",
    "id_external_user": 1001,
    "role": "environment analyzer",
    "usecase": "The user has been asking the AI to analyze the made gases substitution.",
}

USER_MEMORY_DOC = {
    "_id": "64b8f0a1c9e1a2b3c4d5e6f2",
    "id_user": "64b8f0a1c9e1a2b3c4d5e6f1",
    "field": "preferred_language",
    "description": "AekoUser prefers responses in Portuguese",
    "created_at": "2026-08-28T12:00:00Z",
    "expires_at": "2027-08-28T12:00:00Z",
}

MESSAGE_DOC = {
    "input": "Como redefinir minha senha?",
    "output": (
        "Você pode redefinir sua senha clicando no link 'Esqueci minha senha' "
        "na tela de login."
    ),
    "submitted_at": "2026-08-28T12:05:00Z",
}

SESSION_DOC = {
    "_id": "64b8f0a1c9e1a2b3c4d5e6f3",
    "id_user": "64b8f0a1c9e1a2b3c4d5e6f1",
    "name": "Suporte Técnico #12",
    "messages": [MESSAGE_DOC],
    "created_at": "2026-08-28T12:04:00Z",
    "updated_at": "2026-08-28T12:05:00Z",
}

IMPROVEMENT_PLAN_DOC = {
    "_id": "64b8f0a1c9e1a2b3c4d5e6f4",
    "id_external_inventory": 502,
    "defined_problem": (
        "Gases do interior das vacas estão sendo acumulado devido a má alimentação"
    ),
    "method": (
        "Incluir alimentação de maior qualidade e consultas com veterinários "
        "para acompanhamento"
    ),
    "reasoning": (
        "A emissão de gases em corpos vivos, estão na maioria das vezes, "
        "relacioadas a alimentação."
    ),
    "updated_at": "2026-08-28T12:10:00Z",
}

COLLECTIONS = [
    pytest.param(AekoUser, USER_DOC, id="user"),
    pytest.param(AekoUserMemory, USER_MEMORY_DOC, id="user_memory"),
    pytest.param(AekoMessage, MESSAGE_DOC, id="session.messages"),
    pytest.param(AekoSession, SESSION_DOC, id="session"),
    pytest.param(AekoImprovementPlan, IMPROVEMENT_PLAN_DOC, id="improvement_plan"),
]


# --- mirroring the collections -------------------------------------------


@pytest.mark.parametrize("model, document", COLLECTIONS)
def test_a_document_survives_the_round_trip_unchanged(model, document):
    parsed = model.model_validate(document)

    assert parsed.model_dump(by_alias=True, mode="json") == document


@pytest.mark.parametrize("model, document", COLLECTIONS)
def test_the_dto_declares_exactly_the_collections_fields(model, document):
    dumped = model.model_validate(document).model_dump(by_alias=True)

    assert set(dumped) == set(document), (
        "a DTO nao pode inventar nem esquecer campos da collection"
    )


def test_the_document_id_is_read_and_written_as_underscore_id():
    parsed = AekoSession.model_validate(SESSION_DOC)

    assert parsed.id == SESSION_DOC["_id"]
    assert "_id" in parsed.model_dump(by_alias=True)
    assert "_id" not in parsed.model_dump(), "sem by_alias o campo e o `id` pythonico"


def test_a_dto_can_be_built_in_python_by_field_name():
    session = AekoSession(id="abc", id_user="def", name="Nova conversa")

    assert session.model_dump(by_alias=True)["_id"] == "abc"


def test_timestamps_are_parsed_as_timezone_aware_datetimes():
    memory = AekoUserMemory.model_validate(USER_MEMORY_DOC)

    assert memory.created_at == datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    assert memory.expires_at == datetime(2027, 8, 28, 12, 0, tzinfo=timezone.utc)


# --- defaults ------------------------------------------------------------


def test_a_message_only_needs_what_the_user_sent():
    message = AekoMessage(input="Como redefinir minha senha?")

    assert message.output == ""
    assert message.submitted_at.tzinfo is not None, "o timestamp precisa ser tz-aware"


def test_a_brand_new_session_starts_empty():
    session = AekoSession()

    assert session.messages == []
    assert (session.id, session.id_user, session.name) == (None, None, "")


def test_a_user_without_a_characterized_usecase_is_valid():
    user = AekoUser(id_external_user=1001, role="environment analyzer")

    assert user.usecase == ""


def test_an_improvement_plan_timestamps_itself():
    plan = AekoImprovementPlan(
        id_external_inventory=502, defined_problem="p", method="m", reasoning="r"
    )

    assert plan.updated_at.tzinfo is not None


# --- integrity -----------------------------------------------------------


@pytest.mark.parametrize("missing", ["id_external_user", "role"])
def test_a_user_without_its_required_fields_is_rejected(missing):
    document = {key: value for key, value in USER_DOC.items() if key != missing}

    with pytest.raises(ValidationError):
        AekoUser.model_validate(document)


@pytest.mark.parametrize("missing", ["defined_problem", "method", "reasoning"])
def test_an_improvement_plan_without_its_required_fields_is_rejected(missing):
    document = {
        key: value for key, value in IMPROVEMENT_PLAN_DOC.items() if key != missing
    }

    with pytest.raises(ValidationError):
        AekoImprovementPlan.model_validate(document)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_a_negative_token_count_is_rejected(field):
    # The counts live on the event tracking now, per agent invocation — the
    # turn itself carries no cost of its own (see `AekoMessage`).
    with pytest.raises(ValidationError):
        AekoAgentMetrics(name="FAQ", **{field: -1})


def test_a_non_numeric_external_id_is_rejected():
    with pytest.raises(ValidationError):
        AekoUser.model_validate({**USER_DOC, "id_external_user": "mil e um"})


def test_a_session_validates_the_messages_it_carries():
    with pytest.raises(ValidationError):
        AekoSession.model_validate({**SESSION_DOC, "messages": [{"output": "sem pergunta"}]})


# --- what reaches the prompt ---------------------------------------------


def test_the_user_context_carries_the_business_information():
    context = AekoUser.model_validate(USER_DOC).to_prompt_context()

    assert USER_DOC["role"] in context
    assert USER_DOC["usecase"] in context


def test_the_user_context_omits_the_bookkeeping_fields():
    context = AekoUser.model_validate(USER_DOC).to_prompt_context()

    assert USER_DOC["_id"] not in context
    assert str(USER_DOC["id_external_user"]) not in context


def test_a_user_with_nothing_to_say_renders_no_context():
    assert AekoUser(id_external_user=1001, role="").to_prompt_context() == ""


def test_a_memory_renders_as_its_field_and_description():
    line = AekoUserMemory.model_validate(USER_MEMORY_DOC).to_prompt_line()

    assert line == "preferred_language: AekoUser prefers responses in Portuguese"


def test_a_memory_never_shows_the_model_when_it_expires():
    line = AekoUserMemory.model_validate(USER_MEMORY_DOC).to_prompt_line()

    assert "2027" not in line, "a validade e filtro da API, nao decisao do modelo"
    assert USER_MEMORY_DOC["id_user"] not in line


def test_the_log_only_fields_are_declared_for_the_whole_sdk():
    assert set(LOG_ONLY_FIELDS) == {
        "_id", "id", "id_external_user", "id_user", "expires_at"
    }


# --- AekoMessageResponse -----------------------------------------------------


def make_metrics() -> AekoMetrics:
    """
    The event tracking every response carries, as `send_message()` fills it.

    These tests are about the envelope, not about what it measured, so the
    cheapest valid one will do — what matters here is that a response cannot be
    built without one.

    Returns:
        AekoMetrics: An event tracking for a request that went fine.
    """

    return AekoMetrics(id_request="req-1", flow="conversational")


def make_metrics() -> AekoMetrics:
    """
    The event tracking every response carries, as `send_message()` fills it.

    These tests are about the envelope, not about what it measured, so the
    cheapest valid one will do — what matters here is that a response cannot be
    built without one.

    Returns:
        AekoMetrics: An event tracking for a request that went fine.
    """

    return AekoMetrics(id_request="req-1", flow="conversational")


def test_the_response_carries_a_persistable_message_plus_run_metadata():
    response = AekoMessageResponse(
        message=AekoMessage.model_validate(MESSAGE_DOC),
        aeko_metrics=make_metrics(),
        aeko_metrics=make_metrics(),
        agents_called=["Roteador", "FAQ"],
        approved=True,
    )

    assert response.message.model_dump(mode="json") == MESSAGE_DOC, (
        "o que a API grava e exatamente uma entrada de session.messages"
    )
    assert response.guardrail_retries == 0


def test_the_response_carries_the_identifiers_it_belongs_to():
    response = AekoMessageResponse(
        message=AekoMessage(input="oi"),
        aeko_metrics=make_metrics(),
        aeko_metrics=make_metrics(),
        id_session=SESSION_DOC["_id"],
        id_user=USER_DOC["_id"],
    )

    assert response.id_session == SESSION_DOC["_id"]
    assert response.id_user == USER_DOC["_id"]


def test_the_identifiers_stay_out_of_the_persisted_message():
    response = AekoMessageResponse(
        message=AekoMessage(input="oi"),
        aeko_metrics=make_metrics(),
        id_session="s1",
        id_user="u1",
        message=AekoMessage(input="oi"),
        aeko_metrics=make_metrics(),
        id_session="s1",
        id_user="u1",
    )

    assert set(response.message.model_dump()) == set(MESSAGE_DOC), (
        "session.messages nao tem identificadores; eles ficam no envelope"
    )


def test_the_run_metadata_stays_out_of_the_persisted_message():
    response = AekoMessageResponse(
        message=AekoMessage(input="oi"),
        aeko_metrics=make_metrics(),
        agents_called=["FAQ"],
        approved=True,
        message=AekoMessage(input="oi"),
        aeko_metrics=make_metrics(),
        agents_called=["FAQ"],
        approved=True,
    )

    assert "agents_called" not in response.message.model_dump()
    assert "approved" not in response.message.model_dump()
