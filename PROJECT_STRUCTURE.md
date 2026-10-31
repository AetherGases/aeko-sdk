# Project structure and practices

How this repository is laid out, and the rules every change must follow.
Consumer-facing behaviour lives in [README.md](README.md). This file is for
people (and agents) who change the SDK.

Python **3.11 only** (`requires-python = ">=3.11,<3.12"`). The public surface is
a **library**, not a service: no HTTP server, no database, no environment-variable
fallback for the API key.

---

## 1. Layout

```text
aeko/                      # installable package
  __init__.py              # public facade + __version__
  config/                  # public API: Aeko, messenger, inventory, DTOs, errors
  engine/                  # LangChain / LangGraph runtime, agents, prompts, graph
  shared/                  # logging, metrics, text protocol — depends on nothing above
  side_content/            # conversation summary, outside the graph
tests/                     # pytest suite (fake LLM only)
scratch/                   # working specs and tickets; not shipped
.github/                   # CI and publish workflows
```

| Package | Owns | May import |
| --- | --- | --- |
| `aeko.shared` | Logs, `processing()`, `agent_call()`, routing-marker helpers, personal data masking | nothing from `config` or `engine` |
| `aeko.engine` | Runtime, LLMs, agents, prompts, graph | `shared` only at module load |
| `aeko.config` | Facade the API consumes | `engine` and `shared` |
| `aeko.side_content` | Summariser | `engine` / `shared` as needed |

`aeko.config` is the public facade built **on top of** the engine. The engine
must not import `aeko.config` at module load time (cycles). Lazy imports inside
methods (`RUNTIME.require_api_key`, `RUNTIME.agents_for`) are the allowed
exception. `TYPE_CHECKING` blocks are the other.

Constants live in the layer that owns them:

- Graph node names and retry caps → `aeko.engine.graph.constants` (including
  `INVENTORY_ENTRY_POINT`).
- Facade bookkeeping → `aeko.config.constants`.
- Logging / routing-marker strings → `aeko.shared.constants`.

Do not duplicate a constant across layers. `analyze()` and the graph must
compare the **same** `INVENTORY_ENTRY_POINT`.

---

## 2. Public API

Only names in `aeko.__all__` (re-exported from `aeko.config` / `aeko.shared`)
are public. Everything else is internal, including
`recover_extracted_inventory`, salvage/canonicalize helpers, graph nodes, and
prompt specs.

Adding a public name means: export it from `aeko.config.__init__` (or
`aeko.shared`), re-export from `aeko/__init__.py`, document it in the README
API reference, and treat the change as breaking if you rename or remove it.

DTOs that mirror Mongo collections keep `_id` via `populate_by_name` /
`model_dump(by_alias=True)`. Identifiers and `expires_at` never reach a prompt
(`LOG_ONLY_FIELDS`). The SDK never talks to Mongo or Postgres; the embedding
API reads and writes.

---

## 3. Source style (enforced)

`tests/test_code_quality.py` walks **every** `*.py` under `aeko/` and `tests/`:

1. Every **module** has a docstring.
2. Every **function** has a docstring, including private names (`_foo`) and
   tests.
3. **Zero `#` comments** — no `# noqa`, no `# type: ignore`, no inline
   commentary. Put the why in the docstring.

Docstrings are **English**, Google style: short summary, then `Args` / `Returns`
/ `Raises` when the function takes, returns, or raises something a caller must
know. Match the surrounding module; do not invent a second voice.

Agent **display names** are Portuguese and are routing keys. Spell them exactly
as in `AGENT_NAMES`, accents included, including the historical
`Análista de inventários`.

---

## 4. Runtime and configuration

- One process-wide `RUNTIME`. `Aeko.config()` and `AekoMessenger.set_tools()`
  write it at **startup**, never per request. Any write drops the agent
  registry so the next run rebuilds it.
- No `GEMINI_API_KEY` / `.env` fallback. Missing key → `AekoNotConfiguredError`.
- Fast agents (router, FAQ, orchestrator, both reviewers) use `fast_model`;
  specialists use `slow_model`. Each LLM is registered with the other as
  `with_fallbacks`. The reviewers (`REVIEWER_AGENTS`) get their own fast pair
  at temperature 0; every other agent keeps the configured temperature.
- Chat runs use `max_tokens`; `analyze()` uses `report_max_tokens`.
- Tools are declared once: description in the prompt **and** bound on the
  executor. Unknown agent keys → `UnknownAgentError`.

---

## 5. Agent graph protocol

Handoff is **prose**, not provider structured-output on `create_agent`:

- Every agent ends with `Next agent: <name>` or `Next agent: Nenhum`.
- `_invoke_agent` reads that line (`next_agent_name`); user-facing text is
  stripped (`strip_routing_marker`).
- Do not add `response_format=` to `create_agent` unless a dedicated design
  says so. LangChain supports it; this SDK’s routing, prompts, wrapper and
  tests still assume a string. Native Gemini schema on agent creation is
  **out of scope** until that design exists.

