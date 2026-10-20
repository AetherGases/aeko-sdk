# Spec: prompt-inventory-json

Convenções compartilhadas: `scratch/SPEC.md`.

## Objective

O caminho feliz continua sendo **texto**: 3 seções markdown + bloco ` ```inventory ` JSON. Os shots atuais do coordenador **não** ensinam o bloco (e um shot sem o bloco compete com a instrução). Esta spec aperta só a instrução injetada no fluxo de inventário, com um exemplo concreto.

## Fora de escopo

* Structured output no caminho feliz.
* Mudar `CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC.shots` (chat).
* Canonicalizer e fallback.

## Comportamento atual a preservar

* `_with_inventory_payload_instructions` já anexa `INVENTORY_PAYLOAD_INSTRUCTIONS` quando `catalog_context` existe.
* Chat não tem `catalog_context` → não recebe a instrução.

## Requisitos funcionais

* FR-P01: `INVENTORY_PAYLOAD_INSTRUCTIONS` continua a exigir, após as três seções, **um** fence com linguagem `inventory` e um objeto JSON (não o plano em JSON).
* FR-P02: A instrução contém **um exemplo mínimo** válido (chaves raiz + um item de emissão e o comentário de que redução zera extras). Sem isso o modelo copia os shots só-de-seções.
* FR-P03: A instrução diz para usar só ids dos catálogos, `null` quando o markdown não tiver o dado, e que `is_upstream` deve seguir a classification (o canonicalizer corrige de qualquer forma; o prompt reduz lixo).
* FR-P04: A instrução **não** pede JSON no lugar das seções do plano e **não** pede blocos de código além do fence `inventory`.
* FR-P05: Shots de `CONTINUOUS_IMPROVEMENT_COORDINATOR_SPEC` permanecem só com as 3 seções.
* FR-P06: Testes de chat que leem o system/prompt do coordenador **não** passam a exigir o bloco inventory.

## Cenários e critérios de aceitação

1. Given um run de `analyze()` (há `catalog_context`)
   When o coordenador é invocado
   Then a mensagem humana contém `INVENTORY_PAYLOAD_INSTRUCTIONS` incluindo o fence de exemplo e as chaves `description`, `emissions`
2. Given `send_message` que roteia ao coordenador
   When o coordenador é invocado
   Then a mensagem **não** contém ` ```inventory `
3. Given os shots do spec do agente
   When lidos
   Then nenhum shot contém ` ```inventory `

## Success Criteria

* SC-P01: `tests/test_inventory_prompt.py` (ou equivalente) trava FR-P01–P06 sem chamar `analyze()` e2e.
* SC-P02: `test_the_chat_flow_does_not_retry_the_coordinator` e testes de seções do plano permanecem verdes.

## Dependências

* Nenhuma. Paralelo a `canonicalizer` e aos testes unitários de `fallback-schema`.
