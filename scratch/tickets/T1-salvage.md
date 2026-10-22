# T1 — Salvage determinístico do JSON

- Wave: A (paralelo com T2, T3, T4)
- Spec: `scratch/SPEC-canonicalizer.md` FR-C01–C04, FR-C09
- Blocked by: —

## Task

Implementar `salvage_inventory_payload(answer: str) -> dict | None`.

- Acceptance: FR-C01, FR-C02, FR-C03, FR-C04, FR-C09 verdes; não chama LLM; não lê catálogos
- Verify: `python -m pytest tests/test_inventory_salvage.py tests/test_code_quality.py`
- Files: `aeko/config/inventory_salvage.py`, `tests/test_inventory_salvage.py`

Não criar `canonicalize` aqui (T2). Não tocar `inventory.py`.
