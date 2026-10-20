# T4 — recover_extracted_inventory (schema com mocks)

- Wave: A (paralelo com T1, T2, T3)
- Spec: `scratch/SPEC-fallback-schema.md` FR-F01–F06, FR-F10
- Blocked by: assinaturas em `SPEC-canonicalizer.md` (não o código de T1/T2)

## Task

Implementar `recover_extracted_inventory` e `INVENTORY_SCHEMA_MAX_RETRIES = 2`, injetando `salvage`, `canonicalize` e `fill_schema` nos testes.

No código de produção, importar as funções reais de T1/T2 **por nome estável**. Se T1/T2 ainda não existirem na branch local, usar stubs mínimos que os testes de T4 substituem via kwarg.

- Acceptance: salvage dict → 0 `fill_schema`; salvage None → cap 2; semântico após dict não chama schema de novo; erro vira `MalformedAgentOutputError`
- Verify: `python -m pytest tests/test_inventory_fallback.py tests/test_code_quality.py`
- Files: `aeko/config/inventory_fallback.py`, `tests/test_inventory_fallback.py`

Não ligar `analyze()` (T5). Contagem em `used_agents` pode ficar como TODO de T5 se `agent_call` exigir run ativo; neste caso o teste de T4 verifica o número de vezes que `fill_schema` foi chamado.
