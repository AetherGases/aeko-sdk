# aeko

**The intelligent core of the Aether ecosystem** — a multi-agent system that reads a
company's greenhouse gas (GHG) inventory, explains it, and turns it into a concrete
improvement plan.

Aether helps industrial companies cut their greenhouse gas emissions. Their employees
fill in the company's GHG inventory; Aeko is what reads that inventory and answers with
pollutant analysis, green gas alternatives and a prioritized improvement plan — plus a
chatbot for the day-to-day ESG questions that come up along the way.

This package is the reusable engine behind that product. It is a **library, not a
service**: it exposes a small facade over a LangChain/LangGraph agent graph so any
backend — an API, a worker, a notebook — can embed it.

> 🇧🇷 Um resumo em português está no final deste documento: [Resumo em português](#resumo-em-português).

---

## Table of contents

- [What the SDK does](#what-the-sdk-does)
- [The agent system](#the-agent-system)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [Integrating into other systems](#integrating-into-other-systems)
  - [Design rules that shape the integration](#design-rules-that-shape-the-integration)
  - [1. Configure once, at startup](#1-configure-once-at-startup)
  - [2. Register your tools](#2-register-your-tools)
  - [3. The conversational flow](#3-the-conversational-flow)
  - [4. The inventory flow](#4-the-inventory-flow)
  - [5. Full example: a stateless FastAPI service](#5-full-example-a-stateless-fastapi-service)
  - [6. Error handling](#6-error-handling)
- [API reference](#api-reference)
- [Development](#development)
- [Resumo em português](#resumo-em-português)
- [License](#license)

---

## What the SDK does

Aeko exposes two independent entry points into the same agent graph:

| Entry point | Class | Use it for |
| --- | --- | --- |
| **Conversation** | `AekoMessenger` | The ESG chatbot: a user message in, a reviewed answer out, with session memory. |
| **Inventory report** | `AekoInventoryAnalyzer` | A GHG inventory in, a full improvement plan out. |

Both share the same agents, the same registered tools and the same configuration. The
difference is where the run enters the graph and how it ends.

## The agent system

Eight agents, each with its own prompt, persona and tools. Names are in Portuguese
because they are also the routing keys the graph and `set_tools()` use — pass them
exactly as written.

| Agent | Role | Model |
| --- | --- | --- |
| `Roteador` | First-touch triage; classifies intent and hands off. | fast |
| `FAQ` | Answers institutional/conceptual questions directly. | fast |
| `Orquestrador` | Consolidates the specialists' output and replies to the user. | fast |
| `Guardrail de Saída` | Reviews the consolidated answer before it can leave. | fast |
| `Análista de inventários` | Reads the GHG inventory itself. | slow |
| `Analista de Poluentes` | Pollutant analysis. | slow |
| `Analista de Gases Verdes` | Green gas alternatives. | slow |
| `Coordenador de Melhoria Contínua` | Writes the improvement plan. | slow |

The four cheap agents (classify, consolidate, review) run on `fast_model`; the four
specialist analysts run on `slow_model`. Both are configurable.

**Conversational flow** — enters at the router, always passes the guardrail:

```
send_message()
      │
      ▼
  Roteador ──────────────────────────▶ FAQ ─────────────────────────▶ answer
      │
      ├──▶ Analista de Poluentes ─────┐
      ├──▶ Analista de Gases Verdes ──┼──▶ Orquestrador ──▶ Guardrail de Saída
      │                               │                            │
      └──▶ Coordenador de Melhoria ───┴───────────▶ answer   approved? ──▶ answer
                                                              │
                                                       rejected (up to 3x)
                                                              │
                                                              └──▶ back to Roteador
```

If the guardrail keeps rejecting past the retry cap, the run ends with **no** answer:
`MessageResponse.message.output` is `""` and `approved` is `False`. Always check it.

**Inventory flow** — enters at the inventory analyst, ends at a terminal node, so it
never passes the guardrail:

```
analyze()
      │
      ▼
  Análista de inventários ──▶ Analista de Poluentes ⇄ Analista de Gases Verdes
                                                              │
                                                              ▼
                                        Coordenador de Melhoria Contínua ──▶ plan
```

## Requirements

- **Python 3.11** (`>=3.11,<3.12`)
- A **Google Gemini API key**

Runtime dependencies (`langchain-core`, `pydantic`, `langchain-classic`, `langgraph`,
`langchain-google-genai`) are installed with the package.

## Installation

```bash
pip install aeko
```

Distribution and import name are the same: `aeko`. (The GitHub repository is `aeko-sdk`.)

## Quickstart

```python
from aeko import Aeko, AekoMessenger, AekoInventoryAnalyzer, Session, User

# 1. Configure the SDK once, for the whole process.
Aeko.config("YOUR_GEMINI_API_KEY")

# 2. Chat. Both arguments are the documents your database already holds.
messenger = AekoMessenger()
messenger.prepare(
    Session.model_validate(db.sessions.find_one({"_id": session_id})),
    User.model_validate(db.users.find_one({"_id": user_id})),
)

reply = messenger.send_message("What is the difference between scope 1 and scope 2?")
print(reply.message.output)  # the answer
print(reply.agents_called)   # e.g. ['FAQ']
print(reply.approved)        # guardrail verdict

# `reply.message` is one entry of `session.messages`, ready to append.
db.sessions.update_one(
    {"_id": session_id}, {"$push": {"messages": reply.message.model_dump()}}
)

# 3. Analyze an inventory.
analyzer = AekoInventoryAnalyzer()
analyzer.set_context("Last report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

plan = analyzer.analyze(inventory_markdown, id_external_inventory=502)
db.improvement_plans.insert_one(plan.model_dump(by_alias=True, exclude={"id"}))
```

---

## Integrating into other systems

### Design rules that shape the integration

Three deliberate design choices decide how you wire Aeko into your backend. Read these
before writing the integration — they explain every recommendation below.

1. **The SDK never reads the environment.** There is no `GEMINI_API_KEY` fallback, no
   `.env` loading. Your application owns its configuration and passes it in through
   `Aeko.config()`. Nothing works until it does.
2. **Configuration and tools are process-wide.** `Aeko.config()` and
   `AekoMessenger.set_tools()` mutate a single process-wide runtime and rebuild every
   agent. Call them **at startup**, not per request. (`set_tools` is a `classmethod` for
   exactly this reason: registering tools on one instance would silently rebuild the
   agents behind every other instance's back.)
3. **Session memory lives in the process.** `AekoMessenger` keeps conversation history in
   a process-wide dict keyed by `session_id`. That is fine for a single worker, and *not*
   enough for anything else — see the stateless pattern below.

### 1. Configure once, at startup

```python
from aeko import Aeko

Aeko.config(
    api_key=settings.GEMINI_API_KEY,
    fast_model="gemini-3.1-flash-lite",     # router, FAQ, orchestrator, guardrail
    slow_model="gemini-3.5-flash",          # the four specialist analysts
    max_tokens=1024,                        # output cap for chat turns
    report_max_tokens=8192,                 # output cap for the inventory report
)
```

Every keyword argument is optional and falls back to the default shown. Calling
`Aeko.config()` again applies the change immediately — the agents and the compiled graph
are rebuilt on the next run. `Aeko.is_configured()` tells you whether a key was supplied
(useful in a health check); `Aeko.reset()` restores every default and clears registered
tools.

### 2. Register your tools

Tools are **your** application's code — a price lookup, an emission-factor table, a search
over your own documents. Aeko binds them to the agents you choose.

```python
from langchain_core.tools import tool
from aeko import AekoMessenger, AekoTool

@tool
def consulta_precos(equipamento: str) -> str:
    """Return the average market price for a piece of equipment."""
    return price_service.lookup(equipamento)

AekoMessenger.set_tools({
    "Coordenador de Melhoria Contínua": [
        AekoTool(
            tool=consulta_precos,
            description="Use to price the equipment you recommend, in BRL.",
        ),
    ],
    # A bare LangChain tool works too — its own docstring becomes the description.
    "FAQ": [buscar_norma],
})
```

Each tool is used **twice, from a single declaration**: its description is rendered into
that agent's `# Ferramentas Disponiveis` prompt section, and the tool object itself is
bound to that agent's executor. The prompt therefore can never advertise a tool the agent
is unable to call.

Keys must be agent names from the table above; anything else raises `UnknownAgentError`,
which carries the valid names in `.known_agents`. `AGENT_NAMES` is exported for
validating input before you get there:

```python
from aeko import AGENT_NAMES  # ('Roteador', 'FAQ', 'Orquestrador', ...)
```

### 3. The conversational flow

```python
from aeko import AekoMessenger, Message, Session, User

messenger = AekoMessenger()

session = messenger.prepare(
    Session(
        id="64b8f0a1c9e1a2b3c4d5e6f3",
        id_user="64b8f0a1c9e1a2b3c4d5e6f1",
        name="Scope 1 review",
        messages=[Message(input="What is scope 3?", output="Scope 3 covers...")],
    ),
    User(id_external_user=1001, role="ESG analyst",
         usecase="Tracks the boiler fleet's gas substitution."),
)

response = messenger.send_message("Our scope 1 jumped 12% this quarter. Where do I look?")
```

`prepare()` takes the two documents your database already holds, so there is no separate
"history" format to translate: `session.messages` **is** the conversation, replayed to the
agents oldest first. Nothing is kept between calls — pass the session on every request and
any worker can serve it.

`user.role` and `user.usecase` become the business context every agent reads. The
identifiers (`_id`, `id_external_user`, `id_user`) never reach a prompt: they are there so
you can correlate documents, and a model can do nothing with them.

`send_message()` returns a `MessageResponse`:

| Field | Meaning |
| --- | --- |
| `message` | The turn, as one entry of `session.messages` — ready to append. |
| `id_session` / `id_user` | The `_id`s of the session and user this answer belongs to, echoed back from `prepare()`. |
| `agents_called` | Names of the agents that contributed, in call order. |
| `approved` | Whether the output guardrail approved the answer. |
| `guardrail_retries` | How many times the guardrail sent the draft back. |

Only `message` belongs in the database; everything else describes *how* the run reached
the answer, or which documents it belongs to, and is deliberately not part of the
persisted entry — `session.messages[]` carries no identifiers of its own. The identifiers
are still returned so a response is enough on its own to file and log the answer against
the right conversation. The turn itself mirrors the collection exactly:

| Field | Meaning |
| --- | --- |
| `input` | What the user sent. |
| `output` | The final user-facing text, already stripped of the agents' internal routing markers. **Empty when the guardrail never approved a draft.** |
| `submitted_at` | When the turn was answered (UTC). |
| `llm` | The model(s) that served the turn, as reported by the provider — comma-separated when a turn crossed both, which it does whenever a specialist analyst was called. |
| `input_tokens` / `output_tokens` | What the whole run consumed, summed across every agent it called. |

A single turn crosses both configured models — the router, FAQ, orchestrator and guardrail
run on the fast one, the analysts on the slow one — so `input_tokens` and `output_tokens`
are the **whole run's** consumption, summed across every agent it called, not one model
call's. That is the number that answers "what did this conversation cost".

An answered turn is also appended to the `Session` you passed in, and `updated_at` is
bumped, so a second `send_message()` in the same process sees it. A turn the guardrail
rejected is *not* appended — a draft that never reached the user cannot become context for
the next question.

Calling `send_message()` before `prepare()` raises `SessionNotPreparedError`.

**User memories.** The SDK never receives `user_memory` documents: reading and expiring
them is your API's job. Register a lookup tool instead, and the agents' instructions
already tell them to consult it:

```python
AekoMessenger.set_tools({"FAQ": [AekoTool(tool=buscar_memorias,
                                          description="Consulta as memórias do usuário.")]})
```

`UserMemory.to_prompt_line()` renders a memory the way those instructions expect
(`"<field>: <description>"`), with `expires_at` and the identifiers left out.

### 4. The inventory flow

```python
from aeko import AekoInventoryAnalyzer

analyzer = AekoInventoryAnalyzer()

# Optional: a company's first report legitimately has no previous one.
analyzer.set_context("2025 report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

plan = analyzer.analyze(inventory_markdown, id_external_inventory=502)
```

`analyze()` expects the inventory **rendered as Markdown** — a table is the natural shape.
It runs with `report_max_tokens` instead of the chat cap, since this flow writes a full
report that the chat-sized cap would truncate. `id_external_inventory` is what ties the
resulting plan back to the inventory; the SDK never reads your database, so it cannot be
derived here.

It returns an `ImprovementPlan`, mirroring one document of the collection:

| Field | Meaning |
| --- | --- |
| `id` | The document's `_id`. Always `None` on a fresh plan — the database owns it. |
| `id_external_inventory` | The inventory the plan was produced from. |
| `defined_problem` | The problem the analysis identified. |
| `method` | What to do about it. |
| `reasoning` | Why that method addresses that problem. |
| `updated_at` | When the plan was produced (UTC). |

The continuous improvement coordinator is instructed — in its scope, its tasks and every
one of its few-shot examples — to answer with exactly those three text fields as a JSON
object, so what the model writes and what you persist are the same thing. An answer that
doesn't match raises `MalformedAgentOutputError` rather than being padded with guesses:
the SDK will not hand you a plan whose fields it invented. The model is also never given a
say in `_id` or `updated_at` — only the three content fields are read back from it.

There is no `approved` field: this flow ends at the continuous improvement coordinator, a
terminal node, and never reaches the output guardrail.

### 5. Full example: a stateless FastAPI service

This is the shape Aeko was designed for: an HTTP API with more than one worker, where
conversation history lives in **your** database and is handed back to the SDK on every
request.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from aeko import (
    Aeko,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoNotConfiguredError,
    AekoTool,
    Session,
    User,
)

from .settings import settings
from .tools import buscar_memorias, buscar_norma, consulta_precos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Process-wide setup: exactly once, before the first request.
    Aeko.config(settings.GEMINI_API_KEY, report_max_tokens=12288)
    AekoMessenger.set_tools({
        "FAQ": [
            buscar_norma,
            AekoTool(tool=buscar_memorias,
                     description="Consulta as memórias do usuário (user_memory)."),
        ],
        "Coordenador de Melhoria Contínua": [
            AekoTool(tool=consulta_precos, description="Price the equipment you recommend."),
        ],
    })
    yield


app = FastAPI(lifespan=lifespan)


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/chat")
def chat(body: ChatRequest):
    # 1. Read the documents — this worker may never have seen this session before.
    #    The DTOs take them exactly as they come out of the collections.
    session = Session.model_validate(db.session.find_one({"_id": body.session_id}))
    user = User.model_validate(db.user.find_one({"_id": session.id_user}))

    messenger = AekoMessenger()
    messenger.prepare(session, user)

    # 2. Run it. Blocking call: keep it off the event loop in production
    #    (`run_in_threadpool`, a task queue, or a sync worker).
    try:
        response = messenger.send_message(body.message)
    except AekoNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 3. The guardrail can refuse every draft — there is no answer to persist.
    if not response.message.output:
        raise HTTPException(
            status_code=502,
            detail="The output guardrail rejected every draft. Please rephrase.",
        )

    # 4. Persist the turn so the next request (on any worker) can replay it.
    db.session.update_one(
        {"_id": response.id_session},
        {
            "$push": {"messages": response.message.model_dump()},
            "$set": {"updated_at": session.updated_at},
        },
    )

    return {
        "answer": response.message.output,
        "agents": response.agents_called,
        "approved": response.approved,
        "guardrail_retries": response.guardrail_retries,
    }


class InventoryRequest(BaseModel):
    inventory_markdown: str
    id_external_inventory: int
    previous_report: str | None = None


@app.post("/inventory")
def inventory(body: InventoryRequest):
    analyzer = AekoInventoryAnalyzer()

    if body.previous_report:
        analyzer.set_context(body.previous_report)

    plan = analyzer.analyze(body.inventory_markdown, body.id_external_inventory)

    # `exclude={"id"}`: the plan is new, so let MongoDB generate its `_id`.
    db.improvement_plan.insert_one(plan.model_dump(by_alias=True, exclude={"id"}))

    return plan.model_dump(exclude={"id"})
```

Notes for production:

- **`send_message()` and `analyze()` are synchronous and slow** (several model calls per
  run, more when the guardrail retries). Don't block an async event loop with them — use
  a threadpool, a background worker, or a task queue.
- **Never call `Aeko.config()` or `set_tools()` per request.** Both rebuild every agent
  process-wide; doing it under load throws away warm agents for every concurrent run.
- **The SDK holds no session state.** Read the `Session` document and pass it to
  `prepare()` on every request; any worker can then serve any conversation.
- Configuration and registered tools are shared mutable process state, so treat startup as
  the only place that writes them.

### 6. Error handling

Every error the SDK raises inherits from `AekoError`, so one `except` covers the SDK:

| Exception | Raised when | Typical response |
| --- | --- | --- |
| `AekoNotConfiguredError` | `Aeko.config()` was never called, or the key is empty/not a string. | `503` — a deployment problem, not a user one. |
| `SessionNotPreparedError` | `send_message()` ran before `prepare()`. | `500` — an integration bug. |
| `UnknownAgentError` | `set_tools()` got a key that is not an agent name. Carries `.agent` and `.known_agents`. | Fail at startup. |
| `MalformedAgentOutputError` | An agent's answer did not match the shape its prompt demands — today, the improvement plan's JSON. | `502` — retry the analysis rather than persisting a guess. |
| `AekoError` | Base class for all of the above. | Catch-all. |

```python
from aeko import AekoError

try:
    response = messenger.send_message(text)
except AekoError as exc:
    logger.exception("Aeko failed")
    raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Remember that a *rejected* answer is not an exception — it is a successful run with an
empty `message.output` and `approved=False`.

---

## API reference

Everything below is importable directly from `aeko`.

**`Aeko`** — configuration facade.

| Member | Signature |
| --- | --- |
| `config` | `config(api_key: str, *, fast_model: str \| None = None, slow_model: str \| None = None, max_tokens: int \| None = None, report_max_tokens: int \| None = None) -> None` |
| `is_configured` | `is_configured() -> bool` |
| `reset` | `reset() -> None` |

**`AekoMessenger`** — conversational entry point.

| Member | Signature |
| --- | --- |
| `set_tools` *(classmethod)* | `set_tools(tools: dict[str, list[AekoTool \| Any]]) -> None` |
| `prepare` | `prepare(session: Session, user: User) -> Session` |
| `send_message` | `send_message(message: str) -> MessageResponse` |

**`AekoInventoryAnalyzer`** — report entry point.

| Member | Signature |
| --- | --- |
| `set_context` | `set_context(context: str) -> None` |
| `analyze` | `analyze(inventory: str, id_external_inventory: int) -> ImprovementPlan` |

**Data objects.** Every DTO that crosses the API boundary is a Pydantic model mirroring one
MongoDB collection, field for field:

| DTO | Collection |
| --- | --- |
| `User` | `user` |
| `UserMemory` | `user_memory` |
| `Session` | `session` |
| `Message` | `session.messages[]` |
| `ImprovementPlan` | `improvement_plan` |

`model_validate(document)` takes a raw document and `model_dump(by_alias=True)` gives one
back — `_id` included, under that exact name — so the hand-off is lossless in both
directions. `MessageResponse` (the run's answer plus its metadata) and `AekoTool` are
SDK-only and mirror nothing.

The identifiers (`_id`, `id_external_user`, `id_user`, `id_external_inventory`) and
`expires_at` are carried across the boundary in both directions and echoed back on
`MessageResponse` — they are how you correlate and log what came out of a run. What they
never do is reach a prompt: a model can act on a role or a usecase, not on an ObjectId.

**Timestamps.** The SDK stamps a timestamp only on a document it produces itself, and
never invents one for a document it merely received:

| Field | Filled by | Why |
| --- | --- | --- |
| `Message.submitted_at` | SDK | The turn is created here; only the SDK knows when. |
| `ImprovementPlan.updated_at` | SDK | The plan is produced here. |
| `Session.updated_at` | SDK | The SDK is what updates the session, by appending the turn. |
| `Session.created_at` | **You** | The session already exists by the time it reaches `prepare()`. |
| `UserMemory.created_at` | **You** | The SDK never receives memories in the first place. |

So `Session`, `User` and `UserMemory` default their timestamps to `None` rather than to
"now": a session read from the database brings its own `created_at` through untouched, and
one built in Python without it would otherwise be handed a creation date that is simply
false. The consequence to watch for is on the way back — a *new* session built in Python
and dumped straight into MongoDB writes `created_at: null`. Set it at insert time, or drop
the empty fields:

```python
session.model_dump(by_alias=True, exclude_none=True)
```

**Exceptions**: `AekoError`, `AekoNotConfiguredError`, `MalformedAgentOutputError`,
`SessionNotPreparedError`, `UnknownAgentError`.
**Constants**: `AGENT_NAMES`, `__version__`.

**Defaults**

| Setting | Default |
| --- | --- |
| `fast_model` | `gemini-3.1-flash-lite` |
| `slow_model` | `gemini-3.5-flash` |
| `max_tokens` | `1024` |
| `report_max_tokens` | `8192` |
| Guardrail retry cap | `3` |

## Development

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

The test suite runs against a scripted fake chat model and never calls Gemini, so no API
key is needed to run it.

Releases are published to PyPI by the GitHub Actions workflow in
`.github/workflows/publish.yml`, triggered when a GitHub Release is published. The release
tag must match `aeko.__version__` or the workflow fails before building.

---

## Resumo em português

**aeko** é o núcleo inteligente do ecossistema Aether: um sistema multiagente
(LangChain + LangGraph) que lê o inventário de gases de efeito estufa de uma empresa,
explica os números e devolve um plano de melhoria priorizado — além do chatbot que
responde às dúvidas de ESG do dia a dia. É uma **biblioteca**, não um serviço: qualquer
backend Python pode embuti-la.

```bash
pip install aeko
```

```python
from aeko import Aeko, AekoMessenger, AekoInventoryAnalyzer, Session, User

Aeko.config("SUA_CHAVE_GEMINI")          # obrigatório: o SDK não lê variáveis de ambiente

messenger = AekoMessenger()
messenger.prepare(
    Session.model_validate(db.session.find_one({"_id": id_sessao})),
    User.model_validate(db.user.find_one({"_id": id_usuario})),
)
resposta = messenger.send_message("Como reduzo o escopo 1 da nossa caldeira?")
print(resposta.message.output)
db.session.update_one({"_id": id_sessao},
                      {"$push": {"messages": resposta.message.model_dump()}})

analisador = AekoInventoryAnalyzer()
plano = analisador.analyze(inventario_em_markdown, id_external_inventory=502)
db.improvement_plan.insert_one(plano.model_dump(by_alias=True, exclude={"id"}))
```

Quatro pontos que definem a integração:

1. **Configure no startup, nunca por requisição.** `Aeko.config()` e
   `AekoMessenger.set_tools()` alteram um runtime único do processo e reconstroem todos os
   agentes.
2. **As DTOs espelham as collections.** `User`, `UserMemory`, `Session`, `Message` e
   `ImprovementPlan` são modelos Pydantic com os mesmos campos dos documentos, `_id`
   incluso: `model_validate(documento)` entra e `model_dump(by_alias=True)` volta igual.
   O SDK não fala com o banco — quem lê e grava é a API.
3. **O SDK não guarda sessão.** Passe o documento `Session` em todo `prepare()`: a
   conversa é o próprio `session.messages`, e qualquer worker atende qualquer sessão. As
   memórias do usuário chegam por uma tool registrada em `set_tools()`, nunca por
   parâmetro.
4. **Resposta vazia não é exceção.** Se o `Guardrail de Saída` reprovar todas as
   tentativas (limite de 3), o run termina com `message.output == ""` e `approved is
   False` — verifique sempre antes de persistir. Já um plano de melhoria fora do formato
   levanta `MalformedAgentOutputError`, em vez de devolver campos inventados.

Os nomes dos agentes (`Roteador`, `FAQ`, `Orquestrador`, `Guardrail de Saída`,
`Análista de inventários`, `Analista de Poluentes`, `Analista de Gases Verdes`,
`Coordenador de Melhoria Contínua`) são também as chaves de roteamento — use-os
exatamente como escritos, inclusive acentuação. A seção em inglês acima tem o detalhamento
completo, incluindo um exemplo de serviço FastAPI stateless.

## License

MIT — see [LICENSE](LICENSE).
