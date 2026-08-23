from typing import Annotated, TypedDict

from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import MessagesState


def _merge_previous_agents(current: dict[str, str], update: dict[str, str]) -> dict[str, str]:
    """
    Reducer for the "previous_agents" state field: merges in new agent outputs.

    Args:
        current: The existing agent-name-to-output mapping.
        update: New entries to merge in, overwriting any matching agent names.

    Returns:
        dict[str, str]: The merged mapping.
    """

    return {**current, **update}

def _merge_pending_agents(current: list[dict[str, str]], update: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Reducer for the "pending_agents" state field: keeps one entry per agent.

    Args:
        current: The existing list of pending-agent entries.
        update: New entries to merge in, replacing any existing entry for the
            same agent.

    Returns:
        list[dict[str, str]]: The merged list, with one entry per agent name.
    """

    # Merge the two lists, keeping only the latest entry for each agent
    merged = {agent["agent"]: agent for agent in current}
    for agent in update:
        merged[agent["agent"]] = agent
    return list(merged.values())


class NextAgent(TypedDict):
    agent: str
    message: str

class PendingAgents(TypedDict):
    agent: str
    is_still_pending: bool

class AetherGraphState(MessagesState):
    initial_question: str
    previous_agents: Annotated[dict[str, str], _merge_previous_agents]
    next_agent: NextAgent | None
    pending_agents: Annotated[list[PendingAgents], _merge_pending_agents]
    guard_rail_requested_changes: list[str]
    guard_rail_retries: int
    guard_rail_approved: bool
    company_context: str


def history_to_messages(history: Sequence[Any] | None) -> list[BaseMessage]:
    """
    Convert caller-supplied conversation history into LangChain messages.

    The SDK is consumed by an API that owns persistence, so a session may have
    to be rebuilt from turns held elsewhere (a database, another worker). This
    accepts both already-built messages and plain {"role", "content"} dicts.

    Args:
        history: Prior turns, oldest first, or None.

    Returns:
        list[BaseMessage]: The converted messages, in the same order.
    """

    if not history:
        return []

    messages: list[BaseMessage] = []
    for turn in history:
        if isinstance(turn, BaseMessage):
            messages.append(turn)
            continue

        role = turn.get("role", "user")
        content = turn.get("content", "")
        messages.append(
            HumanMessage(content=content) if role in ("user", "human")
            else AIMessage(content=content)
        )

    return messages


def create_initial_state(initial_question: str, company_context: str = "",
                         history: Sequence[Any] | None = None) -> AetherGraphState:
    """
    Build the initial graph state for a new conversation.

    Args:
        initial_question: The user's opening question, seeded as the last message.
        company_context: Optional context about the requesting company.
        history: Optional prior turns to seed before the question, so a session
            resumed from outside the process keeps its conversational context.

    Returns:
        AetherGraphState: A fully-populated initial state with empty defaults.
    """

    # MessagesState (and AetherGraphState) is a TypedDict: `field: type = value`
    # in the class body is a dead class attribute, never applied to instances.
    # This factory is the only place defaults are actually populated.
    return AetherGraphState(
        messages=[*history_to_messages(history), HumanMessage(content=initial_question)],
        initial_question=initial_question,
        previous_agents={},
        next_agent=None,
        pending_agents=[],
        guard_rail_requested_changes=[],
        guard_rail_retries=0,
        guard_rail_approved=False,
        company_context=company_context,
    )
