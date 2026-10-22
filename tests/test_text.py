"""Tests for the marker parsing in aeko/shared/text.py."""

import pytest


from aeko.shared.text import next_agent_name, parse_sections, strip_routing_marker


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


def test_the_routing_marker_is_stripped():
    """The routing marker is stripped."""
    assert strip_routing_marker("Resposta.\nNext agent: Nenhum") == "Resposta."


def test_an_answer_without_a_marker_is_left_alone():
    """An answer without a marker is left alone."""
    assert strip_routing_marker("Resposta.") == "Resposta."


def test_text_below_the_routing_marker_is_kept():
    """Text below the routing marker is kept."""
    answer = "Resposta.\nNext agent: Nenhum\n\n```inventory\n{}\n```"

    assert strip_routing_marker(answer) == "Resposta.\n\n```inventory\n{}\n```"


def test_next_agent_name_is_the_rest_of_the_marker_line():
    """Next agent name is the rest of the marker line."""
    assert next_agent_name("Ok.\nNext agent: FAQ") == "FAQ"


def test_next_agent_name_ignores_text_below_the_marker_line():
    """Next agent name ignores text below the marker line."""
    assert (
        next_agent_name("Ok.\nNext agent: Nenhum\n\n```inventory\n{}\n```") == "Nenhum"
    )


def test_next_agent_name_is_empty_when_there_is_no_marker():
    """Next agent name is empty when there is no marker."""
    assert next_agent_name("Ok.") == ""


def test_every_requested_section_is_extracted():
    """Every requested section is extracted."""
    assert parse_sections(ANSWER, LABELS) == {
        "defined_problem": "Os fornos concentram a emissao.",
        "method": "Trocar os queimadores.",
        "reasoning": "A combustao e a fonte dominante.",
    }


def test_a_section_keeps_its_own_line_breaks():
    """A section keeps its own line breaks."""
    text = "## Método\nPrimeiro passo.\n\nSegundo passo."

    assert parse_sections(text, LABELS)["method"] == "Primeiro passo.\n\nSegundo passo."


def test_the_sections_can_come_in_any_order():
    """The sections can come in any order."""
    text = "## Raciocínio\nPorque sim.\n\n## Problema definido\nO forno."

    assert parse_sections(text, LABELS) == {
        "reasoning": "Porque sim.",
        "defined_problem": "O forno.",
    }


def test_anything_before_the_first_section_belongs_to_no_section():
    """Anything before the first section belongs to no section."""
    text = "Claro! Segue o plano:\n\n## Método\nTrocar os queimadores."

    assert parse_sections(text, LABELS) == {"method": "Trocar os queimadores."}


@pytest.mark.parametrize(
    "heading", ["# Método", "### Método", "##Método", "##  Método  "]
)
def test_the_heading_level_and_spacing_are_tolerated(heading):
    """The heading level and spacing are tolerated."""
    assert parse_sections(f"{heading}\nTrocar.", LABELS) == {"method": "Trocar."}


@pytest.mark.parametrize(
    "heading", ["## METODO", "## metodo", "## Metodo", "## Método"]
)
def test_case_and_accents_are_tolerated(heading):
    """Case and accents are tolerated."""
    assert parse_sections(f"{heading}\nTrocar.", LABELS) == {"method": "Trocar."}


def test_a_heading_that_was_not_asked_for_does_not_open_a_section():
    """A heading that was not asked for does not open a section."""
    text = "## Método\n## Fase 1\nTrocar os queimadores."

    assert parse_sections(text, LABELS) == {
        "method": "## Fase 1\nTrocar os queimadores."
    }


def test_a_repeated_heading_continues_its_section():
    """A repeated heading continues its section."""
    text = "## Método\nTrocar.\n\n## Método\nMigrar."

    assert parse_sections(text, LABELS) == {"method": "Trocar.\n\nMigrar."}


def test_a_section_that_was_left_empty_is_reported_as_empty():
    """A section that was left empty is reported as empty."""
    text = "## Problema definido\nO forno.\n\n## Método\n\n## Raciocínio\nPorque sim."

    assert parse_sections(text, LABELS)["method"] == ""


def test_a_section_that_was_never_written_is_absent():
    """A section that was never written is absent."""
    assert "reasoning" not in parse_sections("## Método\nTrocar.", LABELS)


def test_an_answer_without_any_section_yields_nothing():
    """An answer without any section yields nothing."""
    assert parse_sections("Plano: trocar os queimadores.", LABELS) == {}


def test_only_the_requested_labels_are_read():
    """Only the requested labels are read."""
    text = "## Método\nTrocar.\n\n## Raciocínio\nPorque sim."

    assert parse_sections(text, {"method": "Método"}) == {
        "method": "Trocar.\n\n## Raciocínio\nPorque sim."
    }
