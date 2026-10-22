"""Constants used to build agent prompts."""

DEFAULT_INITIAL_CONTEXT = "Você é um agente do ecossistema Aether: uma empresa que busca auxiliar industrias na transição e monintoramento da substituição de gases poluentes para gases verdes."

PLAN_SECTIONS: dict[str, str] = {
    "defined_problem": "Problema definido",
    "method": "Método",
    "reasoning": "Raciocínio",
}
