from typing import Callable

from langchain_core.messages import HumanMessage

from system.engine.graph.state import AetherGraphState, NextAgent
from system.engine.agents.agents import create_agents

_AGENTS = create_agents()


def _format_findings(previous_agents: dict[str, str]) -> str:
    """
    Format previous_agents entries as a "- name: output" bullet list.

    Args:
        previous_agents: Mapping of agent name to its output.

    Returns:
        str: The bullet-formatted findings, one per line.
    """

    return "\n".join(f"- {name}: {output}" for name, output in previous_agents.items())


def _build_context_message(state: AetherGraphState) -> HumanMessage:
    """
    Build an isolated handoff message from structured state, not raw history.

    Every agent's own prompt is designed (see its few-shot examples in
    system/engine/prompts/) around a single self-contained human turn, never
    around replaying another agent's raw output as chat history. Feeding an
    agent the accumulated `state["messages"]` would eventually end on another
    agent's "ai" turn, which Gemini silently answers with empty content, and
    also blurs each agent's persona with whatever it reads as "said by the
    user". Building this message from `initial_question` plus a structured
    summary of `previous_agents` avoids both problems.

    Also includes the Guardrail's most recent rejection feedback, if any, so
    that whoever is invoked next (typically the Roteador, deciding where to
    retry) can tell whether the gap is a missing specialist analysis or just
    a consolidation/tone fix, instead of guessing blind.

    Args:
        state: The current graph state.

    Returns:
        HumanMessage: The original question, a summary of any specialist
            findings gathered so far, and the latest guardrail feedback.
    """

    parts = [state["initial_question"]]

    previous = state.get("previous_agents") or {}
    if previous:
        parts.append(f"Análises recebidas até agora:\n{_format_findings(previous)}")

    requested_changes = state.get("guard_rail_requested_changes") or []
    if requested_changes:
        parts.append(
            f"Pontos apontados pelo Guardrail de Saída na tentativa anterior:\n{requested_changes[-1]}"
        )

    return HumanMessage(content="\n\n".join(parts))


def _build_guardrail_message(state: AetherGraphState) -> HumanMessage:
    """
    Build the isolated review request sent to the output guardrail.

    Matches the shape the guardrail's own prompt is designed for (see its
    few-shot examples): the draft, the original question, and the specialist
    findings backing it up. Without the question and findings, the guardrail
    has no way to fulfill its own stated task of checking that the draft is
    actually grounded in the analyses and fully answers the original request
    ("sem lacunas relevantes") — it can only judge tone in a vacuum.

    Args:
        state: The current graph state, whose "previous_agents" holds the
            Orquestrador's draft answer and the specialists' findings.

    Returns:
        HumanMessage: The review request wrapping the draft, the original
            question, and any supporting findings.
    """

    previous = state.get("previous_agents") or {}
    draft = previous.get("Orquestrador", "")
    parts = [
        f"Revise esta resposta: '{draft}'",
        f"Pergunta original do usuário: {state['initial_question']}",
    ]

    findings = {name: output for name, output in previous.items() if name != "Orquestrador"}
    if findings:
        parts.append(f"Análises recebidas:\n{_format_findings(findings)}")

    return HumanMessage(content="\n\n".join(parts))


def _invoke_agent(agent_name: str, message: HumanMessage) -> tuple[str, NextAgent | None]:
    """
    Invoke a named agent with a single isolated message and parse its routing decision.

    Args:
        agent_name: The key of the agent to invoke in `_AGENTS`.
        message: The isolated handoff message built for this agent.

    Returns:
        tuple[str, NextAgent | None]: The agent's raw output text, and the next
            agent to route to (or None if the agent signaled no follow-up).

    Raises:
        ValueError: If the agent's output names a next agent that doesn't exist.
    """

    output = _AGENTS[agent_name].invoke({"messages": [message]})["output"]

    raw_next = output.split("Next agent: ")[-1].strip() if "Next agent: " in output else ""

    if raw_next in ("", "Nenhum"):
        next_agent = None
    elif raw_next not in _AGENTS:
        raise ValueError(f"Next agent '{raw_next}' is not a valid agent name.")
    else:
        next_agent: NextAgent = {"agent": raw_next, "message": output}

    return output, next_agent


def _specialist_node_factory(agent_name: str, *, terminal: bool = False) -> Callable[[AetherGraphState], dict]:
    """
    Create a graph node for a specialist analyst agent.

    The node always records its output in "previous_agents" (so later agents
    can build on its findings) and marks itself no longer pending in
    "pending_agents". It only writes to "messages" when `terminal` is True,
    i.e. when this agent's edge leads directly to END and its output is the
    literal answer delivered to the user (e.g. the continuous improvement
    coordinator).

    Args:
        agent_name: The key of the agent this node should invoke.
        terminal: Whether this agent's output is the final user-facing answer.

    Returns:
        Callable[[AetherGraphState], dict]: A graph node function.
    """

    def node(state: AetherGraphState) -> dict:
        message = _build_context_message(state)
        output, next_agent = _invoke_agent(agent_name, message)

        update = {
            "previous_agents": {agent_name: output},
            "pending_agents": [{"agent": agent_name, "is_still_pending": False}],
            "next_agent": next_agent,
        }

        if terminal:
            update["messages"] = [{"role": "assistant", "content": output, "name": agent_name}]

        return update

    return node


