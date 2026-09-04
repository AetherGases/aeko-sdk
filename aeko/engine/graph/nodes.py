from time import perf_counter
from typing import Callable

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from aeko.engine._content import text_of
from aeko.engine.graph.state import AetherGraphState, NextAgent
from aeko.engine.runtime import RUNTIME
from aeko.shared import record_agent_call

# How many more times the continuous improvement coordinator is asked to fix an
# answer that came back in the wrong shape, before the run gives up and hands
# the last attempt over as-is. Five calls of the slow model is already a lot for
# one node; whoever reads the answer is what turns exhaustion into an error (see
# `AekoInventoryAnalyzer.analyze`).
PLAN_FORMAT_MAX_RETRIES = 4


def _max_tokens_from(config: RunnableConfig | None) -> int:
    """
    Read the output token cap a run opted into.

    Args:
        config: The run's configuration, optionally carrying a "max_tokens".

    Returns:
        int: The requested cap, or the configured conversational one.
    """

    return (config or {}).get("configurable", {}).get("max_tokens") or RUNTIME.max_tokens


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
    aeko/engine/prompts/) around a single self-contained human turn, never
    around replaying another agent's raw output as chat history. Handing an
    agent a running transcript would eventually end on another agent's "ai"
    turn, which Gemini silently answers with empty content, and also blurs each
    agent's persona with whatever it reads as "said by the user". Building this
    message from `initial_question` plus a structured summary of
    `previous_agents` avoids both problems.

    Also includes the Guardrail's most recent rejection feedback, if any, so
    that whoever is invoked next (typically the Roteador, deciding where to
    retry) can tell whether the gap is a missing specialist analysis or just
    a consolidation/tone fix, instead of guessing blind.

    Args:
        state: The current graph state.

    Returns:
        HumanMessage: The company context and the prior conversation, the
            original question, a summary of any specialist findings gathered so
            far, and the latest guardrail feedback.
    """

    parts = []

    company_context = state.get("company_context") or ""
    if company_context:
        parts.append(f"Contexto da empresa/usuário:\n{company_context}")

    # Replayed whole: whoever owns the conversation is the one that decides how
    # many turns are worth replaying, and renders them (see `SESSION_HISTORY_USAGE`).
    history = state.get("history") or ""
    if history:
        parts.append(f"Histórico da conversa:\n{history}")

    parts.append(state["initial_question"])

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


def _build_format_retry_message(state: AetherGraphState, answer: str,
                                problems: list[str]) -> HumanMessage:
    """
    Build the message that asks an agent to rewrite an ill-formed answer.

    Carries the whole original request again, not just the complaint: the
    agents are invoked with one isolated message and no history of their own
    (see `_build_context_message`), so an agent told only "your format was
    wrong" would have nothing left to rewrite the answer *from*.

    Args:
        state: The current graph state, holding the original request.
        answer: The answer that was refused, verbatim.
        problems: What is wrong with it, phrased for the agent to act on.

    Returns:
        HumanMessage: The correction request.
    """

    parts = [
        _build_context_message(state).content,
        "Sua resposta anterior foi recusada porque não seguiu o formato exigido:\n"
        + "\n".join(f"- {problem}" for problem in problems),
        f"Resposta anterior:\n{answer}",
        "Reescreva o plano inteiro no formato exigido. Não comente esta correção.",
    ]

    return HumanMessage(content="\n\n".join(parts))


def _invoke_agent(agent_name: str, message: HumanMessage,
                  max_tokens: int | None = None) -> tuple[str, NextAgent | None]:
    """
    Invoke a named agent with a single isolated message and parse its routing decision.

    Args:
        agent_name: The name of the agent to invoke, as registered in `RUNTIME.agents`.
        message: The isolated handoff message built for this agent.
        max_tokens: The output token cap this run opted into.

    Returns:
        tuple[str, NextAgent | None]: The agent's raw output text, and the next
            agent to route to (or None if the agent signaled no follow-up).

    Raises:
        ValueError: If the agent's output names a next agent that doesn't exist.
    """

    agents = RUNTIME.agents_for(max_tokens)

    # Recorded here for the same reason the normalization below lives here:
    # this is the one point every agent of every flow is invoked through, so an
    # agent added to the graph later is reported without anyone remembering to.
    # Nothing is written — the request the call belongs to lists it when it
    # ends, and a node knows nothing about which request that is. In a `finally`
    # so an agent that raises is still listed, which is what says where a failed
    # request got to.
    started = perf_counter()

    try:
        # Normalized here and nowhere else in the graph: this is the single
        # point every agent's output enters the run through, so everything
        # downstream — "previous_agents", the "messages" channel, the routing
        # marker below — goes on being the plain text it was written against.
        output = text_of(agents[agent_name].invoke({"messages": [message]})["output"])
    finally:
        record_agent_call(agent_name, perf_counter() - started)

    raw_next = output.split("Next agent: ")[-1].strip() if "Next agent: " in output else ""

    if raw_next in ("", "Nenhum"):
        next_agent = None
    elif raw_next not in agents:
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
    literal answer delivered to the user. No analyst wired into the graph is
    terminal today: the one that ends the report flow is the continuous
    improvement coordinator, which needs a retry loop and so has a node of its
    own (see `_coordenador_melhoria_node`).

    Args:
        agent_name: The key of the agent this node should invoke.
        terminal: Whether this agent's output is the final user-facing answer.

    Returns:
        Callable[[AetherGraphState], dict]: A graph node function.
    """

    def node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
        message = _build_context_message(state)
        output, next_agent = _invoke_agent(agent_name, message, _max_tokens_from(config))

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

    def node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
        message = _build_context_message(state)
        output, next_agent = _invoke_agent(agent_name, message, _max_tokens_from(config))

        update = {"next_agent": next_agent}

        if terminal:
            update["messages"] = [{"role": "assistant", "content": output, "name": agent_name}]

        return update

    return node


