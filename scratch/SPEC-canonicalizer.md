# Spec: canonicalizer

Convenções compartilhadas: `scratch/SPEC.md`.

## Objective

Passo **determinístico** (zero LLM) que (1) extrai um objeto JSON do texto do Coordenador e (2) o deixa no formato do contrato `AekoExtractedInventory`, sem inventar ids nem quantidades.

Consumidor: `recover_extracted_inventory` / `analyze()`. Pode ser testado e enviado sem o fallback de schema.

## Fora de escopo

* Chamadas de modelo, métricas, prompts, grafo.
* Validação de catálogo/range (isso é Pydantic **depois** da canonicalização; falha sem retry — ver `fallback-schema`).

## Interfaces

```python
def salvage_inventory_payload(answer: str) -> dict | None: ...
def canonicalize_inventory_payload(
    payload: dict,
    catalogs: AekoInventoryCatalogs,
) -> dict: ...
```

`salvage` não recebe catálogos. `canonicalize` não chama LLM e não levanta `MalformedAgentOutputError` por FK — só normaliza shape/tipos deriváveis.

## Requisitos funcionais

* FR-C01: Procurar primeiro um fence ` ```inventory `. O conteúdo interno passa pelo reparo de serialização e `json.loads`.
* FR-C02: Sem fence, procurar **um** objeto JSON **depois da última seção do plano** (`## Raciocínio` / `PLAN_SECTIONS["reasoning"]`, comparação igual a `parse_sections`: acento/caixa ignorados). Não vasculhar o corpo das seções.
* FR-C03: Reparo de serialização permitido **somente**: vírgula final antes de `}`/`]`, literais Python `None`/`True`/`False`. Nada de aspas simples, regex no documento inteiro, nem vários objetos.
* FR-C04: Sucesso de salvage → `dict` com raiz objeto. Array, scalar, JSON irrecuperável ou objeto só dentro do plano → `None`.
* FR-C05: Canonicalize coerção: strings numéricas de campos numéricos (`"1"` → `1`, `"12.5"` → `12.5`); `"true"`/`"false"`/`"null"` (casefold) nos booleanos/opcionais adequados; `None` Python já é `null`.
* FR-C06: Campos opcionais ausentes (`description`, `start_period`, `end_period`, e nos itens os opcionais do contrato) → `None`. `emissions` ausente → `[]`.
* FR-C07: `is_upstream` é **sempre** derivado da `classification` da categoria do item quando `is_reduction` é falso: `UPSTREAM` → `True`, `DOWNSTREAM` → `False`, classificação nula ou categoria ausente no catálogo → `None`. O valor que o modelo escreveu é ignorado. Em redução (`is_reduction` verdadeiro), `is_upstream` fica `None`.
* FR-C08: Chaves que não pertencem ao contrato são descartadas (objeto raiz e cada item de `emissions`). Não se completam `gas`/`scope`/`quantity_co2e` ausentes.
* FR-C09: Marker `Next agent:` não impede o salvage (strip ou busca após seções, alinhado a `_plan_sections_in`).

## Cenários e critérios de aceitação

### Fence válido

1. Given resposta com 3 seções e ` ```inventory ` contendo JSON objeto
   When `salvage_inventory_payload`
   Then devolve o objeto parseado

### JSON depois da última seção

1. Given seções válidas, sem fence, e um `{...}` após a última seção
   When salvage
   Then devolve esse objeto
2. Given um `{` apenas dentro de `## Método`
   When salvage
   Then `None`

### Reparo mínimo

1. Given fence com vírgula final ou `None`/`True`/`False`
   When salvage
   Then parseia
2. Given aspas simples ou texto que não é JSON
   When salvage
   Then `None`

### Canonicalização

1. Given `{"emissions":[{"quantity_co2e":"10","gas":"1","scope":"1","category":"1","is_reduction":"false"}]}` e categoria com `classification` nula
   When canonicalize
   Then tipos numéricos/bool corretos, `is_upstream is None`, opcionais preenchidos com `None`
2. Given objeto sem `emissions`
   When canonicalize
   Then `emissions == []`
3. Given item `is_reduction true` com `is_upstream true` e `gas` 1
   When canonicalize
   Then `is_upstream is None`; **não** apaga `gas` (isso é erro semântico posterior, não formato)
4. Given chave extra `id_inventory`
   When canonicalize
   Then a chave não aparece no dict resultante

## Success Criteria

* SC-C01: Funções puras, determinísticas, sem I/O nem LLM.
* SC-C02: Cada FR-C0x tem teste unitário em `tests/test_inventory_salvage.py` ou `tests/test_inventory_canonicalize.py`.

## Dependências

* `PLAN_SECTIONS` e `AekoInventoryCatalogs` existentes.
* Não depende de `prompt-inventory-json` nem de `fallback-schema`.
