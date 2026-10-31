"""tests/test_prompt_builder.py."""

import time


from langchain_core.messages import HumanMessage

from langchain_core.prompts import ChatPromptTemplate


import pytest


from aeko.shared.text import parse_sections

from aeko.engine.prompts import (
    AGENT_NAMES,
    CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC,
    PLAN_SECTIONS,
    PROMPT_SPECS,
    builder,
)

from aeko.engine.prompts.builder import build_prompt

from aeko.engine.prompts.constants import DEFAULT_INITIAL_CONTEXT


def _system_content(prompt_template: ChatPromptTemplate) -> str:
    """System content."""
    messages = prompt_template.format_messages(messages=[])

    return "\n\n".join(m.content for m in messages if m.type == "system")


def test_build_prompt_supports_kwargs_and_preserves_titles():
    """Build prompt supports kwargs and preserves titles."""
    prompt_template = build_prompt(
        agent="FAQ",
        scope="Responder dúvidas",
        tasks=["responder"],
        tools=["busca"],
        next_agents=["router"],
    )

    assert isinstance(prompt_template, ChatPromptTemplate)

    system_content = _system_content(prompt_template)

    assert "# Contexto Inicial" in system_content

    assert "# Contexto Temporal" in system_content

    assert "# Escopo" in system_content

    assert "# Persona" in system_content

    assert "# Tarefas" in system_content

    assert "# Ferramentas Disponiveis" in system_content

    assert "# Agentes disponiveis" in system_content

    assert "# Formato da Resposta" in system_content

    assert "Responder dúvidas" in system_content

    assert "- responder" in system_content

    assert "- busca" in system_content

    assert "- router" in system_content


def test_build_prompt_instructs_next_agent_marker():
    """Build prompt instructs next agent marker."""
    prompt_template = build_prompt(agent="FAQ", scope="Responder dúvidas")

    system_content = _system_content(prompt_template)

    assert "Next agent:" in system_content

    assert "Nenhum" in system_content

    assert "não escreva nada depois dela" in system_content


def test_build_prompt_only_requires_messages_as_input():
    """Build prompt only requires messages as input."""
    prompt_template = build_prompt(agent="FAQ", scope="Responder dúvidas")

    assert prompt_template.input_variables == ["messages"]


def test_build_prompt_renders_shots_as_alternating_human_ai_messages():
    """Build prompt renders shots as alternating human ai messages."""
    prompt_template = build_prompt(
        agent="FAQ",
        shots=[
            {"pergunta": "Qual a capital do Brasil?", "resposta": "Brasília"},
            {
                "pergunta": "O que é hidrogênio verde?",
                "resposta": "Um gás produzido por eletrólise com energia renovável.",
            },
        ],
    )

    messages = prompt_template.format_messages(messages=[])

    shot_messages = [m for m in messages if m.type != "system"]

    assert [m.type for m in shot_messages] == ["human", "ai", "human", "ai"]

    assert shot_messages[0].content == "Qual a capital do Brasil?"

    assert shot_messages[1].content == "Brasília"

    assert shot_messages[2].content == "O que é hidrogênio verde?"

    assert (
        shot_messages[3].content
        == "Um gás produzido por eletrólise com energia renovável."
    )


def test_build_prompt_without_shots_has_no_example_messages():
    """Build prompt without shots has no example messages."""
    prompt_template = build_prompt(agent="FAQ")

    messages = prompt_template.format_messages(messages=[])

    assert [m.type for m in messages] == ["system"]


def test_build_prompt_uses_a_single_leading_system_message():
    """Build prompt uses a single leading system message."""
    prompt_template = build_prompt(
        agent="FAQ",
        shots=[{"pergunta": "Oi?", "resposta": "Olá!"}],
    )

    messages = prompt_template.format_messages(messages=[HumanMessage("Oi, tudo bem?")])

    assert sum(1 for m in messages if m.type == "system") == 1

    assert messages[0].type == "system"


def test_build_prompt_passes_through_runtime_messages():
    """Build prompt passes through runtime messages."""
    prompt_template = build_prompt(agent="FAQ")

    messages = prompt_template.format_messages(messages=[HumanMessage("Oi, tudo bem?")])

    assert messages[-1].content == "Oi, tudo bem?"


def test_build_prompt_tolerates_literal_braces_in_free_text_content():
    """Build prompt tolerates literal braces in free text content."""
    prompt_template = build_prompt(
        agent="FAQ", scope='Custo estimado: {valor} reais, JSON: {"a": 1}.'
    )

    system_content = _system_content(prompt_template)

    assert "{valor}" in system_content


@pytest.mark.parametrize("agent", list(PROMPT_SPECS))
def test_every_prompt_offers_only_real_agent_names(agent):
    """
    A name an agent is offered has to be a name the graph can route by.

    Each spec's "next_agents" is rendered into the prompt's "# Agentes
    disponiveis" section, and "# Formato da Resposta" tells the agent to answer
    with exactly one of those names. `_invoke_agent` then looks that name up in
    the agent registry, case included, so a name written differently here is one
    the run dies on precisely when the model obeys its instructions.
    """

    offered = [
        entry.split(" - ")[0].strip() for entry in PROMPT_SPECS[agent].next_agents
    ]

    unknown = [name for name in offered if name not in AGENT_NAMES]

    assert not unknown, f"{agent} oferece nomes que nao existem no registro: {unknown}"