def _roteador_node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
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
        config: The run's configuration, carrying the output token cap.

    Returns:
        dict: The state update.
    """

    message = _build_context_message(state)
    output, next_agent = _invoke_agent("Roteador", message, _max_tokens_from(config))

    if next_agent and next_agent["agent"] == "Orquestrador" and not state.get("previous_agents"):
        next_agent = {"agent": "FAQ", "message": output}

    return {"next_agent": next_agent}


def _orquestrador_node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
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
    output, next_agent = _invoke_agent("Orquestrador", message, _max_tokens_from(config))

    return {
        "previous_agents": {"Orquestrador": output},
        "next_agent": next_agent,
    }


def _guardrail_node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
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
    output, next_agent = _invoke_agent("Guardrail de Saída", message, _max_tokens_from(config))

    approved = output.strip().lower().startswith("aprovado")
    update = {"next_agent": next_agent, "guard_rail_approved": approved}

    if approved:
        draft = (state.get("previous_agents") or {}).get("Orquestrador", "")
        update["messages"] = [{"role": "assistant", "content": draft, "name": "Orquestrador"}]
    else:
        update["guard_rail_requested_changes"] = [output]
        update["guard_rail_retries"] = state["guard_rail_retries"] + 1

    return update


def _coordenador_melhoria_node(state: AetherGraphState, config: RunnableConfig | None = None) -> dict:
    """
    Invoke the continuous improvement coordinator, retrying an ill-formed answer.

    Behaves exactly like a terminal specialist node, except that a run may hand
    in a "validate_answer" callable — the inventory flow does, since its answer
    is parsed into an `AekoImprovementPlan` rather than read as prose. Whatever
    that callable complains about is sent straight back to the agent, up to
    `PLAN_FORMAT_MAX_RETRIES` times, so a format slip costs one more call to
    this node instead of the whole analysis.

    Retrying here rather than around the graph is what keeps it affordable: the
    inventory analyst and the pollutant/green gas analysts already ran, their
    findings are in the state, and only the coordinator has to answer again.

    The last attempt is recorded either way. Nothing is raised here — the graph
    has no opinion on what the text is for; the caller that parses it is the one
    that can tell an exhausted retry from a plan (see
    `AekoInventoryAnalyzer.analyze`).

    Args:
        state: The current graph state.
        config: The run's configuration, carrying the output token cap and,
            optionally, the "validate_answer" callable. It takes the agent's raw
            output and returns what is wrong with it, empty when nothing is.

    Returns:
        dict: The state update, recording the last attempt as the answer.
    """

    agent_name = "Coordenador de Melhoria Contínua"
    configurable = (config or {}).get("configurable", {})
    validate = configurable.get("validate_answer")
    max_tokens = _max_tokens_from(config)

    message = _build_context_message(state)

    for _ in range(PLAN_FORMAT_MAX_RETRIES + 1):
        output, next_agent = _invoke_agent(agent_name, message, max_tokens)

        problems = list(validate(output)) if validate else []
        if not problems:
            break

        message = _build_format_retry_message(state, output, problems)

    return {
        "previous_agents": {agent_name: output},
        "pending_agents": [{"agent": agent_name, "is_still_pending": False}],
        "next_agent": next_agent,
        "messages": [{"role": "assistant", "content": output, "name": agent_name}],
    }


roteador_node = _roteador_node
faq_node = _non_specialist_node_factory("FAQ", terminal=True)
orquestrador_node = _orquestrador_node
guardrail_node = _guardrail_node

analista_inventarios_node = _specialist_node_factory("Análista de inventários")
analista_poluentes_node = _specialist_node_factory("Analista de Poluentes")
analista_gases_verdes_node = _specialist_node_factory("Analista de Gases Verdes")
coordenador_melhoria_node = _coordenador_melhoria_node
