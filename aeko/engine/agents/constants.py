"""Constants used by agent construction.

Agents that only classify, consolidate or review use the fast model.
Everything else is a specialist analyst and gets the high-effort model.
"""

FAST_AGENTS = (
    "Roteador",
    "FAQ",
    "Orquestrador",
    "Guardrail de Saída",
    "Verificador de Resposta",
)
