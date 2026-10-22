# T2 — Canonicalização determinística

- Wave: A (paralelo com T1, T3, T4)
- Spec: `scratch/SPEC-canonicalizer.md` FR-C05–C08
- Blocked by: —

## Task

Implementar `canonicalize_inventory_payload(payload: dict, catalogs: AekoInventoryCatalogs) -> dict`.

- Acceptance: coerção de tipos, `emissions` default `[]`, opcionais `None`, `is_upstream` derivado, chaves extra removidas; não valida FK (não levanta `MalformedAgentOutputError`)
- Verify: `python -m pytest tests/test_inventory_canonicalize.py tests/test_code_quality.py`
- Files: `aeko/config/inventory_canonicalize.py`, `tests/test_inventory_canonicalize.py`

Não implementar salvage. Não tocar prompts nem `analyze()`.
