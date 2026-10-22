"""
Lock FR-P01–P06 for INVENTORY_PAYLOAD_INSTRUCTIONS without calling analyze() e2e.
"""

import json

import re


from langchain_core.messages import HumanMessage


from aeko.engine.graph import nodes

from aeko.engine.graph.state import create_initial_state

from aeko.engine.prompts.continuous_improvement_coordinator import (
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
    INVENTORY_PAYLOAD_INSTRUCTIONS,
    PLAN_SECTIONS,
)


def _inventory_fence_json(text: str) -> dict:
    """Parse the single ```inventory example embedded in the instruction string."""

    match = re.search(r"```inventory\n(\{.*?\})\n```", text, re.DOTALL)

    assert match, "instruction must contain one ```inventory fenced JSON example"

    return json.loads(match.group(1))


def test_inventory_instructions_require_plan_sections_then_one_inventory_fence():
    """FR-P01: three markdown sections first, then one inventory JSON block (not plan as JSON)."""

    lowered = INVENTORY_PAYLOAD_INSTRUCTIONS.lower()

    assert "três seções" in lowered or "tres secoes" in lowered.replace("ê", "e")

    assert INVENTORY_PAYLOAD_INSTRUCTIONS.count("```inventory") == 1

    assert "plano" in lowered

    assert "json" in lowered

    example = _inventory_fence_json(INVENTORY_PAYLOAD_INSTRUCTIONS)

    assert set(example) >= {"description", "start_period", "end_period", "emissions"}

    assert isinstance(example["emissions"], list)


def test_inventory_instructions_include_minimal_emission_example_and_reduction_rule():
    """FR-P02: root keys, one emission item, and reduction nulls extra fields."""

    example = _inventory_fence_json(INVENTORY_PAYLOAD_INSTRUCTIONS)

    assert len(example["emissions"]) >= 1

    emission = example["emissions"][0]

    assert "quantity_co2e" in emission

    assert "is_reduction" in emission

    assert "description" in INVENTORY_PAYLOAD_INSTRUCTIONS

    assert "emissions" in INVENTORY_PAYLOAD_INSTRUCTIONS

    assert "is_reduction" in INVENTORY_PAYLOAD_INSTRUCTIONS

    assert "null" in INVENTORY_PAYLOAD_INSTRUCTIONS.lower()

    assert re.search(
        r"is_reduction.*(demais|extras).*null",
        INVENTORY_PAYLOAD_INSTRUCTIONS,
        re.IGNORECASE | re.DOTALL,
    )


def test_inventory_instructions_catalog_ids_null_and_upstream_from_classification():
    """FR-P03: catalog ids only, null when missing, is_upstream follows classification."""

    text = INVENTORY_PAYLOAD_INSTRUCTIONS

    assert "catálogo" in text.lower() or "catalogo" in text.lower()

    assert "null" in text.lower()

    assert "is_upstream" in text

    assert "UPSTREAM" in text

    assert "DOWNSTREAM" in text

    assert "classifica" in text.lower()


def test_inventory_instructions_do_not_replace_sections_with_json_or_extra_fences():
    """FR-P04: keep section plan; only the inventory fence may use code blocks."""

    text = INVENTORY_PAYLOAD_INSTRUCTIONS

    assert text.count("```") == 2

    assert "única exceção" in text or "unica excecao" in text

    assert not re.search(
        r"responda\s+(somente|apenas)\s+.*json",
        text,
        re.IGNORECASE,
    )


def test_coordinator_shots_never_include_inventory_fence():
    """FR-P05: chat shots stay three-section markdown only."""

    for shot in CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.shots:
        assert "```inventory" not in shot["resposta"]

        assert "```inventory" not in shot["pergunta"]

        for label in PLAN_SECTIONS.values():
            assert f"## {label}" in shot["resposta"]


def test_chat_handoff_skips_inventory_instructions_without_catalog_context():
    """FR-P06: catalog_context absent → human message has no inventory block instruction."""

    state = create_initial_state("Pergunta de chat")

    message = HumanMessage(content="Pergunta de chat")

    result = nodes._with_inventory_payload_instructions(message, state)

    assert result.content == message.content

    assert "```inventory" not in result.content

    assert INVENTORY_PAYLOAD_INSTRUCTIONS not in result.content


def test_inventory_handoff_appends_inventory_instructions_with_catalog_context():
    """FR-P06: catalog_context present → human message includes the full instruction."""

    state = create_initial_state("Markdown do inventario")

    state["catalog_context"] = "Gases: CO2 id=1"

    message = HumanMessage(content="Markdown do inventario")

    result = nodes._with_inventory_payload_instructions(message, state)

    assert result.content.startswith("Markdown do inventario\n\n")

    assert INVENTORY_PAYLOAD_INSTRUCTIONS in result.content

    assert "```inventory" in result.content

    assert "description" in result.content

    assert "emissions" in result.content


def test_coordinator_spec_module_constants_are_wired_for_coverage():
    """Import-time wiring: plan section labels appear in the coordinator spec."""

    labels = list(PLAN_SECTIONS.values())

    scope = CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.scope

    for label in labels:
        assert label in scope

    format_task = CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.tasks[-1]

    for label in labels:
        assert f"## {label}" in format_task

    assert CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.agent

    assert CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.persona

    assert len(CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.shots) >= 1
