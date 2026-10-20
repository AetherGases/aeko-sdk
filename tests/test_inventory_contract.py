"""
Contract tests for the structured inventory returned by `analyze()`.

The plan in sections stays a Mongo `improvement_plan` document. The sibling
`inventory` object is what ms-aeko persists to Postgres, using only catalog
ids supplied on the same call.
"""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from aeko import (
    Aeko,
    AekoExtractedInventory,
    AekoImprovementPlan,
    AekoInventoryAnalyzer,
)
from aeko.config.dto import AekoInventoryCatalogs
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.config.inventory_fallback import INVENTORY_SCHEMA_MAX_RETRIES
from aeko.engine.prompts import PLAN_SECTIONS
from tests.conftest import catalog_kwargs, with_extracted_inventory
from tests.test_config import (
    API_KEY,
    INVENTORY_ID,
    INVENTORY_MD,
    PLAN_FIELDS,
    REQUEST_ID,
    _coordinator_calls,
    as_sections,
)

VALID_EMISSION = {
    "quantity_co2e": 1200.0,
    "methodology_description": None,
    "supplier_data_percentage": None,
    "gas": 1,
    "scope": 1,
    "category": 1,
    "is_upstream": None,
    "is_reduction": False,
}

VALID_REDUCTION = {
    "quantity_co2e": 40.0,
    "methodology_description": None,
    "supplier_data_percentage": None,
    "gas": None,
    "scope": None,
    "category": 2,
    "is_upstream": None,
    "is_reduction": True,
}

VALID_INVENTORY = {
    "description": "Inventario 2023",
    "start_period": "2023-01-01",
    "end_period": "2023-12-31",
    "emissions": [VALID_EMISSION, VALID_REDUCTION],
}


@pytest.fixture
def configured():
    Aeko.config(API_KEY)


def _flow(payload: dict | None = VALID_INVENTORY) -> dict[str, str]:
    """Inventory-flow script whose coordinator emits plan sections and an inventory."""

    return {
        "Análista de inventários": "Escopo 1 = 1.200 tCO2e.\nNext agent: Analista de Poluentes",
        "Analista de Poluentes": "Combustao dominante.\nNext agent: Orquestrador",
        "Coordenador de Melhoria Contínua": with_extracted_inventory(
            as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum",
            payload,
        ),
    }


def _analyze(configured, use_fake_llm, payload: dict | None = VALID_INVENTORY, **catalogs):
    use_fake_llm(_flow(payload))
    return AekoInventoryAnalyzer().analyze(
        INVENTORY_MD,
        id_external_inventory=INVENTORY_ID,
        id_request=REQUEST_ID,
        **catalog_kwargs(**catalogs),
    )


COORDINATOR = "Coordenador de Melhoria Contínua"

COERCE_INVENTORY = {
    "description": "Inventario 2023",
    "start_period": "2023-01-01",
    "end_period": "2023-12-31",
    "emissions": [
        {
            "quantity_co2e": 1200.0,
            "gas": "1",
            "scope": 1,
            "category": 2,
            "is_upstream": False,
            "is_reduction": False,
        }
    ],
}


def _raise_schema_unavailable(*_args, **_kwargs):
    raise RuntimeError("schema fill is stubbed in this test")


def _patch_fill(monkeypatch, fill):
    monkeypatch.setattr("aeko.config.inventory._fill_inventory_from_schema", fill)
    return fill


def _sections_only_flow() -> dict[str, str]:
    return {
        "Análista de inventários": "Escopo 1 = 1.200 tCO2e.\nNext agent: Analista de Poluentes",
        "Analista de Poluentes": "Combustao dominante.\nNext agent: Orquestrador",
        COORDINATOR: as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum",
    }


def _malformed_fence_flow() -> dict[str, str]:
    return {
        "Análista de inventários": "Escopo 1.\nNext agent: Analista de Poluentes",
        "Analista de Poluentes": "Combustao.\nNext agent: Orquestrador",
        COORDINATOR: (
            as_sections(PLAN_FIELDS)
            + "\n\n```inventory\n{not json}\n```\nNext agent: Nenhum"
        ),
    }


def _run_analyze(**catalogs):
    return AekoInventoryAnalyzer().analyze(
        INVENTORY_MD,
        id_external_inventory=INVENTORY_ID,
        id_request=REQUEST_ID,
        **catalog_kwargs(**catalogs),
    )


