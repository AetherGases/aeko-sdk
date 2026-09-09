"""Generate conversation summaries through a lonely side-content agent."""

from typing import Sequence

from langchain_core.messages import HumanMessage

from aeko.config._text import strip_routing_marker
from aeko.config.dto import AekoMessage
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.engine._content import text_of
from aeko.engine.agents import agents as agent_runtime
from aeko.shared import agent_call
from aeko.side_content.constants import SUMMARIZER_AGENT
from aeko.side_content.prompt import SUMMARIZER_SPEC


def _render_transcript(messages: Sequence[AekoMessage]) -> str:
    """
    Render the conversation the summarizer should read.

    Each turn is numbered in the order it was received. A turn the assistant
    never answered contributes only the user's line, which is exactly how it
    was stored.

    Args:
        messages: The turns to summarize, oldest first.

    Returns:
        str: The labelled transcript, or a single line when there are none.
    """

    if not messages:
        return "Não houve mensagens nesta janela."

    lines: list[str] = []

    for index, turn in enumerate(messages, start=1):
        submitted_at = turn.submitted_at.isoformat()
        lines.append(f"Turno {index} ({submitted_at})")
        lines.append(f"Usuário: {turn.input}")

        if turn.output:
            lines.append(f"Assistente: {turn.output}")

    return "\n".join(lines)


def _build_summary_message(messages: Sequence[AekoMessage]) -> HumanMessage:
    """
    Build the isolated handoff message sent to the summarizer.

    Args:
        messages: The turns to summarize, oldest first.

    Returns:
        HumanMessage: The transcript wrapped for the agent to read.
    """

    transcript = _render_transcript(messages)

    return HumanMessage(
        content=(
            "Transcrição da conversa:\n"
            f"{transcript}"
        )
    )


def generate_conversation_summary(messages: Sequence[AekoMessage]) -> str:
    """
    Summarize a conversation window through the lonely summarizer agent.

    The agent is built on demand from the configured fast model and is never
    registered in the LangGraph, which is what keeps this flow outside the
    conversational routing the chat entry point uses.

    Args:
        messages: The turns to summarize, oldest first.

    Returns:
        str: The summary text, stripped of the agents' routing marker.

    Raises:
        AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
        MalformedAgentOutputError: If the agent returns an empty summary.
    """

    fast_llm, _ = agent_runtime.create_llms()
    agent = agent_runtime._build_agent(fast_llm, SUMMARIZER_SPEC, [])
    message = _build_summary_message(messages)

    with agent_call(SUMMARIZER_AGENT) as call:
        result = agent.invoke({"messages": [message]}, config={"callbacks": [call]})

        if "output" in result:
            output = text_of(result["output"])
        else:
            output = text_of(result["messages"][-1].content)

    summary = strip_routing_marker(output).strip()

    if not summary:
        raise MalformedAgentOutputError(
            "the conversation summarizer returned an empty summary"
        )

    return summary
