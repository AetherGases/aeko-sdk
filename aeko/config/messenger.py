from typing import Any, Sequence

from langchain_core.callbacks import get_usage_metadata_callback

from aeko.config._text import strip_routing_marker
from aeko.engine._content import text_of
from aeko.config.dto import (
    AekoMessage,
    AekoMessageResponse,
    AekoSession,
    AekoTool,
    AekoUser,
    AekoUserMemory,
)
from aeko.config.exceptions import UnknownAgentError
from aeko.engine.graph.builder import get_app
from aeko.engine.graph.state import create_initial_state
from aeko.engine.prompts import AGENT_NAMES
from aeko.engine.runtime import RUNTIME


def _final_answer(result: dict) -> str:
    """
    Extract the user-facing answer a run produced, if it produced one.

    A run's "messages" starts empty and only a terminal node or an approved
    guardrail ever writes to it (see aeko/engine/graph/nodes.py), so a run that
    ends without reaching any of them — the guardrail rejecting past its retry
    cap — leaves it empty, and has no answer to report.

    Args:
        result: The graph's final state.

    Returns:
        str: The final answer, stripped of the agents' routing marker, or an
            empty string when none was produced.
    """

    messages = result.get("messages") or []
    if not messages:
        return ""

    final = messages[-1]
    content = final.get("content", "") if isinstance(final, dict) else getattr(final, "content", "")

    return strip_routing_marker(text_of(content))


def _agents_called(result: dict) -> list[str]:
    """
    List the agents that contributed to a run, in the order they were called.

    Args:
        result: The graph's final state.

    Returns:
        list[str]: The agent names. Includes the terminal agent that wrote the
            final message even when it recorded no analysis of its own — the FAQ
            answers directly and never writes to "previous_agents".
    """

    called = list(result.get("previous_agents") or {})

    messages = result.get("messages") or []
    if messages:
        final = messages[-1]
        name = final.get("name") if isinstance(final, dict) else getattr(final, "name", None)
        if name and name not in called:
            called.append(name)

    return called


# How many turns of "session.messages" a run is allowed to read back. Enough to
# keep a follow-up question intelligible, bounded so a long conversation neither
# crowds out the actual question nor grows what each turn costs without limit.
# The session itself is never trimmed: this caps what the agents see, not what
# the API persists.
SESSION_HISTORY_USAGE = 10


def _history_from(session: AekoSession) -> str:
    """
    Render the conversation the agents should read from a session document.

    Only the `SESSION_HISTORY_USAGE` most recent turns are replayed, however
    many the session carries — a conversation of 500 turns costs the same as
    one of 10. Each persisted turn holds both sides of the exchange, so one
    entry of "session.messages" becomes a "Usuário:" line followed by an
    "Assistente:" one. A turn the guardrail never approved has an empty
    `output` and contributes only the question, which is exactly how it was
    stored.

    The transcript is rendered here, where the turns actually live, rather than
    seeded into the graph as messages for a node to flatten back into text: no
    agent is ever invoked with a running transcript (see
    `_build_context_message`), so that intermediate form had no reader.

    Args:
        session: The session as the API read it from the database.

    Returns:
        str: One labelled line per side of each replayed turn, oldest first,
            empty for a conversation that has none.
    """

    lines: list[str] = []

    for turn in session.messages[-SESSION_HISTORY_USAGE:]:
        lines.append(f"Usuário: {turn.input}")
        if turn.output:
            lines.append(f"Assistente: {turn.output}")

    return "\n".join(lines)


# The label the memories are rendered under, inside the context every agent
# reads. They are deliberately a section of their own rather than more lines of
# the user's role/usecase: what the user *is* and what has been *remembered*
# about them are different claims, and the agents are instructed accordingly.
MEMORIES_LABEL = "Memórias do usuário:"


def _memories_context(memories: Sequence[AekoUserMemory]) -> str:
    """
    Render the user's memories as the section the agents read.

    Every memory handed over is rendered, however many there are: unlike the
    conversation (see `SESSION_HISTORY_USAGE`), a memory is already the
    condensed form of something the user told us once, and dropping any of them
    would silently un-remember it. Which memories are still valid is the API's
    call — it filters `expires_at` before handing them over.

    Args:
        memories: The user's memories, as the API read them from "user_memory".

    Returns:
        str: The labelled block, one "- <field>: <description>" line per
            memory, or an empty string when there are none — an empty section
            is one more thing for a model to read meaning into.
    """

    if not memories:
        return ""

    lines = "\n".join(f"- {memory.to_prompt_line()}" for memory in memories)

    return f"{MEMORIES_LABEL}\n{lines}"


