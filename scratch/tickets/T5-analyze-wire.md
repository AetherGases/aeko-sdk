# T5 — Ligar o pipeline em analyze()

- Wave: B
- Spec: `scratch/SPEC-fallback-schema.md` FR-F07–F09, FR-F02 (métricas)
- Blocked by: T1, T2, T4 (T3 pode mergear depois; não bloqueia a fiação)

## Task

`analyze()`: plano como hoje (`_to_improvement_plan`); `inventory` via `recover_extracted_inventory`; cada `fill_schema` de produção envolvida em `agent_call("Coordenador de Melhoria Contínua")` com o LLM slow / schema constrito.

Não alterar `_coordenador_melhoria_node` nem `PLAN_FORMAT_MAX_RETRIES`. Plano inválido após cap de seções continua falhando antes de schema.

- Acceptance: caminho feliz 0 schema; `used_agents` ganha 1–2 entradas extras só quando salvage é None; chat intocado
- Verify: `python -m pytest tests/test_config.py tests/test_inventory_contract.py tests/test_nodes.py tests/test_inventory_fallback.py`
- Files: `aeko/config/inventory.py`, `aeko/config/inventory_fallback.py` (produção de `fill_schema` se ainda não estiver)

Não reescrever README (T7). Casos e2e novos que falharem por cobertura incompleta são T6 — T5 deve manter a suíte **já existente** verde.
