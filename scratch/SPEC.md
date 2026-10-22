# Spec: Formato consistente do inventory no Coordenador de Melhoria Contínua

## ASSUMPTIONS I'M MAKING

1. O retry das 3 seções do plano (`PLAN_FORMAT_MAX_RETRIES = 4` em `_coordenador_melhoria_node`) **não muda**.
2. O pipeline de formato do `inventory` corre **depois do grafo**, em `AekoInventoryAnalyzer.analyze()`, não num segundo loop do nó.
3. `send_message` / prompts de chat **não mudam** (FR-015 do contrato já implementado).
4. Cada chamada de schema entra em `aeko_metrics.used_agents` como `Coordenador de Melhoria Contínua` (uma entrada por invocação).
5. O fallback de schema recebe **markdown do inventário + catálogos + schema**. Não recebe o JSON quebrado nem o plano congelado.
6. Chave `emissions` ausente vira `[]`. Chaves fora do contrato são descartadas. Não truncamos texto `> 150` caracteres.
7. Testes continuam 100% fake LLM (sem Gemini). Módulos em `aeko/config/` sem comentários inline.
8. O caller nunca vê a string crua do coordenador: `analyze()` devolve `plan` já aceito + `inventory` canônico.

→ Corrija agora se alguma premissa estiver errada.

## Objective

No fluxo analítico (`analyze()`), o Coordenador de Melhoria Contínua já devolve o plano em três seções markdown, com retry próprio. O payload `inventory` (bloco JSON) hoje só é validado **depois** do grafo: JSON quebrado ou tipos frouxos derrubam a análise inteira sem tentativa barata.

Esta demanda garante **consistência de formatação fora da IA**:

* Recuperar JSON com um passo determinístico.
* Canonicalizar o que o contrato já determina (tipos, `null`, `is_upstream`).
* Só então, se o JSON for irrecuperável, até **2** chamadas de schema (só o payload; plano congelado).
* Violação de catálogo/range/quantidade **não** reabre o modelo.

Usuário: `ms-aeko` via `analyze()`. Sucesso: menos `MalformedAgentOutputError` por lixo de formato, sem reescrever o plano nem inflar o caminho feliz (1 chamada slow do coordenador).

## Tech Stack

* Python `>=3.11,<3.12`
* Pacote `aeko` (LangChain / LangGraph / `langchain-google-genai` / Pydantic v2) — versões em `pyproject.toml`
* Testes: `pytest>=8,<9` (`requirements-dev.txt`)
* LLMs de produção: Gemini via `ChatGoogleGenerativeAI` (fast/slow com fallback cruzado). Coordenador usa o modelo **slow**.

Não adicionar dependência nova só para parse de JSON.

## Commands

```
python -m pytest
python -m pytest tests/test_inventory_salvage.py tests/test_inventory_canonicalize.py tests/test_inventory_fallback.py tests/test_inventory_prompt.py tests/test_inventory_contract.py tests/test_config.py tests/test_nodes.py
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m build && twine check dist/*
```

CI (`.github/workflows/ci.yml`): `python -m pytest` em Python 3.11.

## Project Structure

```
aeko/config/            → API pública e pipeline de analyze() (sem comentários inline)
aeko/config/inventory.py
aeko/engine/graph/      → nó do coordenador (retry de seções; não alterar o cap)
aeko/engine/prompts/    → INVENTORY_PAYLOAD_INSTRUCTIONS e shots de chat
aeko/engine/agents/     → create_agent; wrapper lê .content
tests/                  → pytest; fake LLM em tests/conftest.py
scratch/                → estas specs e tickets (não é código)
```

Arquivos novos desta demanda devem ficar em `aeko/config/` (funções do pipeline) e `tests/test_inventory_*.py`, para as tickets não se atropelarem. O grafo só muda se o comportamento observável de retry de **seções** exigir — o desenho atual diz que não.

## Code Style

Docstrings em inglês, estilo Google, como o resto do SDK. Em `aeko/config/`: módulo e funções públicas documentados; **zero** comentários `#` (enforced por `tests/test_code_quality.py`). Funções privadas `_nome` não exigem docstring nesse teste.

```python
def salvage_inventory_payload(answer: str) -> dict | None:
    """
    Read a JSON object from a coordinator answer, without calling a model.

    Args:
        answer: Coordinator output, routing marker already stripped or not.

    Returns:
        dict | None: The object when recovery succeeded, otherwise None.
    """
```

Nomes estáveis das interfaces (tickets paralelas dependem disto):

* `salvage_inventory_payload(answer: str) -> dict | None`
* `canonicalize_inventory_payload(payload: dict, catalogs: AekoInventoryCatalogs) -> dict`
* `recover_extracted_inventory(answer, catalogs, inventory_markdown) -> AekoExtractedInventory`

Constantes: `INVENTORY_SCHEMA_MAX_RETRIES = 2`. Não reutilizar `PLAN_FORMAT_MAX_RETRIES` para schema.

## Testing Strategy

* Framework: pytest, sem rede, via `use_fake_llm` / agentes scriptados.
* Níveis:
  * Unitário por módulo (`test_inventory_salvage.py`, `test_inventory_canonicalize.py`, `test_inventory_fallback.py`, `test_inventory_prompt.py`).
  * Contrato público (`test_inventory_contract.py`, `test_config.py`): `analyze()` feliz, coerce, JSON irrecuperável + schema, id inválido sem schema, chat sem retry de inventory.
  * Regressão: `test_nodes.py` (retry de seções) e testes de chat que já exigem 1 chamada do coordenador.
* Coverage: cada FR das specs de módulo tem pelo menos um teste que falharia se o FR fosse revertido.
* Fake de schema: o fallback injeta um callable `fill_schema`; testes de unidade não precisam de `with_structured_output` real.

## Boundaries

* Always: TDD (teste vermelho antes do código daquela ticket); `python -m pytest` na fatia da ticket; manter retry de seções e contrato de `AekoImprovementPlan`; falha de inventory continua `MalformedAgentOutputError` com `aeko_metrics`.
* Ask first: nova dependência; mudar cap de seções; mudar contrato público de `analyze()`; structured output no caminho feliz (toda análise).
* Never: retry de LLM por id/range/`quantity` inválidos; reescrever seções do plano no fallback; alterar shots de chat do coordenador; commitar secrets; silenciar teste vermelho; inventar valores ausentes do markdown.

## Success Criteria

* SC-001: Caminho feliz (seções + JSON parseável) = exatamente 1 invocação do coordenador no grafo e 0 chamadas de schema.
* SC-002: JSON com tipos string coercíveis / `is_upstream` omitido / opcionais ausentes / `emissions` ausente → sucesso sem schema, payload canônico.
* SC-003: JSON irrecuperável → no máximo 2 invocações extras nomeadas `Coordenador de Melhoria Contínua`; plano das seções aceitas permanece o do grafo.
* SC-004: Após JSON parseável, id fora do catálogo / range / `quantity` inválida → `MalformedAgentOutputError` sem chamada de schema.
* SC-005: `send_message` não emite bloco inventory nem dispara schema.
* SC-006: Suíte atual de seções/retries/ids de plano continua verde.

## Open Questions

Nenhuma ambiguidade crítica. Premissas listadas no topo.