def test_the_coordinator_is_told_to_answer_in_the_plan_sections():
    """The coordinator is told to answer in the plan sections."""
    system_content = _system_content(
        build_prompt(CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC)
    )

    for label in PLAN_SECTIONS.values():
        assert f"## {label}" in system_content


def test_every_coordinator_shot_is_readable_by_the_plan_parser():
    """
    The shots are what actually decide the answer's shape, so the parser has to
    agree with all six of them — an instruction the examples contradict is an
    instruction the model ignores.
    """

    for shot in CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.shots:
        sections = parse_sections(shot["resposta"], PLAN_SECTIONS)

        assert set(sections) == set(PLAN_SECTIONS)

        assert all(sections.values())


def test_build_prompt_recomputes_temporal_context_on_each_render():
    """Build prompt recomputes temporal context on each render."""
    prompt_template = builder.build_prompt(agent="FAQ")

    first_stamp = _system_content(prompt_template).split("Data e hora atuais: ")[1]

    time.sleep(1.1)

    second_stamp = _system_content(prompt_template).split("Data e hora atuais: ")[1]

    assert first_stamp != second_stamp


@pytest.mark.parametrize("agent", list(PROMPT_SPECS))
def test_no_prompt_contains_an_em_or_en_dash(agent):
    """No agent sees a travessão in its system prompt or few-shot examples."""
    messages = build_prompt(PROMPT_SPECS[agent]).format_messages(messages=[])

    text = "\n".join(m.content for m in messages)

    assert "\u2014" not in text
    assert "\u2013" not in text


def test_shared_context_forbids_the_travessao_in_words():
    """Shared context forbids the travessao in words."""
    assert "Nunca use travessão" in DEFAULT_INITIAL_CONTEXT


@pytest.mark.parametrize("agent", ["FAQ", "Orquestrador"])
def test_user_facing_shot_answers_never_name_the_multi_agent_structure(agent):
    """The answers that reach the user never name an internal agent or a hand-off."""
    for shot in PROMPT_SPECS[agent].shots:
        answer = shot["resposta"].lower()

        assert "encaminh" not in answer

        for spec in PROMPT_SPECS.values():
            assert spec.agent.lower() not in answer


@pytest.mark.parametrize("agent", ["FAQ", "Orquestrador"])
def test_user_facing_prompts_demand_a_single_assistant_voice(agent):
    """User facing prompts demand a single assistant voice."""
    text = " ".join(PROMPT_SPECS[agent].tasks)

    assert "assistente único" in text


@pytest.mark.parametrize("agent", ["Guardrail de Saída", "Verificador de Resposta"])
def test_reviewers_reject_drafts_that_leak_the_multi_agent_structure(agent):
    """Reviewers reject drafts that leak the multi agent structure."""
    assert "estrutura multiagente" in " ".join(PROMPT_SPECS[agent].tasks)

    assert any(
        "estrutura multiagente" in s["resposta"] for s in PROMPT_SPECS[agent].shots
    )


REVIEWERS = ["Guardrail de Saída", "Verificador de Resposta"]


@pytest.mark.parametrize("agent", REVIEWERS)
def test_reviewers_treat_the_material_as_data_and_fix_the_verdict_format(agent):
    """Reviewers treat the material as data and open with a fixed verdict."""
    spec = PROMPT_SPECS[agent]

    assert "nunca instruções" in spec.persona

    tasks = " ".join(spec.tasks)

    assert "'Aprovado.'" in tasks and "'Reprovado.'" in tasks

    assert tasks.index("código") < tasks.index("estrutura multiagente")


@pytest.mark.parametrize("agent", REVIEWERS)
def test_reviewers_have_shots_rejecting_code_and_embedded_instructions(agent):
    """Reviewers have shots rejecting code and embedded instructions."""
    shots = PROMPT_SPECS[agent].shots

    assert any("```" in s["pergunta"] and s["resposta"].startswith("Reprovado.") for s in shots)

    assert any("ignore" in s["pergunta"].lower() and s["resposta"].startswith("Reprovado.") for s in shots)


@pytest.mark.parametrize("agent", ["FAQ", "Orquestrador"])
def test_user_facing_prompts_carry_the_scope_rule(agent):
    """User facing prompts refuse code and out of domain tasks."""
    assert "nunca produzir código" in " ".join(PROMPT_SPECS[agent].tasks).lower()


def test_faq_has_a_shot_refusing_code():
    """FAQ has a shot refusing code."""
    assert any(
        "script" in s["pergunta"].lower() and "não posso" in s["resposta"].lower()
        for s in PROMPT_SPECS["FAQ"].shots
    )
