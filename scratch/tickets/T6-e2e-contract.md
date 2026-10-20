# T6 — Testes de contrato de analyze()

- Wave: C (paralelo com T7)
- Spec: `scratch/SPEC.md` SC-001–SC-006 e cenários de `SPEC-fallback-schema.md`
- Blocked by: T5

## Task

Estender `tests/test_inventory_contract.py` e, se preciso, `tests/test_config.py` / `tests/test_event_tracking.py`:

* coerce sem schema extra
* JSON irrecuperável + 1 e 2 schema (fake)
* 2 schema falhas → `MalformedAgentOutputError`
* id inválido em JSON parseável → 0 schema
* schema devolve id inválido → sem 2ª chamada
* chat sem schema

O fake LLM do grafo não cobre `fill_schema` fora do grafo: o teste precisa de um fake no callable de produção (patch do ponto de `fill_schema`, não de todo o Gemini).

- Acceptance: cada cenário da spec de fallback tem teste; regressão de seções/retries verde
- Verify: `python -m pytest tests/test_inventory_contract.py tests/test_config.py tests/test_event_tracking.py tests/test_nodes.py`
- Files: `tests/test_inventory_contract.py`, `tests/test_config.py`, `tests/test_event_tracking.py` (somente acréscimos)

Não mudar prompts (T3) nem README (T7).
