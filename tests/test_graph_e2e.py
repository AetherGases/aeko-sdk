import os
from pathlib import Path

import pytest

from system.engine.graph.builder import AETHER_APP
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

requires_gemini_api_key = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is not set; this test invokes the real Gemini API through the graph.",
)


@requires_gemini_api_key
def test_graph_invocation_writes_result_to_file():
    initial_state = create_initial_state(INITIAL_QUESTION)

    result = AETHER_APP.invoke(initial_state)

    # Only terminal nodes (FAQ, Coordenador de Melhoria Contínua) and an
    # approved Guardrail write to "messages" (see nodes.py). If the run ends
    # without ever reaching one of those - e.g. the guardrail rejects past the
    # retry cap - "messages" never grows past the original question, and there
    # is no real final answer to report.
    assert len(result["messages"]) > 1, (
        "O grafo terminou sem produzir uma resposta final aprovada "
        "(guardrail provavelmente reprovou ate o limite de retries)."
    )

    final_message = result["messages"][-1]
    final_content = (
        final_message.content if hasattr(final_message, "content") else final_message["content"]
    )

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
