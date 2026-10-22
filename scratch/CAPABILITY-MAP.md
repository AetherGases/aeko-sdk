# Capability Map: Formato do inventory no Coordenador (`analyze`)

Índice das specs desta demanda. Cada módulo tem consumidor e suíte próprios; um pode ser cortado sem reescrever os requisitos dos outros.

| Module id | Responsibility | Depends on |
|---|---|---|
| canonicalizer | Recuperar JSON do texto do coordenador e canonicalizar tipos/`null`/`is_upstream` sem LLM | — |
| prompt-inventory-json | Instrução + exemplo do bloco JSON só no fluxo de inventário; chat intacto | — |
| fallback-schema | Até 2 chamadas de schema quando o JSON é irrecuperável; métricas; orquestração em `analyze()` | canonicalizer |

Build order: `canonicalizer` + `prompt-inventory-json` em paralelo → `fallback-schema`.

Specs:

* `scratch/SPEC.md` — convenções compartilhadas (stack, comandos, estilo, testes, limites)
* `scratch/SPEC-canonicalizer.md`
* `scratch/SPEC-prompt-inventory-json.md`
* `scratch/SPEC-fallback-schema.md`

Tickets: `scratch/tickets/` (ondas em `scratch/tickets/README.md`).
