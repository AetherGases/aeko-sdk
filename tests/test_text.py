"""Tests for the marker parsing in aeko/config/_text.py."""

import pytest

from aeko.config._text import parse_sections, strip_routing_marker

LABELS = {
    "defined_problem": "Problema definido",
    "method": "Método",
    "reasoning": "Raciocínio",
}

ANSWER = (
    "## Problema definido\n"
    "Os fornos concentram a emissao.\n\n"
    "## Método\n"
    "Trocar os queimadores.\n\n"
    "## Raciocínio\n"
    "A combustao e a fonte dominante."
)


# --- strip_routing_marker ------------------------------------------------


def test_the_routing_marker_is_stripped():
    assert strip_routing_marker("Resposta.\nNext agent: Nenhum") == "Resposta."


def test_an_answer_without_a_marker_is_left_alone():
    assert strip_routing_marker("Resposta.") == "Resposta."


# --- parse_sections ------------------------------------------------------


def test_every_requested_section_is_extracted():
    assert parse_sections(ANSWER, LABELS) == {
        "defined_problem": "Os fornos concentram a emissao.",
        "method": "Trocar os queimadores.",
        "reasoning": "A combustao e a fonte dominante.",
    }


def test_a_section_keeps_its_own_line_breaks():
    text = "## Método\nPrimeiro passo.\n\nSegundo passo."

    assert parse_sections(text, LABELS)["method"] == "Primeiro passo.\n\nSegundo passo."


def test_the_sections_can_come_in_any_order():
    text = "## Raciocínio\nPorque sim.\n\n## Problema definido\nO forno."

    assert parse_sections(text, LABELS) == {
        "reasoning": "Porque sim.",
        "defined_problem": "O forno.",
    }


def test_anything_before_the_first_section_belongs_to_no_section():
    text = "Claro! Segue o plano:\n\n## Método\nTrocar os queimadores."

    assert parse_sections(text, LABELS) == {"method": "Trocar os queimadores."}


@pytest.mark.parametrize("heading", ["# Método", "### Método", "##Método", "##  Método  "])
def test_the_heading_level_and_spacing_are_tolerated(heading):
    assert parse_sections(f"{heading}\nTrocar.", LABELS) == {"method": "Trocar."}


@pytest.mark.parametrize("heading", ["## METODO", "## metodo", "## Metodo", "## Método"])
def test_case_and_accents_are_tolerated(heading):
    assert parse_sections(f"{heading}\nTrocar.", LABELS) == {"method": "Trocar."}


def test_a_heading_that_was_not_asked_for_does_not_open_a_section():
    text = "## Método\n## Fase 1\nTrocar os queimadores."

    assert parse_sections(text, LABELS) == {
        "method": "## Fase 1\nTrocar os queimadores."
    }


def test_a_repeated_heading_continues_its_section():
    text = "## Método\nTrocar.\n\n## Método\nMigrar."

    assert parse_sections(text, LABELS) == {"method": "Trocar.\n\nMigrar."}


def test_a_section_that_was_left_empty_is_reported_as_empty():
    text = "## Problema definido\nO forno.\n\n## Método\n\n## Raciocínio\nPorque sim."

    assert parse_sections(text, LABELS)["method"] == ""


def test_a_section_that_was_never_written_is_absent():
    assert "reasoning" not in parse_sections("## Método\nTrocar.", LABELS)


def test_an_answer_without_any_section_yields_nothing():
    assert parse_sections("Plano: trocar os queimadores.", LABELS) == {}


def test_only_the_requested_labels_are_read():
    text = "## Método\nTrocar.\n\n## Raciocínio\nPorque sim."

    assert parse_sections(text, {"method": "Método"}) == {
        "method": "Trocar.\n\n## Raciocínio\nPorque sim."
    }
