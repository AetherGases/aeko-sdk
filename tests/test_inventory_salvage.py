"""Unit tests for deterministic inventory JSON salvage (FR-C01–C04, FR-C09)."""

import json

import pytest

from aeko.config.inventory_salvage import salvage_inventory_payload
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
    answer = with_extracted_inventory(as_sections(PLAN_FIELDS) + "\nNext agent: Nenhum", MINIMAL_INVENTORY)

    assert salvage_inventory_payload(answer) == MINIMAL_INVENTORY


def test_fr_c02_json_after_last_section_without_fence():
    payload = {"description": "Pos secoes", "emissions": []}
    answer = (
        as_sections(PLAN_FIELDS)
        + "\n\n"
        + json.dumps(payload)
        + "\nNext agent: Nenhum"
    )

    assert salvage_inventory_payload(answer) == payload


def test_fr_c02_json_only_inside_method_section_returns_none():
    sneaky = '{"description": "escondido", "emissions": []}'
    answer = as_sections(
        {
            **PLAN_FIELDS,
            "method": f"Veja isto: {sneaky}",
        }
    )

    assert salvage_inventory_payload(answer) is None


def test_fr_c03_trailing_comma_in_fence_repairs_and_parses():
    block = (
        "```inventory\n"
        '{"description": "x", "emissions": [],}\n'
        "```"
    )
    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) == {
        "description": "x",
        "emissions": [],
    }


def test_fr_c03_python_literals_in_fence_repairs_and_parses():
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
    block = "```inventory\n{'description': 'x', 'emissions': []}\n```"
    answer = as_sections(PLAN_FIELDS) + "\n\n" + block

    assert salvage_inventory_payload(answer) is None


def test_fr_c03_non_json_text_in_fence_returns_none():
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\nnot json at all\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_array_root_in_fence_returns_none():
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\n[]\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_scalar_root_in_fence_returns_none():
    answer = as_sections(PLAN_FIELDS) + "\n\n```inventory\n42\n```"

    assert salvage_inventory_payload(answer) is None


def test_fr_c04_multiple_json_objects_after_reasoning_returns_none():
    answer = (
        as_sections(PLAN_FIELDS)
        + '\n\n{"a": 1}\n{"b": 2}'
    )

    assert salvage_inventory_payload(answer) is None


def test_fr_c09_next_agent_marker_with_json_after_sections():
    payload = {"description": "apos marker", "emissions": []}
    answer = (
        as_sections(PLAN_FIELDS)
        + "\nNext agent: Nenhum\n\n"
        + json.dumps(payload)
    )

    assert salvage_inventory_payload(answer) == payload


def test_fence_takes_precedence_over_trailing_json():
    fenced = {"description": "fence wins", "emissions": []}
    answer = (
        as_sections(PLAN_FIELDS)
        + "\n\n"
        + extracted_inventory_block(fenced)
        + '\n\n{"description": "tail loses", "emissions": []}'
    )

    assert salvage_inventory_payload(answer) == fenced


def test_reasoning_heading_accent_and_case_insensitive():
    payload = {"description": "casefold", "emissions": []}
    answer = (
        f"## {PLAN_SECTIONS['defined_problem']}\n{PLAN_FIELDS['defined_problem']}\n\n"
        f"## {PLAN_SECTIONS['method']}\n{PLAN_FIELDS['method']}\n\n"
        "## RACIOCINIO\n"
        f"{PLAN_FIELDS['reasoning']}\n\n"
        + json.dumps(payload)
    )

    assert salvage_inventory_payload(answer) == payload


def test_empty_answer_returns_none():
    assert salvage_inventory_payload("") is None


def test_no_inventory_signal_returns_none():
    assert salvage_inventory_payload(as_sections(PLAN_FIELDS)) is None


def test_nested_braces_and_escaped_quotes_in_trailing_json():
    payload = {
        "description": 'Say "hello"',
        "emissions": [{"quantity_co2e": 1.0, "note": "a\\b"}],
    }
    answer = as_sections(PLAN_FIELDS) + "\n\n" + json.dumps(payload)

    assert salvage_inventory_payload(answer) == payload


def test_unclosed_object_after_reasoning_returns_none():
    answer = as_sections(PLAN_FIELDS) + '\n\n{"description": "x", "emissions": ['

    assert salvage_inventory_payload(answer) is None


def test_plan_heading_after_reasoning_stops_tail_search():
    payload = {"description": "nao deve achar", "emissions": []}
    answer = (
        as_sections(PLAN_FIELDS)
        + f"\n\n## {PLAN_SECTIONS['defined_problem']}\n"
        + json.dumps(payload)
    )

    assert salvage_inventory_payload(answer) is None
