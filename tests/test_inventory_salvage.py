"""Unit tests for deterministic inventory JSON salvage (FR-C01–C04, FR-C09)."""

import json


import pytest


from aeko.config.inventory import _to_improvement_plan, salvage_inventory_payload

from aeko.engine.prompts import PLAN_SECTIONS

from tests.conftest import extracted_inventory_block, with_extracted_inventory

from tests.test_config import PLAN_FIELDS, as_sections


MINIMAL_INVENTORY = {
    "description": "Inventario 2023",
    "start_period": None,
    "end_period": None,
    "emissions": [],
}


def test_fr_c01_valid_fence_with_three_sections_returns_parsed_object():
    """Fr c01 valid fence with three sections returns parsed object."""
    answer = with_extracted_inventory(
        as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum", MINIMAL_INVENTORY
    )

    assert salvage_inventory_payload(answer) == MINIMAL_INVENTORY


def test_fr_c02_json_after_last_section_without_fence():
    """Fr c02 json after last section without fence."""
    payload = {"description": "Pos secoes", "emissions": []}

    answer = (
        as_sections(PLAN_FIELDS) + "\n\n" + json.dumps(payload) + "\nNext agent: Nenhum"
    )

    assert salvage_inventory_payload(answer) == payload

    plan = _to_improvement_plan(answer, id_external_inventory=1)

    assert plan.reasoning == PLAN_FIELDS["reasoning"]

    assert json.dumps(payload) not in plan.reasoning


def test_fr_c02_json_only_inside_method_section_returns_none():
    """Fr c02 json only inside method section returns none."""
    sneaky = '{"description": "escondido", "emissions": []}'

    answer = as_sections(
        {
            **PLAN_FIELDS,
            "method": f"Veja isto: {sneaky}",
        }
    )

    assert salvage_inventory_payload(answer) is None

    plan = _to_improvement_plan(answer, id_external_inventory=1)

    assert sneaky in plan.method


def test_fr_c03_trailing_comma_in_fence_repairs_and_parses():
    """Fr c03 trailing comma in fence repairs and parses."""
    block = '```inventory\n{"description": "x", "emissions": [],}\n```'

    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) == {
        "description": "x",
        "emissions": [],
    }


def test_fr_c03_python_literals_in_fence_repairs_and_parses():
    """Fr c03 python literals in fence repairs and parses."""
    block = (
        "```inventory\n"
        '{"description": None, "emissions": [], "flag": True, "off": False}\n'
        "```"
    )

    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) == {
        "description": None,
        "emissions": [],
        "flag": True,
        "off": False,
    }


def test_fr_c03_repair_preserves_literals_and_commas_inside_strings():
    """Fr c03 repair preserves literals and commas inside strings."""
    note = "Set True here, None of False"

    block = (
        "```inventory\n"
        f'{{"description": "{note}", "emissions": [], "flag": True,}}\n'
        "```"
    )

    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) == {
        "description": note,
        "emissions": [],
        "flag": True,
    }


def test_fr_c03_single_quotes_are_not_repaired_returns_none():
    """Fr c03 single quotes are not repaired returns none."""
    block = "```inventory\n{'description': 'x', 'emissions': []}\n```"

    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) is None


def test_fr_c03_non_json_text_in_fence_returns_none():
    """Fr c03 non json text in fence returns none."""
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\nnot json at all\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_array_root_in_fence_returns_none():
    """Fr c04 array root in fence returns none."""
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\n[]\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_scalar_root_in_fence_returns_none():
    """Fr c04 scalar root in fence returns none."""
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\n42\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_multiple_json_objects_after_reasoning_returns_none():
    """Fr c04 multiple json objects after reasoning returns none."""
    answer = as_sections(PLAN_FIELDS) + '\n\n{"a": 1}\n{"b": 2}'

    assert salvage_inventory_payload(answer) is None


def test_fr_c09_next_agent_marker_with_json_after_sections():
    """Fr c09 next agent marker with json after sections."""
    payload = {"description": "apos marker", "emissions": []}

    answer = as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum\n\n" + json.dumps(payload)

    assert salvage_inventory_payload(answer) == payload


def test_fence_takes_precedence_over_trailing_json():
    """Fence takes precedence over trailing json."""
    fenced = {"description": "fence wins", "emissions": []}

    answer = (
        as_sections(PLAN_FIELDS)
        + "\n\n"
        + extracted_inventory_block(fenced)
        + '\n\n{"description": "tail loses", "emissions": []}'
    )

    assert salvage_inventory_payload(answer) == fenced


def test_reasoning_heading_accent_and_case_insensitive():
    """Reasoning heading accent and case insensitive."""
    payload = {"description": "casefold", "emissions": []}

    answer = (
        f"## {PLAN_SECTIONS['defined_problem']}\n{PLAN_FIELDS['defined_problem']}\n\n"
        f"## {PLAN_SECTIONS['method']}\n{PLAN_FIELDS['method']}\n\n"
        "## RACIOCINIO\n"
        f"{PLAN_FIELDS['reasoning']}\n\n" + json.dumps(payload)
    )

    assert salvage_inventory_payload(answer) == payload


def test_empty_answer_returns_none():
    """Empty answer returns none."""
    assert salvage_inventory_payload("") is None


def test_no_inventory_signal_returns_none():
    """No inventory signal returns none."""
    assert salvage_inventory_payload(as_sections(PLAN_FIELDS)) is None


def test_nested_braces_and_escaped_quotes_in_trailing_json():
    """Nested braces and escaped quotes in trailing json."""
    payload = {
        "description": 'Say "hello"',
        "emissions": [{"quantity_co2e": 1.0, "note": "a\\b"}],
    }

    answer = as_sections(PLAN_FIELDS) + "\n\n" + json.dumps(payload)

    assert salvage_inventory_payload(answer) == payload


def test_unclosed_object_after_reasoning_returns_none():
    """Unclosed object after reasoning returns none."""
    answer = as_sections(PLAN_FIELDS) + '\n\n{"description": "x", "emissions": ['

    assert salvage_inventory_payload(answer) is None


def test_plan_heading_after_reasoning_stops_tail_search():
    """Plan heading after reasoning stops tail search."""
    payload = {"description": "nao deve achar", "emissions": []}

    answer = (
        as_sections(PLAN_FIELDS)
        + f"\n\n## {PLAN_SECTIONS['defined_problem']}\n"
        + json.dumps(payload)
    )

    assert salvage_inventory_payload(answer) is None
