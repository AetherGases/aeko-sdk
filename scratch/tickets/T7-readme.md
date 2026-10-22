# T7 — README do fluxo analítico

- Wave: C (paralelo com T6)
- Spec: `scratch/SPEC.md` SC-001–SC-005 (documentar o comportamento, não implementá-lo)
- Blocked by: T5 (o texto tem de refletir o código ligado)

## Task

Atualizar o README na parte do `analyze()` / formato do coordenador: seções + fence; canonicalização fora da IA; schema só se JSON irrecuperável (cap 2); plano congelado; chat inalterado; custo (1 slow feliz, +2 no pior caso de JSON).

Não alterar AGENT_DOCS nesta ticket (gitignored; nota curta do agente no fim do pedido).

- Acceptance: README não afirma mais que inventory inválido falha sem qualquer tentativa de formato; não descreve structured output no caminho feliz
- Verify: leitura da seção de inventory/report no `README.md`; `python -m pytest tests/test_code_quality.py` (não deve quebrar)
- Files: `README.md`

Não mudar código de runtime.
