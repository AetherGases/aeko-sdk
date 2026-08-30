from typing import Annotated, TypedDict

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
    """
    The state one graph run carries.

    "messages" is inherited from MessagesState but is an *output* channel here,
    not a transcript: it starts empty and only a terminal node or an approved
    guardrail ever writes to it (see aeko/engine/graph/nodes.py), so "the run
    produced a user-facing answer" is exactly "messages is not empty". No agent
    is ever invoked with it — each one gets a single isolated message built by
    `_build_context_message`, and the prior conversation reaches them through
    "history" instead, already rendered as the text they read.
    """

    initial_question: str
    history: str
    previous_agents: Annotated[dict[str, str], _merge_previous_agents]
    next_agent: NextAgent | None
    pending_agents: Annotated[list[PendingAgents], _merge_pending_agents]
    guard_rail_requested_changes: list[str]
    guard_rail_retries: int
    guard_rail_approved: bool
    company_context: str


def create_initial_state(initial_question: str, company_context: str = "",
                         history: str = "") -> AetherGraphState:
    """
    Build the initial graph state for a new conversation.

    Args:
        initial_question: The user's opening question.
        company_context: Optional context about the requesting company.
        history: Optional prior turns, already rendered as the transcript the
            agents read, so a session resumed from outside the process keeps
            its conversational context. The SDK is consumed by an API that owns
            persistence, so the caller is the one that knows how much of it is
            worth replaying (see `AekoMessenger._history_from`).

    Returns:
        AetherGraphState: A fully-populated initial state with empty defaults.
    """

    # MessagesState (and AetherGraphState) is a TypedDict: `field: type = value`
    # in the class body is a dead class attribute, never applied to instances.
    # This factory is the only place defaults are actually populated.
    return AetherGraphState(
        messages=[],
        initial_question=initial_question,
        history=history,
        previous_agents={},
        next_agent=None,
        pending_agents=[],
        guard_rail_requested_changes=[],
        guard_rail_retries=0,
        guard_rail_approved=False,
        company_context=company_context,
    )
