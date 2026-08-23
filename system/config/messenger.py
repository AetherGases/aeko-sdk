from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from system.config._text import strip_routing_marker
from system.config.dto import AekoTool, MessageResponse, SessionInfo
from system.config.exceptions import SessionNotPreparedError, UnknownAgentError
from system.engine.graph.builder import get_app
from system.engine.graph.state import create_initial_state, history_to_messages
from system.engine.prompts import AGENT_NAMES
from system.engine.runtime import RUNTIME


def _final_answer(result: dict, seeded: int) -> str:
    """
    Extract the user-facing answer a run produced, if it produced one.

    Only terminal nodes and an approved guardrail append to "messages" (see
    system/engine/graph/nodes.py). A run that ends without reaching any of them
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


class AekoMessenger:
    """
    Conversational entry point: routes a user message through the agent graph.

    Sessions are kept in process, keyed by the id given to `prepare()`. Since
    the SDK is consumed by a stateless API, `prepare()` also accepts the prior
    turns, so a session can be rebuilt from wherever the API persists it.
    """

    # Sessions and tools are both process-wide. Registering tools per instance
    # would rebuild the shared agent registry behind the other instances' backs,
    # so `set_tools` is deliberately a classmethod.
    _sessions: dict[str, list[BaseMessage]] = {}

    def __init__(self):
        self._session: SessionInfo | None = None

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

        RUNTIME.tools = normalized
        RUNTIME.notify_changed()

    def prepare(self, session_id: str, user_info: str,
                history: Sequence[Any] | None = None) -> SessionInfo:
        """
        Open (or resume) a conversation session.

        Args:
            session_id: The conversation's id. Reusing an id resumes it.
            user_info: Free-form information about the user and their company,
                forwarded to every agent as context.
            history: Prior turns, oldest first, as {"role", "content"} dicts or
                LangChain messages. Needed only when resuming a session this
                process doesn't hold — after an API restart, or on another
                worker. Passing it replaces whatever this process had.

        Returns:
            SessionInfo: The session handle, reporting how many turns it holds.
        """

        if history is not None:
            self._sessions[session_id] = list(history_to_messages(history))
        else:
            self._sessions.setdefault(session_id, [])

        self._session = SessionInfo(
            session_id=session_id,
            user_info=user_info,
            turns=len(self._sessions[session_id]),
        )

        return self._session

    def send_message(self, message: str) -> MessageResponse:
        """
        Send a user message through the graph and return the reviewed answer.

        Args:
            message: The user's message.

        Returns:
            MessageResponse: The final answer, which agents contributed, and
                the output guardrail's verdict.

        Raises:
            SessionNotPreparedError: If `prepare()` hasn't been called.
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
        """

        if self._session is None:
            raise SessionNotPreparedError(
                "Call AekoMessenger.prepare(session_id, user_info) before sending messages."
            )

        history = self._sessions[self._session.session_id]

        state = create_initial_state(
            message, company_context=self._session.user_info, history=history
        )

        result = get_app().invoke(
            state, config={"configurable": {"entry_point": "Roteador"}}
        )

        answer = _final_answer(result, seeded=len(state["messages"]))

        if answer:
            history.extend([HumanMessage(content=message), AIMessage(content=answer)])

        return MessageResponse(
            session_id=self._session.session_id,
            answer=answer,
            agents_called=_agents_called(result),
            approved=bool(result.get("guard_rail_approved")),
            guardrail_retries=int(result.get("guard_rail_retries", 0)),
        )
