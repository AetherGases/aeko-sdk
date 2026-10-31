"""Constants used by agent construction.

Agents that only classify, consolidate or review use the fast model.
The reviewers (REVIEWER_AGENTS) also run at temperature 0, so the same
material always gets the same verdict.
Everything else is a specialist analyst and gets the high-effort model.
"""

REVIEWER_AGENTS = ("Guardrail de Saída", "Verificador de Resposta")

FAST_AGENTS = (
    "Roteador",
    "FAQ",
    "Orquestrador",
    "Guardrail de Saída",
    "Verificador de Resposta",
)
