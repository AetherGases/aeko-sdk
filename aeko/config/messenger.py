from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from aeko.config._text import strip_routing_marker
from aeko.config.dto import AekoTool, Message, MessageResponse, Session, User
from aeko.config.exceptions import SessionNotPreparedError, UnknownAgentError
from aeko.engine.graph.builder import get_app
from aeko.engine.graph.state import create_initial_state
from aeko.engine.prompts import AGENT_NAMES
from aeko.engine.runtime import RUNTIME


def _final_answer(result: dict, seeded: int) -> str:
    """
    Extract the user-facing answer a run produced, if it produced one.

    Only terminal nodes and an approved guardrail append to "messages" (see
    aeko/engine/graph/nodes.py). A run that ends without reaching any of them
    — the guardrail rejecting past its retry cap — leaves "messages" exactly as
    it was seeded, and has no answer to report.

    Args:
        result: The graph's final state.
        seeded: How many messages the run started with.

    Returns:
        str: The final answer, stripped of the agents' routing marker, or an
            empty string when none was produced.
    """

    messages = result.get("messages") or []
    if len(messages) <= seeded:
        return ""

    final = messages[-1]
    content = final.get("content", "") if isinstance(final, dict) else getattr(final, "content", "")

    return strip_routing_marker(content)


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


def _history_from(session: Session) -> list[BaseMessage]:
    """
    Rebuild the conversation the graph should see from a session document.

    Each persisted turn holds both sides of the exchange, so one entry of
    "session.messages" becomes a human message followed by the assistant's
    reply. A turn the guardrail never approved has an empty `output` and
    contributes only the question, which is exactly how it was stored.

    Args:
        session: The session as the API read it from the database.

    Returns:
        list[BaseMessage]: The turns as LangChain messages, oldest first.
    """

    messages: list[BaseMessage] = []

    for turn in session.messages:
        messages.append(HumanMessage(content=turn.input))
        if turn.output:
            messages.append(AIMessage(content=turn.output))

    return messages


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

    The conversation lives in the `Session` given to `prepare()`, mirroring the
    "session" collection: the API reads the document, hands it over, and gets a
    new entry back to append. Nothing is kept process-wide between calls, which
    is what lets a stateless API serve the same conversation from any worker.
    """

    # Tools are process-wide. Registering them per instance would rebuild the
    # shared agent registry behind the other instances' backs, so `set_tools`
    # is deliberately a classmethod.

    def __init__(self):
        self._session: Session | None = None
        self._user: User | None = None

    @classmethod
    def set_tools(cls, tools: dict[str, list[Any]]) -> None:
        """
        Register the tools available to each agent, process-wide.

        Each tool's description is rendered into that agent's prompt *and* the
        tool itself is bound to that agent's executor, so the prompt can never
        advertise something the agent is unable to call. Registering tools
        invalidates the current agents, which are rebuilt on the next run.

        This is also how the agents reach the user's memories: the API registers
        a lookup tool here, and the agents' instructions tell them to consult it
        (see the prompt specs). The SDK deliberately never receives the memories
        itself — reading "user_memory" is the API's job.

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

    def prepare(self, session: Session, user: User) -> Session:
        """
        Open (or resume) a conversation, on behalf of a given user.

        Args:
            session: The conversation, as the API read it from the "session"
                collection. Its `messages` are replayed to the agents as the
                conversation so far, and `send_message()` appends to them.
            user: Who is asking, as the API read it from the "user" collection.
                Their role and usecase become the business context every agent
                reads; the identifiers never reach a prompt.

        Returns:
            Session: The session this messenger now holds — the same object,
                so appended turns are visible to the caller that owns it.
        """

        self._session = session
        self._user = user

        return session

    def send_message(self, message: str) -> MessageResponse:
        """
        Send a user message through the graph and return the reviewed answer.

        The answer is returned as a `Message`, ready to be appended to the
        session document, and is also appended to the held session so a second
        call in the same process sees the turn that just happened. A turn the
        guardrail never approved produces no answer and is not recorded, so a
        rejected draft cannot become context for the next question.

        Args:
            message: The user's message.

        Returns:
            MessageResponse: The turn to persist, the session and user it
                belongs to, plus which agents contributed and the output
                guardrail's verdict.

        Raises:
            SessionNotPreparedError: If `prepare()` hasn't been called.
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
        """

        if self._session is None or self._user is None:
            raise SessionNotPreparedError(
                "Call AekoMessenger.prepare(session, user) before sending messages."
            )

        state = create_initial_state(
            message,
            company_context=self._user.to_prompt_context(),
            history=_history_from(self._session),
        )

        with get_usage_metadata_callback() as usage:
            result = get_app().invoke(
                state, config={"configurable": {"entry_point": "Roteador"}}
            )

        answer = _final_answer(result, seeded=len(state["messages"]))
        llm, input_tokens, output_tokens = _usage_of(usage.usage_metadata)

        turn = Message(
            input=message,
            output=answer,
            llm=llm,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        if answer:
            self._session.messages.append(turn)
            self._session.updated_at = turn.submitted_at

        return MessageResponse(
            message=turn,
            id_session=self._session.id,
            id_user=self._session.id_user,
            agents_called=_agents_called(result),
            approved=bool(result.get("guard_rail_approved")),
            guardrail_retries=int(result.get("guard_rail_retries", 0)),
        )
