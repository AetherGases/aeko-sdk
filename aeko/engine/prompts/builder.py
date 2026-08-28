from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict

from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
    SystemMessagePromptTemplate,
)
from langchain_core.prompts.chat import MessagesPlaceholder

DEFAULT_INITIAL_CONTEXT = (
    "Você é um agente do ecossistema Aether: uma empresa que busca auxiliar industrias na transição e monintoramento da substituição de gases poluentes para gases verdes."
)


@dataclass
class PromptSpec:
    agent: str = ""
    scope: str = ""
    persona: str = ""
    tasks: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    next_agents: list[str] = field(default_factory=list)
    shots: list[Dict[str, str]] = field(default_factory=list)
    initial_context: str = DEFAULT_INITIAL_CONTEXT


def _current_datetime() -> str:
    """
    Format the current local time for the prompt's "Contexto Temporal" section.

    Returns:
        str: The current date and time, e.g. "Monday, 19 de August de 2026 — 14:30:00".
    """

    return datetime.now(timezone.utc).astimezone().strftime("%A, %d de %B de %Y — %H:%M:%S %Z")


def _render_instructions(prompt: PromptSpec) -> str:
    """
    Render a PromptSpec's fields into the system prompt's instruction sections.

    Args:
        prompt: The spec describing the agent's scope, persona, tasks, tools and
            possible next agents.

    Returns:
        str: The rendered instructions, as titled sections joined by blank lines.
    """

    sections = [
        ("# Contexto Inicial", prompt.initial_context),
        ("# Escopo", prompt.scope),
        ("# Persona", "Voce é o agente: " + prompt.agent + " - Você deve atuar com base em: " + prompt.persona),
        ("# Tarefas", "\n".join(f"- {task}" for task in prompt.tasks)),
        ("# Ferramentas Disponiveis", "\n".join(f"- {tool}" for tool in prompt.tools)),
        ("# Agentes disponiveis", "\n".join(f"- {agent}" for agent in prompt.next_agents)),
        ("# Formato da Resposta", (
            "Ao final da sua resposta, adicione uma nova linha no formato exato "
            "\"Next agent: <Nome>\", usando exatamente um dos nomes listados em "
            "\"Agentes disponiveis\". Se nao houver um proximo agente (fluxo "
            "encerrado), utilize \"Next agent: Nenhum\"."
        )),
    ]

    rendered_sections = []
    for title, content in sections:
        rendered_sections.append(f"{title}\n{content if content else '(No content for this section)'}".rstrip())

    return "\n\n".join(rendered_sections)


_SYSTEM_TEMPLATE = "{instructions}\n\n# Contexto Temporal\nData e hora atuais: {now}"


def build_prompt(prompt: PromptSpec | None = None, **kwargs) -> ChatPromptTemplate:
    """
    Build a chat prompt template for an agent from a PromptSpec (or its fields).

    Args:
        prompt: A ready-made PromptSpec instance. Mutually exclusive with
            passing spec fields as keyword arguments.
        **kwargs: PromptSpec fields (agent, scope, persona, tasks, tools,
            next_agents, shots, initial_context) used to build a PromptSpec when
            `prompt` is not given.

    Returns:
        ChatPromptTemplate: A system message (with rendered instructions and the
            current date/time), optional few-shot examples, and a placeholder for
            runtime "messages".

    Raises:
        ValueError: If both `prompt` and keyword arguments are given.
        TypeError: If `prompt` is given but isn't a PromptSpec instance.
    """

    if prompt is None:
        prompt = PromptSpec(**kwargs)
    elif kwargs:
        raise ValueError("Use either a prompt object or keyword arguments, not both.")
    elif not isinstance(prompt, PromptSpec):
        raise TypeError("prompt must be a PromptSpec instance.")

    system_prompt = SystemMessagePromptTemplate.from_template(
        _SYSTEM_TEMPLATE,
        partial_variables={
            "instructions": _render_instructions(prompt),
            "now": _current_datetime,
        },
    )

    messages = [system_prompt]

    if prompt.shots:
        example_prompt = ChatPromptTemplate.from_messages(
            [("human", "{pergunta}"), ("ai", "{resposta}")]
        )
        messages.append(
            FewShotChatMessagePromptTemplate(
                example_prompt=example_prompt,
                examples=prompt.shots,
            )
        )

    messages.append(MessagesPlaceholder(variable_name="messages"))

    return ChatPromptTemplate.from_messages(messages)