def _non_specialist_node_factory(agent_name: str, *, terminal: bool = False) -> Callable[[AetherGraphState], dict]:
    """
    Create a graph node for a non-specialist agent (router, FAQ).

    Only writes to "messages" when `terminal` is True, i.e. when this agent's
    edge leads directly to END and its output is the literal answer delivered
    to the user (e.g. FAQ). The router never is, since it only classifies and
    hands off, so its own routing text never becomes a user-facing chat turn.

    Args:
        agent_name: The key of the agent this node should invoke.
        terminal: Whether this agent's output is the final user-facing answer.

    Returns:
        Callable[[AetherGraphState], dict]: A graph node function.
    """

    def node(state: AetherGraphState) -> dict:
        message = _build_context_message(state)
        output, next_agent = _invoke_agent(agent_name, message)

        update = {"next_agent": next_agent}

        if terminal:
            update["messages"] = [{"role": "assistant", "content": output, "name": agent_name}]

        return update

    return node


def _roteador_node(state: AetherGraphState) -> dict:
    """
    Invoke the router, code-enforcing that "Orquestrador" is only ever chosen
    when there's already something for it to consolidate.

    The router's own prompt instructs it to pick "Orquestrador" only on a
    retry with existing specialist findings, never on a fresh question — but
    that's a soft constraint on a fast/lightweight model, and this exact
    mistake has already happened once. Overriding it here in code closes the
    gap for good, independent of how reliably the prompt is followed, by
    falling back to "FAQ" instead.

    Args:
        state: The current graph state.

    Returns:
        dict: The state update.
    """

    message = _build_context_message(state)
    output, next_agent = _invoke_agent("Roteador", message)

    if next_agent and next_agent["agent"] == "Orquestrador" and not state.get("previous_agents"):
        next_agent = {"agent": "FAQ", "message": output}

    return {"next_agent": next_agent}


def _orquestrador_node(state: AetherGraphState) -> dict:
    """
    Invoke the orchestrator and record its draft answer for the guardrail to review.

    The orchestrator's edge always leads to the guardrail, never to END, so its
    draft is kept out of "messages" entirely; only an approved draft is later
    promoted into the user-facing history by `_guardrail_node`.

    Args:
        state: The current graph state.

    Returns:
        dict: The state update, recording the draft in "previous_agents".
    """

    message = _build_context_message(state)
    output, next_agent = _invoke_agent("Orquestrador", message)

    return {
        "previous_agents": {"Orquestrador": output},
        "next_agent": next_agent,
    }


def _guardrail_node(state: AetherGraphState) -> dict:
    """
    Invoke the output guardrail and either promote or hold back the draft answer.

    On approval, the Orquestrador's draft (not the guardrail's own commentary)
    is promoted into "messages" as the final user-facing answer. On rejection,
    nothing is added to "messages" — only the review feedback and retry count
    are recorded, so the run can loop back to the router without polluting the
    user-facing history with an unapproved draft.

    Args:
        state: The current graph state.

    Returns:
        dict: The state update, including "guard_rail_approved" and, on
            rejection, "guard_rail_requested_changes" and an incremented
            "guard_rail_retries".
    """

    message = _build_guardrail_message(state)
    output, next_agent = _invoke_agent("Guardrail de Saída", message)

    approved = output.strip().lower().startswith("aprovado")
    update = {"next_agent": next_agent, "guard_rail_approved": approved}

    if approved:
        draft = (state.get("previous_agents") or {}).get("Orquestrador", "")
        update["messages"] = [{"role": "assistant", "content": draft, "name": "Orquestrador"}]
    else:
        update["guard_rail_requested_changes"] = [output]
        update["guard_rail_retries"] = state["guard_rail_retries"] + 1

    return update


roteador_node = _roteador_node
faq_node = _non_specialist_node_factory("FAQ", terminal=True)
orquestrador_node = _orquestrador_node
guardrail_node = _guardrail_node

analista_inventarios_node = _specialist_node_factory("Análista de inventários")
analista_poluentes_node = _specialist_node_factory("Analista de Poluentes")
analista_gases_verdes_node = _specialist_node_factory("Analista de Gases Verdes")
coordenador_melhoria_node = _specialist_node_factory("Coordenador de Melhoria Contínua", terminal=True)