Conversational flow: `Guardrail de Entrada` (`INPUT_GUARDRAIL`) → `Roteador`
→ specialists / FAQ → `Orquestrador` → `Guardrail de Saída` →
`Verificador de Resposta` → `Consolidador`. The FAQ ends at `Consolidador`
too. `Guardrail de Entrada` masks CPF, CNPJ, card and phone numbers in the
question and history (`aeko.shared.personal_data`), keeping the mapping in
the `personal_data` state field; agents only ever see placeholders such as
`[CPF_1]`. `Consolidador` puts the real values back into the delivered answer
(same message id, never a second message) and into staged write arguments.
User-facing prompts never name agents or routing, and contain no em or en
dash. Each reviewer may send the
turn back at most `GUARD_RAIL_MAX_RETRIES` / `RESPONSE_CHECK_MAX_RETRIES`
(2) times. Exhaustion → `MalformedAgentOutputError`, no empty `output`.

Report flow: enters at `INVENTORY_ENTRY_POINT`, never reaches the reviewers,
ends at `Coordenador de Melhoria Contínua`.

Writing tools (`metadata["is_writing"] is True`) do not write mid-run: inside a
bound run each call is staged on `Processing.pending_writes` and answers
`{"staged": true}`. `Consolidador` (`CONSOLIDATOR`) commits them, and only
after an approved `Verificador de Resposta` or the report flow's coordinator.
Retry-cap endings never commit; the FAQ has no writing tools, so its ending
commits nothing. A write that still fails after one
retry sets the run's error description; in chat it also appends the tool's
`metadata["write_failure_message"]` to the answer. The report is never altered.

Plan format retries (`PLAN_FORMAT_MAX_RETRIES = 4`) happen **inside the
coordinator node** and only re-ask that agent.

---

## 6. Inventory recovery (`analyze()`)

Order is fixed:

1. Run the graph; parse the **plan** from `## Problema definido` / `## Método`
   / `## Raciocínio` (`PLAN_SECTIONS`). Missing sections → rewrite in-graph,
   then `MalformedAgentOutputError`. Do not invent plan fields.
2. **Salvage** JSON: ` ```inventory ` fence first; if absent, a single object
   after Raciocínio (FR-C02). JSON in other sections is not a payload.
3. Strip recovered JSON from the plan text so it cannot leak into
   `reasoning`.
4. **Canonicalize** types/nulls; derive `is_upstream` from catalogs. Do not
   invent missing `gas` / `scope` / `quantity_co2e`.
5. Validate against catalogs (`AekoExtractedInventory`). Semantic failures
   (unknown id, range, shape) **do not** trigger schema fill.
6. If salvage yields nothing, `fill_inventory_schema` (delegates to
   `_fill_inventory_from_schema`) may run up to `INVENTORY_SCHEMA_MAX_RETRIES`
   (2). The plan stays frozen. Fill exceptions are retried; the final error
   is always `MalformedAgentOutputError`, with `__cause__` chained when a
   fill attempt raised.

`analyze()` still injects `_fill_inventory_from_schema` so contract tests can
patch that name. Chat (`send_message`) never runs salvage or schema fill.

Do not pad a malformed plan or inventory with guessed values.

---

## 7. Logging and metrics

- Named logger `aeko`, never the root logger.
- Prefix `[aeko-sdk]`. Never log the API key.
- Wrap entry points in `processing(Flow, module, id_request)`.
- Every LLM/agent invoke goes through `agent_call` so `AekoMetrics.used_agents`
  is complete.
- Failed requests attach metrics on the exception (`exc.aeko_metrics` /
  `METRICS_ATTR`).

---

## 8. Tests (TDD)

- Tests are written **before** or with the production change; the suite must
  match when the task is done.
- **No Gemini.** `tests/conftest.py` states it: scripted fakes only, no
  credentials. Patch at the helper the production path actually calls
  (`_fill_inventory_from_schema`, `create_llms` inside inventory, or
  `RUNTIME.agents`), never a live socket.
- `reset_aeko` runs autouse so config/tools do not leak between tests.
- Ask which file under `tests/` to run after a change; run it before claiming
  done.
- `python -m pytest` is what CI runs (`.github/workflows/ci.yml`).

`requirements.txt` pins the versions the suite is green against;
`pyproject.toml` uses ranges for consumers.

---

## 9. Versioning and release

- Version is `aeko.__version__` in `aeko/__init__.py` (Hatch reads it).
- GitHub Release tag must match that string or publish fails.
- Python 3.11 on CI. Do not claim support for 3.12+.

---

## 10. Documentation

- **README.md** — how to consume the SDK (API, integration, migrations).
- **PROJECT_STRUCTURE.md** — this file: how to change it.
- **scratch/** — specs and tickets; not a substitute for tests or README.
- Do not commit secrets. Do not document the API key in examples as a real
  key.

---

## 11. Hard “don’ts”

- Do not import `aeko.config` from `aeko.engine` at module load.
- Do not import `aeko.engine` or `aeko.config` from `aeko.shared`.
- Do not duplicate `INVENTORY_ENTRY_POINT` (or other graph names) in
  `aeko.config.constants`.
- Do not add `#` comments to pass a type-checker; the quality test will fail.
- Do not hit Gemini in tests.
- Do not invent inventory or plan fields the model omitted.
- Do not wire `create_agent(..., response_format=...)` without a new design.
  The fill path already uses `with_structured_output` for unsalvageable JSON
  only.
- Do not configure the root logger.
- Do not commit unless the user asked.
