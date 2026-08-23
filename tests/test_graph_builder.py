from langgraph.graph import END

from aeko.engine.graph import builder


def _state(**overrides):
    base = {
        "messages": [],
        "next_agent": None,
        "guard_rail_retries": 0,
        "guard_rail_requested_changes": [],
        "guard_rail_approved": False,
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


def test_route_from_guardrail_approved_ends_the_graph():
    state = _state(guard_rail_approved=True)

    assert builder._route_from_guardrail(state) == END


def test_route_from_guardrail_rejected_goes_back_to_roteador():
    state = _state(guard_rail_approved=False, guard_rail_retries=0)

    assert builder._route_from_guardrail(state) == "Roteador"


def test_route_from_guardrail_rejected_past_retry_cap_ends_anyway():
    state = _state(
        guard_rail_approved=False,
        guard_rail_retries=builder.GUARD_RAIL_MAX_RETRIES + 1,
    )

    assert builder._route_from_guardrail(state) == END


def test_orquestrador_only_goes_to_guardrail():
    graph = builder.build_graph()

    assert ("Orquestrador", "Guardrail de Saída") in graph._all_edges
    assert not any(source == "Orquestrador" and target != "Guardrail de Saída" for source, target in graph._all_edges)


def test_build_graph_compiles():
    compiled = builder.build_graph().compile()

    assert compiled is not None


def test_aether_app_is_precompiled():
    assert builder.AETHER_APP is not None
    assert hasattr(builder.AETHER_APP, "invoke")