def _coordinator_used_agents(metrics) -> int:
    return sum(1 for agent in metrics.used_agents if agent.name == COORDINATOR)


def test_analyze_requires_the_three_catalogs_as_keyword_only_arguments(configured, use_fake_llm):
    use_fake_llm(_flow())

    with pytest.raises(TypeError):
        AekoInventoryAnalyzer().analyze(
            INVENTORY_MD,
            id_external_inventory=INVENTORY_ID,
            id_request=REQUEST_ID,
        )


def test_invalid_catalogs_are_rejected_before_the_model_runs(configured, use_fake_llm):
    llm = use_fake_llm(_flow())

    with pytest.raises(ValidationError):
        AekoInventoryAnalyzer().analyze(
            INVENTORY_MD,
            id_external_inventory=INVENTORY_ID,
            id_request=REQUEST_ID,
            **catalog_kwargs(gases=[{"id": 1, "name": "CO2"}, {"id": 1, "name": "CH4"}]),
        )

    assert llm.calls == []


def test_analyze_returns_plan_inventory_and_metrics(configured, use_fake_llm):
    response = _analyze(configured, use_fake_llm)

    assert isinstance(response.plan, AekoImprovementPlan)
    assert isinstance(response.inventory, AekoExtractedInventory)
    assert response.plan.defined_problem == PLAN_FIELDS["defined_problem"]
    assert response.inventory.description == "Inventario 2023"
    assert response.inventory.start_period == "2023-01-01"
    assert response.inventory.end_period == "2023-12-31"
    assert [row.is_reduction for row in response.inventory.emissions] == [False, True]
    assert response.inventory.emissions[0].gas == 1
    assert response.inventory.emissions[1].category == 2
    assert response.aeko_metrics.id_request == REQUEST_ID


def test_the_plan_contract_is_unchanged_when_inventory_is_present(configured, use_fake_llm):
    plan = _analyze(configured, use_fake_llm).plan

    assert set(plan.model_dump(by_alias=True)) == {
        "_id", "id_external_inventory", "defined_problem", "method", "reasoning",
        "updated_at",
    }
    assert plan.id_external_inventory == INVENTORY_ID
    assert plan.id is None


def test_a_valid_plan_without_inventory_is_refused(configured, use_fake_llm, monkeypatch):
    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    use_fake_llm(_sections_only_flow())

    with pytest.raises(MalformedAgentOutputError) as raised:
        _run_analyze()

    assert fill.call_count == INVENTORY_SCHEMA_MAX_RETRIES
    assert raised.value.aeko_metrics is not None
    assert raised.value.aeko_metrics.id_request == REQUEST_ID


def test_an_inventory_referencing_an_unknown_id_is_refused(
    configured, use_fake_llm, monkeypatch
):
    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    use_fake_llm(_flow({
        **VALID_INVENTORY,
        "emissions": [{**VALID_EMISSION, "gas": 99}],
    }))

    with pytest.raises(MalformedAgentOutputError) as raised:
        _run_analyze()

    fill.assert_not_called()
    assert raised.value.aeko_metrics is not None


def test_malformed_inventory_json_is_refused(configured, use_fake_llm, monkeypatch):
    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    use_fake_llm(_malformed_fence_flow())

    with pytest.raises(MalformedAgentOutputError) as raised:
        _run_analyze()

    assert fill.call_count == INVENTORY_SCHEMA_MAX_RETRIES
    assert raised.value.aeko_metrics is not None


def test_catalogs_reach_inventory_flow_agents(configured, use_fake_llm):
    llm = use_fake_llm(_flow())

    AekoInventoryAnalyzer().analyze(
        INVENTORY_MD,
        id_external_inventory=INVENTORY_ID,
        id_request=REQUEST_ID,
        **catalog_kwargs(),
    )

    analyst_prompt = llm.prompt_for("Análista de inventários")
    coordinator_prompt = llm.prompt_for("Coordenador de Melhoria Contínua")

    assert "CO2" in analyst_prompt
    assert "Escopo 1" in analyst_prompt
    assert "Combustao estacionaria" in analyst_prompt
    assert "UPSTREAM" in analyst_prompt
    assert "1:" in analyst_prompt or "1: CO2" in analyst_prompt
    assert "```inventory" in coordinator_prompt
    assert PLAN_SECTIONS["defined_problem"] in as_sections(PLAN_FIELDS)


