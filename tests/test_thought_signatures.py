"""
Regression cover for the Gemini 3 thought-signature contract.

Gemini 3 rejects any request whose replayed `functionCall` parts carry no
`thought_signature`, which is exactly the shape an `AgentExecutor` sends on the
turn after a tool ran: the model's own message, the tool's answer, and then ask
again. Under `langchain-google-genai` 2.1.12 the signature was neither read from
the response nor sent back, so every agent that called a tool died with a 400 on
its second turn.

These tests pin both halves of what keeps that from coming back: the executor
replaying the model's message untouched, and the provider putting a signature on
every function call it sends.
"""

import base64

import pytest
from langchain_classic.agents.format_scratchpad.tools import format_to_tool_messages
from langchain_classic.agents.output_parsers.tools import ToolAgentAction
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai.chat_models import (
    SKIP_THOUGHT_SIGNATURE_VALIDATOR,
    _FUNCTION_CALL_THOUGHT_SIGNATURES_MAP_KEY,
    _parse_chat_history,
)

from aeko.engine.runtime import DEFAULT_FAST_MODEL, DEFAULT_SLOW_MODEL

# The private history parser is what turns LangChain messages into the request
# Gemini actually receives. Reaching for it is deliberate: the whole failure was
# invisible one layer above it, in messages that looked perfectly well-formed.

TOOL_CALL_ID = "call-1"
SIGNATURE = b"assinatura-devolvida-pelo-modelo"

# Both configured models are Gemini 3, so both are under the requirement; a
# future model id that isn't would make these tests say so out loud.
CONFIGURED_MODELS = (DEFAULT_FAST_MODEL, DEFAULT_SLOW_MODEL)


def _tool_call_message(signature: bytes | None = None) -> AIMessage:
    """
    Build the message a model returns when it decides to call a tool.

    Args:
        signature: The thought signature the provider recorded for the call, or
            None to simulate a message that never carried one (replayed
            history, or a turn produced by the other model through the cross
            fallback in `create_llms`).

    Returns:
        AIMessage: The tool-calling message, signature sidecar included.
    """

    additional_kwargs = {}

    if signature is not None:
        additional_kwargs[_FUNCTION_CALL_THOUGHT_SIGNATURES_MAP_KEY] = {
            TOOL_CALL_ID: base64.b64encode(signature).decode()
        }

    return AIMessage(
        content="",
        tool_calls=[{"name": "tavily_map", "args": {"query": "escopo 3"}, "id": TOOL_CALL_ID}],
        additional_kwargs=additional_kwargs,
    )


def _replayed_history(signature: bytes | None = None) -> list:
    """
    Build the history an agent executor sends on the turn after a tool ran.

    Args:
        signature: Passed through to `_tool_call_message`.

    Returns:
        list: The user's question, the model's tool call, and the tool's answer,
            assembled the same way the executor's scratchpad assembles them.
    """

    message = _tool_call_message(signature)
    action = ToolAgentAction(
        tool="tavily_map",
        tool_input={"query": "escopo 3"},
        log="",
        message_log=[message],
        tool_call_id=TOOL_CALL_ID,
    )

    return [
        HumanMessage(content="Quais sao as emissoes de escopo 3?"),
        *format_to_tool_messages([(action, "resultado da busca")]),
    ]


def _function_call_parts(model: str, messages: list) -> list:
    """
    Return the function-call parts of the request Gemini would receive.

    Args:
        model: The model id the history is being parsed for, which is what
            decides whether the signature requirement applies at all.
        messages: The messages to parse.

    Returns:
        list: Every part carrying a `function_call`, across all contents.
    """

    _, contents = _parse_chat_history(messages, model=model)

    return [
        part
        for content in contents
        for part in (content.parts or [])
        if part.function_call
    ]


def test_the_scratchpad_replays_the_models_own_message():
    # The executor must hand back the very object the model produced: rebuilding
    # an equivalent AIMessage would drop the signature sidecar and put the 400
    # right back, no matter which provider version is installed.
    message = _tool_call_message(SIGNATURE)

    replayed = format_to_tool_messages([(
        ToolAgentAction(
            tool="tavily_map",
            tool_input={"query": "escopo 3"},
            log="",
            message_log=[message],
            tool_call_id=TOOL_CALL_ID,
        ),
        "resultado da busca",
    )])

    assert replayed[0] is message


@pytest.mark.parametrize("model", CONFIGURED_MODELS)
def test_a_replayed_tool_call_carries_its_thought_signature(model):
    parts = _function_call_parts(model, _replayed_history(SIGNATURE))

    assert [part.thought_signature for part in parts] == [SIGNATURE]


@pytest.mark.parametrize("model", CONFIGURED_MODELS)
def test_a_tool_call_with_no_signature_falls_back_to_the_bypass(model):
    # Nothing recorded a signature for this call, which is what a fallback
    # between the fast and slow models produces. Sending the documented bypass
    # sentinel is the only legal way out: a signature copied from an unrelated
    # response is rejected, and sending none is the 400 this whole file is about.
    parts = _function_call_parts(model, _replayed_history())

    assert [part.thought_signature for part in parts] == [SKIP_THOUGHT_SIGNATURE_VALIDATOR]
