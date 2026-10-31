"""
Tests for the `recommended_gases` list of the improvement plan.

Two seams: the DTO of one recommended gas (bounds and rounding), and
`AekoInventoryAnalyzer.analyze()` fed a scripted coordinator answer, which is
where a missing, malformed or out-of-bounds list becomes a correction retry.
"""

import json

import pytest

from pydantic import ValidationError

from aeko import Aeko, AekoImprovementPlan, AekoInventoryAnalyzer, AekoRecommendedGas

from aeko.config.exceptions import MalformedAgentOutputError

from aeko.engine.graph.constants import PLAN_FORMAT_MAX_RETRIES

from aeko.engine.prompts import PLAN_SECTIONS

from aeko.shared.text import parse_sections

from aeko.engine.prompts.continuous_improvement_coordinator import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
)

from tests.conftest import catalog_kwargs, with_extracted_inventory

from tests.test_config import (
    API_KEY,
    INVENTORY_FLOW,
    INVENTORY_ID,
    INVENTORY_MD,
    PLAN_FIELDS,
    REQUEST_ID,
    _coordinator_calls,
    as_sections,
)


COORDINATOR = "Coordenador de Melhoria Contínua"

GAS = {"scope": 1, "emission": 120.5, "reduction_percentage": 35.25, "name": "Biometano"}

OTHER_GAS = {
    "scope": 2,
    "emission": 80.0,
    "reduction_percentage": 10.0,
    "name": "Hidrogenio verde",
}


@pytest.fixture
def configured():
    """Configured."""
    Aeko.config(API_KEY)


def _answer(body: str | None) -> str:
    """A coordinator answer whose gases section carries `body` (None: no section)."""

    fields = {k: v for k, v in PLAN_FIELDS.items() if k != "recommended_gases"}

    if body is not None:
        fields["recommended_gases"] = body

    return with_extracted_inventory(as_sections(fields) + "\nNext agent: Nenhum")


def _analyze(use_fake_llm, *answers: str):
    """Run `analyze()` with the coordinator answering `answers` in order."""

    llm = use_fake_llm({**INVENTORY_FLOW, COORDINATOR: list(answers)})

    plan = (
        AekoInventoryAnalyzer()
        .analyze(
            INVENTORY_MD,
            id_external_inventory=INVENTORY_ID,
            id_request=REQUEST_ID,
            **catalog_kwargs(),
        )
        .plan
    )

    return plan, llm


def test_a_recommended_gas_rounds_both_floats_to_two_decimals():
    """A recommended gas rounds both floats to two decimals."""
    gas = AekoRecommendedGas(
        scope=3, emission=1.23456, reduction_percentage=99.999, name="Biometano"
    )

    assert gas.emission == 1.23

    assert gas.reduction_percentage == 100.0


@pytest.mark.parametrize(
    "override",
    [
        {"scope": 0},
        {"scope": 4},
        {"emission": -0.01},
        {"reduction_percentage": -1},
        {"reduction_percentage": 100.01},
        {"name": ""},
    ],
)
def test_a_recommended_gas_out_of_bounds_is_rejected(override):
    """A recommended gas out of bounds is rejected."""
    with pytest.raises(ValidationError):
        AekoRecommendedGas(**{**GAS, **override})


def test_a_plan_built_by_hand_has_no_recommended_gases():
    """A plan built by hand has no recommended gases."""
    plan = AekoImprovementPlan(
        id_external_inventory=1, defined_problem="p", method="m", reasoning="r"
    )

    assert plan.recommended_gases == []


def test_a_plan_carries_at_most_two_recommended_gases():
    """A plan carries at most two recommended gases."""
    with pytest.raises(ValidationError):
        AekoImprovementPlan(
            id_external_inventory=1,
            defined_problem="p",
            method="m",
            reasoning="r",
            recommended_gases=[GAS, GAS, GAS],
        )


@pytest.mark.parametrize("items", [[GAS], [GAS, OTHER_GAS]])
def test_analyze_returns_the_recommended_gases_it_was_given(
    configured, use_fake_llm, items
):
    """Analyze returns the recommended gases it was given."""
    plan, llm = _analyze(use_fake_llm, _answer(json.dumps(items)))

    assert plan.recommended_gases == [AekoRecommendedGas(**item) for item in items]

    assert _coordinator_calls(llm) == 1


def test_analyze_rounds_the_recommended_gases_to_two_decimals(
    configured, use_fake_llm
):
    """Analyze rounds the recommended gases to two decimals."""
    item = {**GAS, "emission": 120.456, "reduction_percentage": 35.254}

    plan, _ = _analyze(use_fake_llm, _answer(json.dumps([item])))

    assert plan.recommended_gases[0].emission == 120.46

    assert plan.recommended_gases[0].reduction_percentage == 35.25


def test_an_empty_list_is_a_valid_answer_and_never_retried(configured, use_fake_llm):
    """An empty list is a valid answer and never retried."""
    plan, llm = _analyze(use_fake_llm, _answer("[]"))

    assert plan.recommended_gases == []

    assert _coordinator_calls(llm) == 1


INVALID_BODIES = [
    pytest.param(None, id="missing-section"),
    pytest.param("[{not json", id="invalid-json"),
    pytest.param(json.dumps([GAS, GAS, GAS]), id="three-items"),
    pytest.param(json.dumps([{**GAS, "scope": 4}]), id="scope-4"),
    pytest.param(json.dumps([{**GAS, "emission": -1}]), id="negative-emission"),
    pytest.param(
        json.dumps([{**GAS, "reduction_percentage": 101}]), id="reduction-above-100"
    ),
    pytest.param(json.dumps([{**GAS, "name": ""}]), id="empty-name"),
]


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_an_invalid_list_is_sent_back_naming_the_section(
    configured, use_fake_llm, body
):
    """An invalid list is sent back naming the section."""
    plan, llm = _analyze(use_fake_llm, _answer(body), _answer(json.dumps([GAS])))

    assert plan.recommended_gases == [AekoRecommendedGas(**GAS)]

    assert _coordinator_calls(llm) == 2

    assert f'"## {PLAN_SECTIONS["recommended_gases"]}"' in llm.prompt_for(COORDINATOR)


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_a_list_never_fixed_is_refused_after_every_retry(
    configured, use_fake_llm, body
):
    """A list never fixed is refused after every retry."""
    llm = use_fake_llm({**INVENTORY_FLOW, COORDINATOR: _answer(body)})

    with pytest.raises(MalformedAgentOutputError):
        AekoInventoryAnalyzer().analyze(
            INVENTORY_MD,
            id_external_inventory=INVENTORY_ID,
            id_request=REQUEST_ID,
            **catalog_kwargs(),
        )

    assert _coordinator_calls(llm) == PLAN_FORMAT_MAX_RETRIES + 1


def test_the_coordinator_is_told_an_empty_list_is_correct():
    """The coordinator is told an empty list is correct."""
    spec = CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC

    prompt = " ".join([spec.scope, *spec.tasks])

    assert "lista vazia" in prompt.lower()


def test_every_coordinator_shot_carries_a_valid_recommended_gases_section():
    """Every coordinator shot carries a valid recommended gases section."""
    sizes = []

    for shot in CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.shots:
        body = parse_sections(shot["resposta"], PLAN_SECTIONS)["recommended_gases"]

        sizes.append(len([AekoRecommendedGas(**item) for item in json.loads(body)]))

    assert [size > 0 for size in sizes] == [False, True, False, True, True, False]

    assert max(sizes) <= 2
