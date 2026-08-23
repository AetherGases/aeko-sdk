import time

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from aeko.engine.prompts import builder
from aeko.engine.prompts.builder import build_prompt


def _system_content(prompt_template: ChatPromptTemplate) -> str:
    messages = prompt_template.format_messages(messages=[])
    return "\n\n".join(m.content for m in messages if m.type == "system")


def test_build_prompt_supports_kwargs_and_preserves_titles():
    prompt_template = build_prompt(agent="FAQ", scope="Responder dúvidas", tasks=["responder"], tools=["busca"], next_agents=["router"])

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
    prompt_template = build_prompt(agent="FAQ", scope="Responder dúvidas")

    system_content = _system_content(prompt_template)

    assert "Next agent:" in system_content
    assert "Nenhum" in system_content


def test_build_prompt_only_requires_messages_as_input():
    prompt_template = build_prompt(agent="FAQ", scope="Responder dúvidas")

    assert prompt_template.input_variables == ["messages"]


def test_build_prompt_renders_shots_as_alternating_human_ai_messages():
    prompt_template = build_prompt(
        agent="FAQ",
        shots=[
            {"pergunta": "Qual a capital do Brasil?", "resposta": "Brasília"},
            {"pergunta": "O que é hidrogênio verde?", "resposta": "Um gás produzido por eletrólise com energia renovável."},
        ],
    )

    messages = prompt_template.format_messages(messages=[])

    shot_messages = [m for m in messages if m.type != "system"]
    assert [m.type for m in shot_messages] == ["human", "ai", "human", "ai"]
    assert shot_messages[0].content == "Qual a capital do Brasil?"
    assert shot_messages[1].content == "Brasília"
    assert shot_messages[2].content == "O que é hidrogênio verde?"
    assert shot_messages[3].content == "Um gás produzido por eletrólise com energia renovável."


def test_build_prompt_without_shots_has_no_example_messages():
    prompt_template = build_prompt(agent="FAQ")

    messages = prompt_template.format_messages(messages=[])

    assert [m.type for m in messages] == ["system"]


def test_build_prompt_uses_a_single_leading_system_message():
    prompt_template = build_prompt(
        agent="FAQ",
        shots=[{"pergunta": "Oi?", "resposta": "Olá!"}],
    )

    messages = prompt_template.format_messages(messages=[HumanMessage("Oi, tudo bem?")])

    assert sum(1 for m in messages if m.type == "system") == 1
    assert messages[0].type == "system"


def test_build_prompt_passes_through_runtime_messages():
    prompt_template = build_prompt(agent="FAQ")

    messages = prompt_template.format_messages(messages=[HumanMessage("Oi, tudo bem?")])

    assert messages[-1].content == "Oi, tudo bem?"


def test_build_prompt_tolerates_literal_braces_in_free_text_content():
    prompt_template = build_prompt(agent="FAQ", scope="Custo estimado: {valor} reais, JSON: {\"a\": 1}.")

    system_content = _system_content(prompt_template)

    assert "{valor}" in system_content


def test_build_prompt_recomputes_temporal_context_on_each_render():
    prompt_template = builder.build_prompt(agent="FAQ")

    first_stamp = _system_content(prompt_template).split("Data e hora atuais: ")[1]
    time.sleep(1.1)
    second_stamp = _system_content(prompt_template).split("Data e hora atuais: ")[1]

    assert first_stamp != second_stamp
