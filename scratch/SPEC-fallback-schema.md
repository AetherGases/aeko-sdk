# Spec: fallback-schema

Convenções compartilhadas: `scratch/SPEC.md`. Depende da interface de `canonicalizer`, não da implementação.

## Objective

Quando `salvage_inventory_payload` devolve `None`, obter o `inventory` com até **2** invocações de schema (payload apenas). O plano já aceito pelo retry de seções **congela**. Fora disso, o pipeline só canonicaliza + valida; não chama modelo.

Orquestração observável: `analyze()` depois do grafo.

## Fora de escopo

* Alterar `PLAN_FORMAT_MAX_RETRIES` ou `_format_problems_in`.
* Structured output em toda análise (caminho feliz).
* Reescrita das seções do plano.

## Interfaces

```python
INVENTORY_SCHEMA_MAX_RETRIES = 2


def recover_extracted_inventory(
    answer: str,
    catalogs: AekoInventoryCatalogs,
    inventory_markdown: str,
    *,
    fill_schema: Callable[..., dict] | None = None,
) -> AekoExtractedInventory: ...
```

`fill_schema` é injetável nos testes. Em produção preenche o schema com o LLM slow (mesmo runtime do coordenador), **sem** o JSON quebrado e **sem** o texto do plano.

## Requisitos funcionais

* FR-F01: Se salvage devolve `dict`, **não** chamar schema. Canonicalizar → `AekoExtractedInventory.model_validate(..., context=catálogos)`. Falha de validação → `MalformedAgentOutputError` com `aeko_metrics` (quando houver run).
* FR-F02: Se salvage devolve `None`, chamar `fill_schema` até `INVENTORY_SCHEMA_MAX_RETRIES` vezes, parando no primeiro `dict` raiz objeto. Cada tentativa conta como invocação do agente `Coordenador de Melhoria Contínua` em `used_agents`.
* FR-F03: Input de cada `fill_schema`: markdown do inventário da chamada `analyze()` + catálogos renderizados + schema do payload. Proibido: resposta anterior do coordenador, JSON quebrado, seções do plano.
* FR-F04: `fill_schema` que lança, devolve não-dict, ou JSON irrecuperável consome 1 tentativa; segue a próxima até o cap.
* FR-F05: Primeiro dict obtido via schema passa pelo **mesmo** canonicalize + validate de FR-F01. Validação semântica falha **sem** nova chamada de schema (o JSON já era recuperável).
* FR-F06: Esgotadas as tentativas sem dict → `MalformedAgentOutputError` (inventory ausente/malformado), sem devolver `AekoAnalysisResponse`.
* FR-F07: `analyze()` usa o `plan` parseado da resposta do grafo (já sujeito ao retry de seções) e o `inventory` de `recover_extracted_inventory`. Não reescreve `messages` do grafo. Plano inválido após o cap de seções falha **antes** de schema (comportamento atual).
* FR-F08: Caminho feliz: 0 schema. Pior caso de formato de inventory: 2 schema, além das invocações de seções já feitas no nó.
* FR-F09: Chat não chama `recover_extracted_inventory`.
* FR-F10: `fill_schema` de produção usa geração constrita ao schema (não um segundo loop de prosa/fence). Detalhe de biblioteca é livre desde que FR-F02–F05 se cumpram.

## Cenários e critérios de aceitação

### Feliz / coerce

1. Given seções + fence JSON válido
   When `analyze()`
   Then `plan` e `inventory` preenchidos, `_coordinator_calls` do fake LLM do grafo == 1, e `used_agents` **não** ganha entradas extras de schema
2. Given fence com `"gas": "1"` e `is_upstream` divergente da classification
   When `analyze()`
   Then sucesso, `gas == 1`, `is_upstream` derivado, 0 schema

### Schema só se irrecuperável

1. Given seções válidas e sem JSON recuperável, e `fill_schema` devolve um objeto válido na 1ª chamada
   When `analyze()`
   Then sucesso; exatamente 1 entrada extra `Coordenador de Melhoria Contínua` em `used_agents`; `plan` igual ao texto das seções do grafo
2. Given a 1ª `fill_schema` falha e a 2ª devolve objeto válido
   When `analyze()`
   Then sucesso; 2 entradas extras
3. Given 2 `fill_schema` falhas
   When `analyze()`
   Then `MalformedAgentOutputError`; 2 entradas extras; sem `AekoAnalysisResponse`

### Semântico sem schema

1. Given JSON parseável com `gas` id inexistente
   When `analyze()`
   Then `MalformedAgentOutputError` e 0 schema
2. Given salvage `None` e 1ª schema devolve JSON parseável com id inexistente
   When `analyze()`
   Then `MalformedAgentOutputError` e **não** há 2ª schema

### Isolamento

1. Given `send_message`
   When o coordenador responde sem seções
   Then 1 chamada, sem schema, comportamento de chat inalterado

## Success Criteria

* SC-F01: Testes de `recover_extracted_inventory` com `fill_schema` mockado cobrem FR-F01–F06 sem grafo.
* SC-F02: Testes de `analyze()` cobrem os cenários acima e mantêm `test_only_the_coordinator_answers_again_on_a_retry` (analistas não rodam de novo no retry de **seções**).

## Dependências

* Interfaces `salvage_inventory_payload` e `canonicalize_inventory_payload`.
* `AekoExtractedInventory`, catálogos, `MalformedAgentOutputError`, `agent_call`.
