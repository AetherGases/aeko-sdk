import pytest
from langgraph.graph import END

from aeko.engine.graph import builder


def _state(**overrides):
    base = {
        "messages": [],
        "next_agent": None,
        "output": "",
        "guard_rail_retries": 0,
        "guard_rail_requested_changes": [],
        "guard_rail_approved": False,
        "response_check_retries": 0,
        "response_check_requested_changes": [],
        "response_check_approved": False,
    }
    base.update(overrides)
    return base


def _config(entry_point=None):
    if entry_point is None:
        return {"configurable": {}}
    return {"configurable": {"entry_point": entry_point}}


def test_entry_router_defaults_to_roteador():
    assert builder._entry_router(_state(), _config()) == "Roteador"


def test_entry_router_honors_inventory_entry_point():
    assert builder._entry_router(_state(), _config("Análista de inventários")) == "Análista de inventários"


def test_route_by_next_agent_uses_state_next_agent():
    route = builder._route_by_next_agent("FAQ")
    state = _state(next_agent={"agent": "Orquestrador", "message": "..."})

    assert route(state) == "Orquestrador"


def test_route_by_next_agent_falls_back_to_default_when_none():
    route = builder._route_by_next_agent("FAQ")

    assert route(_state(next_agent=None)) == "FAQ"


def test_route_from_analyst_passes_through_non_orquestrador_target():
    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent={"agent": "Analista de Gases Verdes", "message": "..."})

    assert route(state, _config("Análista de inventários")) == "Analista de Gases Verdes"


def test_route_from_analyst_keeps_orquestrador_outside_inventory_flow():
    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent={"agent": "Orquestrador", "message": "..."})

    assert route(state, _config("Roteador")) == "Orquestrador"
    assert route(state, _config()) == "Orquestrador"


def test_route_from_analyst_forces_coordinator_in_inventory_flow():
    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent={"agent": "Orquestrador", "message": "..."})

    assert route(state, _config("Análista de inventários")) == "Coordenador de Melhoria Contínua"


def test_route_from_analyst_default_target_also_gets_overridden():
    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent=None)

    assert route(state, _config("Análista de inventários")) == "Coordenador de Melhoria Contínua"


@pytest.mark.parametrize(
    "conversational",
    ["Orquestrador", "Roteador", "FAQ", "Guardrail de Saída", "Verificador de Resposta"],
)
def test_no_conversational_agent_is_reachable_from_the_report_flow(conversational):
    """
    The report flow may only ever move between its own agents.

    Naming anything else means the analysis is over, not that the run should
    cross into the conversational flow: that flow ends at the guardrail, which
    the report flow deliberately never passes through.
    """

    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent={"agent": conversational, "message": "..."})

    assert route(state, _config("Análista de inventários")) == "Coordenador de Melhoria Contínua"


@pytest.mark.parametrize("analyst", ["Analista de Poluentes", "Analista de Gases Verdes"])
def test_the_report_flow_still_moves_between_its_own_analysts(analyst):
    route = builder._route_from_analyst("Analista de Poluentes")
    state = _state(next_agent={"agent": analyst, "message": "..."})

    assert route(state, _config("Análista de inventários")) == analyst


@pytest.mark.parametrize("target", ["FAQ", "Roteador", "Orquestrador"])
def test_the_chat_flow_is_left_alone(target):
    route = builder._route_from_analyst("Orquestrador")
    state = _state(next_agent={"agent": target, "message": "..."})

    assert route(state, _config("Roteador")) == target
    assert route(state, _config()) == target


@pytest.mark.parametrize("analyst", ["Analista de Poluentes", "Analista de Gases Verdes"])
def test_an_analyst_naming_itself_is_read_as_having_finished(analyst):
    """
    Naming yourself is a non-answer, so it is read as naming nothing at all.

    No prompt offers an agent its own name, and no edge carries one either, so
    left alone this is not a wrong hop: it is a target the path map does not
    have, which kills the run.
    """

    route = builder._route_from_analyst("Orquestrador", current=analyst)
    state = _state(next_agent={"agent": analyst, "message": "..."})

    assert route(state, _config("Análista de inventários")) == "Coordenador de Melhoria Contínua"


