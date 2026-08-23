"""
End-to-end runs of the whole graph, against a scripted chat model.

Everything above the model is real here — the prompt templates, the agent
executors, the routing, the guardrail loop — so these still exercise the wiring
the previous API-backed version did, minus the network.
"""

from pathlib import Path

import pytest

from system.engine.graph.builder import get_app
from system.engine.graph.state import create_initial_state

# Compatible with Aether's domain: GHG inventory / gas transition for industry (see
# system/engine/prompts/router.py and report_analyst.py for similar examples).
INITIAL_QUESTION = (
    "Sou gestor ambiental de uma fabrica de ceramica e trouxe o inventario GHG de 2023: "
    "emissoes de CO2 dos fornos a gas natural (1.200 toneladas) e consumo de eletricidade "
    "que gera 400 toneladas CO2e. Quero entender os riscos ambientais desses gases e quais "
    "gases verdes poderiam substituir o gas natural nos meus fornos a 1.200 graus Celsius."
)

RESULT_FILE = Path(__file__).resolve().parent.parent / "result.txt"

CONSOLIDATED_ANSWER = (
    "Seu inventario soma 1.600 tCO2e. Os fornos a gas natural concentram o risco "
    "ambiental, e o hidrogenio verde e a substituicao viavel para 1.200 graus Celsius."
)

# The full happy path: router -> both specialists -> orchestrator -> guardrail.
FULL_FLOW = {
    "Roteador": "Encaminhando para analise tecnica.\nNext agent: Analista de Poluentes",
    "Analista de Poluentes": (
        "Emissoes de CO2 dos fornos em nivel critico.\nNext agent: Analista de Gases Verdes"
    ),
    "Analista de Gases Verdes": (
        "Hidrogenio verde atende 1.200 graus Celsius.\nNext agent: Orquestrador"
    ),
    "Orquestrador": f"{CONSOLIDATED_ANSWER}\nNext agent: Guardrail de Saída",
    "Guardrail de Saída": "Aprovado. Resposta fundamentada.\nNext agent: Nenhum",
}

# Same flow, but the guardrail never lets the draft through.
REJECTING_FLOW = {
    **FULL_FLOW,
    "Guardrail de Saída": "Reprovado. Faltam dados de escopo 3.\nNext agent: Nenhum",
}

# The report entry point: inventory analyst -> pollutants -> continuous improvement.
INVENTORY_FLOW = {
    "Análista de inventários": (
        "Escopo 1 = 1.200 tCO2e, Escopo 2 = 400 tCO2e.\nNext agent: Analista de Poluentes"
    ),
    "Analista de Poluentes": "CO2 de combustao e o driver dominante.\nNext agent: Orquestrador",
    "Coordenador de Melhoria Contínua": (
        "Plano: trocar os queimadores e recuperar calor residual.\nNext agent: Nenhum"
    ),
}


def _final_content(result) -> str:
    final = result["messages"][-1]
    return final.content if hasattr(final, "content") else final["content"]


@pytest.fixture
def run_graph(use_fake_llm):
    """Run the compiled graph against a scripted model, and return its state."""

    def _run(responses, question=INITIAL_QUESTION, entry_point="Roteador", **state_kwargs):
        llm = use_fake_llm(responses)
        state = create_initial_state(question, **state_kwargs)
        result = get_app().invoke(
            state, config={"configurable": {"entry_point": entry_point}}
        )
        return result, llm

    return _run


def test_graph_invocation_writes_result_to_file(run_graph):
    result, _ = run_graph(FULL_FLOW)

    # Only terminal nodes (FAQ, Coordenador de Melhoria Contínua) and an
    # approved Guardrail write to "messages" (see nodes.py). If the run ends
    # without ever reaching one of those - e.g. the guardrail rejects past the
    # retry cap - "messages" never grows past the original question, and there
    # is no real final answer to report.
    assert len(result["messages"]) > 1, (
        "O grafo terminou sem produzir uma resposta final aprovada "
        "(guardrail provavelmente reprovou ate o limite de retries)."
    )

    final_content = _final_content(result)

    report_lines = [
        "Agentes chamados:",
        *(f"- {agent_name}" for agent_name in result.get("previous_agents", {})),
        "",
        "Resposta final:",
        final_content,
    ]
    RESULT_FILE.write_text("\n".join(report_lines), encoding="utf-8")

    assert final_content
    assert RESULT_FILE.exists()


def test_approved_answer_is_the_orchestrators_draft(run_graph):
    result, _ = run_graph(FULL_FLOW)

    assert result["guard_rail_approved"] is True
    assert result["guard_rail_retries"] == 0
    # The graph stores the agent's raw output, routing marker and all; it is
    # the SDK facade that strips it before the answer reaches a caller (see
    # tests/test_config.py::test_answer_is_free_of_the_routing_marker).
    assert _final_content(result).startswith(CONSOLIDATED_ANSWER)


def test_every_specialist_contributes_before_consolidation(run_graph):
    result, llm = run_graph(FULL_FLOW)

    assert llm.agents_called() == [
        "Roteador",
        "Analista de Poluentes",
        "Analista de Gases Verdes",
        "Orquestrador",
        "Guardrail de Saída",
    ]
    assert "Analista de Poluentes" in result["previous_agents"]
    assert "Analista de Gases Verdes" in result["previous_agents"]


def test_specialist_findings_reach_the_orchestrator(run_graph):
    _, llm = run_graph(FULL_FLOW)

    orchestrator_prompt = llm.prompt_for("Orquestrador")

    assert "Análises recebidas até agora" in orchestrator_prompt
    assert "Hidrogenio verde atende" in orchestrator_prompt


def test_company_context_reaches_the_first_agent(run_graph):
    _, llm = run_graph(FULL_FLOW, company_context="Ceramica X, relatorio de 2022: 2.100 tCO2e.")

    assert "Ceramica X" in llm.prompt_for("Roteador")


def test_rejected_answer_never_reaches_the_user(run_graph):
    result, _ = run_graph(REJECTING_FLOW)

    assert result["guard_rail_approved"] is False
    assert len(result["messages"]) == 1, "um rascunho reprovado nao pode virar resposta"


def test_guardrail_retries_are_capped(run_graph):
    result, _ = run_graph(REJECTING_FLOW)

    assert result["guard_rail_retries"] == 4, "o laco deve parar logo apos exceder o limite"
    assert result["guard_rail_requested_changes"], "o feedback do guardrail deve ser registrado"


def test_guardrail_feedback_is_sent_back_to_the_router(run_graph):
    _, llm = run_graph(REJECTING_FLOW)

    assert "Pontos apontados pelo Guardrail de Saída" in llm.prompt_for("Roteador")


def test_inventory_entry_point_ends_at_the_improvement_coordinator(run_graph):
    result, llm = run_graph(
        INVENTORY_FLOW,
        question="| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |",
        entry_point="Análista de inventários",
    )

    assert llm.agents_called() == [
        "Análista de inventários",
        "Analista de Poluentes",
        "Coordenador de Melhoria Contínua",
    ]
    assert _final_content(result).startswith("Plano:")
    assert result["guard_rail_approved"] is False, "este fluxo nao passa pelo guardrail"