def test_chat_prompts_do_not_receive_inventory_catalogs(
    configured, use_fake_llm, monkeypatch
):
    from aeko import AekoMessenger
    from tests.test_config import CHAT_FLOW, make_session, make_user

    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    llm = use_fake_llm(CHAT_FLOW)
    AekoMessenger(make_user()).send_message(
        "O que e hidrogenio verde?", make_session(), id_request=REQUEST_ID
    )

    fill.assert_not_called()
    faq_prompt = llm.prompt_for("FAQ")
    assert "Catálogos auxiliares" not in faq_prompt
    assert "```inventory" not in faq_prompt
    assert "Combustao estacionaria" not in faq_prompt


def test_valid_fence_uses_one_graph_coordinator_and_skips_schema_fill(
    configured, use_fake_llm, monkeypatch
):
    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    llm = use_fake_llm(_flow())

    response = _run_analyze()

    fill.assert_not_called()
    assert _coordinator_calls(llm) == 1
    assert _coordinator_used_agents(response.aeko_metrics) == 1
    assert response.inventory.description == "Inventario 2023"


def test_coercible_fence_succeeds_without_schema_fill(
    configured, use_fake_llm, monkeypatch
):
    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    use_fake_llm(_flow(COERCE_INVENTORY))

    response = _run_analyze()

    fill.assert_not_called()
    emission = response.inventory.emissions[0]
    assert emission.gas == 1
    assert emission.is_upstream is True
    assert emission.category == 2


def test_unsalvageable_json_succeeds_on_first_schema_fill(
    configured, use_fake_llm, monkeypatch
):
    fill = _patch_fill(monkeypatch, Mock(return_value=VALID_INVENTORY))
    llm = use_fake_llm(_sections_only_flow())

    response = _run_analyze()

    assert fill.call_count == 1
    markdown, catalogs = fill.call_args.args
    assert markdown == INVENTORY_MD
    assert isinstance(catalogs, AekoInventoryCatalogs)
    assert _coordinator_calls(llm) == 1
    assert response.plan.defined_problem == PLAN_FIELDS["defined_problem"]
    assert response.plan.method == PLAN_FIELDS["method"]
    assert response.plan.reasoning == PLAN_FIELDS["reasoning"]
    assert response.inventory.description == "Inventario 2023"
    assert [row.gas for row in response.inventory.emissions] == [1, None]


def test_schema_fill_retries_after_first_raise_then_succeeds(
    configured, use_fake_llm, monkeypatch
):
    fill = _patch_fill(
        monkeypatch,
        Mock(side_effect=[RuntimeError("first schema fill failed"), VALID_INVENTORY]),
    )
    use_fake_llm(_sections_only_flow())

    response = _run_analyze()

    assert fill.call_count == INVENTORY_SCHEMA_MAX_RETRIES
    assert response.inventory.start_period == "2023-01-01"
    assert response.plan.defined_problem == PLAN_FIELDS["defined_problem"]


def test_schema_fill_unknown_gas_id_does_not_retry(
    configured, use_fake_llm, monkeypatch
):
    invalid = {
        **VALID_INVENTORY,
        "emissions": [{**VALID_EMISSION, "gas": 99}],
    }
    fill = _patch_fill(monkeypatch, Mock(return_value=invalid))
    use_fake_llm(_sections_only_flow())

    with pytest.raises(MalformedAgentOutputError) as raised:
        _run_analyze()

    assert fill.call_count == 1
    assert raised.value.aeko_metrics is not None


def test_send_message_does_not_call_schema_fill(configured, use_fake_llm, monkeypatch):
    from aeko import AekoMessenger
    from tests.test_config import (
        ANALYSIS_FLOW,
        CONSOLIDATED,
        make_session,
        make_user,
    )

    fill = _patch_fill(monkeypatch, Mock(side_effect=_raise_schema_unavailable))
    llm = use_fake_llm({
        **ANALYSIS_FLOW,
        "Roteador": "Plano de melhoria.\nNext agent: Coordenador de Melhoria Contínua",
        COORDINATOR: "Plano em prosa, sem secoes.\nNext agent: Nenhum",
    })

    response = AekoMessenger(make_user()).send_message(
        "como melhoro o forno?", make_session(), id_request=REQUEST_ID
    )

    fill.assert_not_called()
    assert _coordinator_calls(llm) == 1
    assert response.message.output == CONSOLIDATED