def test_an_analyst_naming_itself_in_the_chat_flow_takes_the_default():
    route = builder._route_from_analyst("Orquestrador", current="Analista de Poluentes")
    state = _state(next_agent={"agent": "Analista de Poluentes", "message": "..."})

    assert route(state, _config("Roteador")) == "Orquestrador"


def test_the_inventory_analyst_edge_accepts_the_coordinator():
    graph = builder.build_graph()

    ends = set()
    for branch in graph.branches["Análista de inventários"].values():
        ends.update(branch.ends or {})

    assert "Coordenador de Melhoria Contínua" in ends, (
        "sem isso o desvio existe na funcao de rota mas o alvo nao esta no path map"
    )


def test_route_from_guardrail_approved_goes_to_the_response_checker():
    """
    The guardrail no longer ends the conversational flow: the checker does.

    An approved draft has only cleared the first of the two reviews a chat
    answer goes through, so it moves on instead of being delivered.
    """

    state = _state(guard_rail_approved=True)

    assert builder._route_from_guardrail(state) == builder.RESPONSE_CHECKER


def test_route_from_guardrail_rejected_goes_back_to_roteador():
    state = _state(guard_rail_approved=False, guard_rail_retries=1)

    assert builder._route_from_guardrail(state) == "Roteador"


def test_route_from_guardrail_stops_at_the_second_rejection():
    state = _state(
        guard_rail_approved=False,
        guard_rail_retries=builder.GUARD_RAIL_MAX_RETRIES,
    )

    assert builder._route_from_guardrail(state) == END


def test_both_reviewers_get_two_rejections_before_the_run_gives_up():
    assert builder.GUARD_RAIL_MAX_RETRIES == 2
    assert builder.RESPONSE_CHECK_MAX_RETRIES == 2


def test_route_from_response_check_approved_ends_the_graph():
    state = _state(response_check_approved=True)

    assert builder._route_from_response_check(state) == END


def test_route_from_response_check_rejected_goes_back_to_roteador():
    state = _state(response_check_approved=False, response_check_retries=1)

    assert builder._route_from_response_check(state) == "Roteador"


def test_route_from_response_check_stops_at_the_second_rejection():
    state = _state(
        response_check_approved=False,
        response_check_retries=builder.RESPONSE_CHECK_MAX_RETRIES,
    )

    assert builder._route_from_response_check(state) == END


def test_the_response_checker_is_a_node_of_the_graph():
    graph = builder.build_graph()

    assert builder.RESPONSE_CHECKER in graph.nodes


def test_the_guardrail_edge_carries_the_response_checker():
    graph = builder.build_graph()

    ends = set()
    for branch in graph.branches["Guardrail de Saída"].values():
        ends.update(branch.ends or {})

    assert builder.RESPONSE_CHECKER in ends, (
        "sem isso o desvio existe na funcao de rota mas o alvo nao esta no path map"
    )


def test_the_response_checker_only_ends_or_retries():
    graph = builder.build_graph()

    ends = set()
    for branch in graph.branches[builder.RESPONSE_CHECKER].values():
        ends.update(branch.ends or {})

    assert ends == {"Roteador", END}


def test_the_report_flow_never_reaches_the_response_checker():
    """
    The checker reviews chat answers, and the report flow has none.

    Its answer is parsed into an `AekoImprovementPlan` by whoever called
    `analyze()`, so a reviewer sitting between the coordinator and the caller
    would be judging a document, not a message to a user.
    """

    graph = builder.build_graph()

    reachable = {target for _, target in graph._all_edges}
    for branches in graph.branches.values():
        for branch in branches.values():
            reachable.update(branch.ends or {})

    assert (builder.IMPROVEMENT_COORDINATOR, builder.RESPONSE_CHECKER) not in graph._all_edges
    assert builder.IMPROVEMENT_COORDINATOR not in graph.branches


def test_orquestrador_only_goes_to_guardrail():
    graph = builder.build_graph()

    assert ("Orquestrador", "Guardrail de Saída") in graph._all_edges
    assert not any(source == "Orquestrador" and target != "Guardrail de Saída" for source, target in graph._all_edges)


def test_build_graph_compiles():
    compiled = builder.build_graph().compile()

    assert compiled is not None


def test_get_app_returns_an_invocable_graph():
    app = builder.get_app()

    assert app is not None
    assert hasattr(app, "invoke")