def _usage_of(usage_metadata: dict[str, dict]) -> tuple[str, int, int]:
    """
    Summarize what a run consumed, as the "session.messages" fields record it.

    A single turn can cross both configured models — the router and the FAQ run
    on the fast one, an analyst on the slow one — while the collection keeps a
    single `llm` field, so every model that served the turn is named there.

    Args:
        usage_metadata: Model name to its token usage, as collected by
            LangChain's usage callback. Empty when the provider reports no
            usage at all.

    Returns:
        tuple[str, int, int]: The models used, the prompt tokens and the
            completion tokens, all zeroed/empty when nothing was reported.
    """

    llm = ", ".join(usage_metadata)
    input_tokens = sum(usage.get("input_tokens", 0) for usage in usage_metadata.values())
    output_tokens = sum(usage.get("output_tokens", 0) for usage in usage_metadata.values())

    return llm, input_tokens, output_tokens


class AekoMessenger:
    """
    Conversational entry point: routes a user message through the agent graph.

    A messenger holds only *who* is asking. The conversation itself travels
    with each `send_message()` call as an `AekoSession` the API rehydrated from
    the "session" collection, and is updated in place before the call returns.
    Nothing about a conversation is retained here between calls, which is what
    lets a stateless API serve any session from any worker — and what keeps the
    process from accumulating session state it would never be able to evict.

    The session may carry any number of turns; only the most recent ones are
    read back into a run (see `SESSION_HISTORY_USAGE`).
    """

    # Tools are process-wide. Registering them per instance would rebuild the
    # shared agent registry behind the other instances' backs, so `set_tools`
    # is deliberately a classmethod.

    def __init__(self, user: AekoUser, memories: Sequence[AekoUserMemory] | None = None):
        """
        Open a messenger on behalf of a given user.

        Args:
            user: Who is asking, as the API read it from the "user" collection.
                Their role and usecase become the business context every agent
                reads; the identifiers never reach a prompt.
            memories: What is remembered about them, as the API read it from
                the "user_memory" collection. All of them are rendered into
                that same context, so every agent of the run reads them — the
                memories belong to the user, which is why they are taken here
                rather than per message. A user with none is normal.
        """

        self._user = user
        self._memories = list(memories or [])

    def _context(self) -> str:
        """
        Build the business context this messenger's runs are given.

        Returns:
            str: The user's role and usecase followed by their memories, each
                section left out when it has nothing to say.
        """

        sections = [self._user.to_prompt_context(), _memories_context(self._memories)]

        return "\n\n".join(section for section in sections if section)

    @classmethod
    def set_tools(cls, tools: dict[str, list[Any]]) -> None:
        """
        Register the tools available to each agent, process-wide.

        Each tool's description is rendered into that agent's prompt *and* the
        tool itself is bound to that agent's executor, so the prompt can never
        advertise something the agent is unable to call. Registering tools
        invalidates the current agents, which are rebuilt on the next run.

        Args:
            tools: Agent name to its tools. Each entry may be an `AekoTool`,
                carrying the description the model should read, or a bare
                LangChain tool, in which case its own description is used.

        Raises:
            UnknownAgentError: If a key is not one of the system's agents.
        """

        normalized: dict[str, list[AekoTool]] = {}
        for agent, agent_tools in tools.items():
            if agent not in AGENT_NAMES:
                raise UnknownAgentError(agent, AGENT_NAMES)

            normalized[agent] = [AekoTool.wrap(tool) for tool in agent_tools]

        RUNTIME.configure(tools=normalized)

    def send_message(self, message: str, session: AekoSession) -> AekoMessageResponse:
        """
        Send a user message through the graph and return the reviewed answer.

        The session is the API's, rehydrated from the "session" collection on
        every request: its `messages` are rendered as the run's conversational
        context, and the answered turn is appended back to them in place,
        together with a bumped `updated_at`, so the caller can persist the same
        object it handed over. Only the `SESSION_HISTORY_USAGE` most recent
        turns are read back into the run; the session keeps every turn it
        arrived with.

        Only a final result is recorded. A turn the guardrail never approved
        produces no answer and is left out of the session, so a rejected draft
        cannot become context for the next question.

        Args:
            message: The user's message.
            session: The conversation this message belongs to.

        Returns:
            AekoMessageResponse: The turn to persist, the session and user it
                belongs to, plus which agents contributed and the output
                guardrail's verdict.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
        """

        state = create_initial_state(
            message,
            company_context=self._context(),
            history=_history_from(session),
        )

        with get_usage_metadata_callback() as usage:
            result = get_app().invoke(
                state, config={"configurable": {"entry_point": "Roteador"}}
            )

        answer = _final_answer(result)
        llm, input_tokens, output_tokens = _usage_of(usage.usage_metadata)

        turn = AekoMessage(
            input=message,
            output=answer,
            llm=llm,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        if answer:
            session.messages.append(turn)
            session.updated_at = turn.submitted_at

        return AekoMessageResponse(
            message=turn,
            id_session=session.id,
            id_user=session.id_user,
            agents_called=_agents_called(result),
            approved=bool(result.get("guard_rail_approved")),
            guardrail_retries=int(result.get("guard_rail_retries", 0)),
        )
