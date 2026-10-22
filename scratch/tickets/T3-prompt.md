# T3 — Prompt do bloco inventory

- Wave: A (paralelo com T1, T2, T4)
- Spec: `scratch/SPEC-prompt-inventory-json.md`
- Blocked by: —

## Task

Endurecer `INVENTORY_PAYLOAD_INSTRUCTIONS` com exemplo de fence; não alterar shots.

- Acceptance: FR-P01–P06; chat sem ` ```inventory `; shots sem o bloco; instrução com exemplo mínimo
- Verify: `python -m pytest tests/test_inventory_prompt.py tests/test_nodes.py tests/test_config.py -k "chat_flow_does_not_retry or coordinator"`
- Files: `aeko/engine/prompts/continuous_improvement_coordinator.py`, `tests/test_inventory_prompt.py`

Não alterar o grafo nem `analyze()`.
