from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import MessagesState


def _merge_previous_agents(current: dict[str, str], update: dict[str, str]) -> dict[str, str]:
    return {**current, **update}


class NextAgent(TypedDict):
    agent: str
    message: str


class AetherGraphState(MessagesState):
    initial_question: str
    previous_agents: Annotated[dict[str, str], _merge_previous_agents]
    next_agent: NextAgent | None
    pending_agents: list[str]
    guard_rail_requested_changes: list[str]
    guard_rail_retries: int
    company_context: str


def create_initial_state(initial_question: str, company_context: str = "") -> AetherGraphState:
    # MessagesState (and AetherGraphState) is a TypedDict: `field: type = value`
    # in the class body is a dead class attribute, never applied to instances.
    # This factory is the only place defaults are actually populated.
    return AetherGraphState(
        messages=[HumanMessage(content=initial_question)],
        initial_question=initial_question,
        previous_agents={},
        next_agent=None,
        pending_agents=[],
        guard_rail_requested_changes=[],
        guard_rail_retries=0,
        company_context=company_context,
    )
