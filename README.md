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

## What's new in 3.1

Version 3.1 makes the SDK **report what a request cost**. Both entry points now take an
`id_request` and hand back an `AekoMetrics` alongside the answer, so the API can persist
per-request telemetry it previously had no way to obtain. Two changes are breaking.

| Area | 2.x | 3.1 |
| --- | --- | --- |
| `send_message()` | `send_message(message, session)` | `send_message(message, session, *, id_request)` — **required, keyword-only** |
| `analyze()` | `analyze(inventory, *, id_external_inventory) -> AekoImprovementPlan` | `analyze(inventory, *, id_external_inventory, id_request) -> AekoAnalysisResponse` |
| Telemetry | Logs only, readable by a person | `AekoMetrics` on every response, persistable by a machine |
| Per-agent cost | Nothing — one total per run | Tokens, model and tools called, per agent invocation |
| Failed requests | The exception, and nothing else | The exception, carrying the run's `aeko_metrics` |
| `AekoMessage` | `input`, `output`, `submitted_at`, `llm`, `input_tokens`, `output_tokens` | `input`, `output`, `submitted_at` — the cost moved to `aeko_metrics` |

The logs are unchanged: this adds a second rendering of what a run already observed, it
does not replace the stream you read in a terminal. See
[Event tracking](#5-event-tracking) for the object and
[Migrating from 2.x](#migrating-from-2x) for what to change at your call sites.

---

## What's new in 2.0

Version 2 reshapes the boundary between the SDK and the API that consumes it. The agent
system is the same; **how you hand work to it changed, and every change is breaking.**

| Area | 1.x | 2.0 |
| --- | --- | --- |
| Data objects | Dataclasses describing a call (`SessionInfo`, `MessageResponse`, …) | Pydantic models mirroring the MongoDB collections, field for field |
| Session handling | `prepare(session_id, user_info, history)`, then a process-wide session cache | No `prepare()`: the `AekoSession` document travels with every `send_message()` call |
| Chat result | One `answer` string, always populated | `AekoMessageResponse` — the turn to persist, plus the run's metadata and token cost |
| Inventory result | Prose the caller had to split apart | `AekoImprovementPlan`, parsed from three fixed sections, with retry |
| User context | Nothing | `AekoUser.role`/`usecase` and every `AekoUserMemory` reach every agent |
| Configuration | Several caches, each invalidated by hand | One runtime; writing any setting drops the agents on its own |

If you are coming from 1.x, read [Migrating from 1.x](#migrating-from-1x) — it maps every
removed name to its replacement.

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
  - [5. Event tracking](#5-event-tracking)
  - [6. Full example: a stateless FastAPI service](#6-full-example-a-stateless-fastapi-service)
  - [7. Error handling](#7-error-handling)
- [Migrating from 2.x](#migrating-from-2x)
  - [5. Event tracking](#5-event-tracking)
  - [6. Full example: a stateless FastAPI service](#6-full-example-a-stateless-fastapi-service)
  - [7. Error handling](#7-error-handling)
- [Migrating from 2.x](#migrating-from-2x)
- [Migrating from 1.x](#migrating-from-1x)
- [API reference](#api-reference)
- [Development](#development)
- [Resumo em português](#resumo-em-português)
- [License](#license)

---

## What the SDK does

Aeko exposes two independent entry points into the same agent graph:

| Entry point | Class | Use it for |
| --- | --- | --- |
| **Conversation** | `AekoMessenger` | The ESG chatbot: a user message in, a reviewed answer out, against the session document you pass in. |
| **Inventory report** | `AekoInventoryAnalyzer` | A GHG inventory in, a full improvement plan out. |

Both share the same agents, the same registered tools and the same configuration. The
difference is where the run enters the graph and how it ends.

## The agent system

Eight agents, each with its own prompt, persona and tools. Names are in Portuguese
because they are also the routing keys the graph and `set_tools()` use — pass them
exactly as written, accents included.

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
specialist analysts run on `slow_model`. Both are configurable, and each model is
registered with the other as its fallback — a provider hiccup degrades a run rather than
failing it, which is also why a turn can report a model you did not ask for.

Agents hand off to each other by ending their answer with a literal `Next agent: <name>`
line, which the graph reads and the SDK strips before the text reaches you. It is
protocol between agents, never part of an answer.

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

The guardrail can send a draft back to the router **3 times**; a fourth rejection ends
the run with **no** answer at all — `AekoMessageResponse.message.output` is `""` and
`approved` is `False`. Always check it.

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

A run that entered here is **confined** to those analysts: an agent naming any other
successor — or naming itself — is routed to the coordinator instead. Without that, one
stray handoff would kill the run after every analyst had already been paid for.

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
from aeko import (
    Aeko,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoSession,
    AekoUser,
    AekoUserMemory,
)

# 1. Configure the SDK once, for the whole process.
Aeko.config("YOUR_GEMINI_API_KEY")

# 2. Chat. The documents come straight out of your database, on every request.
user = AekoUser.model_validate(db.user.find_one({"_id": user_id}))
session = AekoSession.model_validate(db.session.find_one({"_id": session_id}))
memories = [AekoUserMemory.model_validate(document)
            for document in db.user_memory.find({"id_user": user_id})]

messenger = AekoMessenger(user, memories)
reply = messenger.send_message(
    "What is the difference between scope 1 and scope 2?", session,
    id_request="req-8a31",   # yours, so you can correlate what the run cost
    "What is the difference between scope 1 and scope 2?", session,
    id_request="req-8a31",   # yours, so you can correlate what the run cost
)
print(reply.message.output)  # the answer
print(reply.agents_called)   # e.g. ['FAQ']
print(reply.approved)        # guardrail verdict
print(reply.aeko_metrics.latency)  # e.g. 2841 (ms)
print(reply.aeko_metrics.latency)  # e.g. 2841 (ms)

# `reply.message` is one entry of `session.messages`, ready to append.
db.session.update_one(
    {"_id": session_id}, {"$push": {"messages": reply.message.model_dump()}}
)

# 3. Analyze an inventory.
analyzer = AekoInventoryAnalyzer()
analyzer.set_context("Last report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

analysis = analyzer.analyze(
    inventory_markdown, id_external_inventory=502, id_request="req-8a32"
)
db.improvement_plan.insert_one(analysis.plan.model_dump(by_alias=True, exclude={"id"}))
db.aeko_metrics.insert_one(analysis.aeko_metrics.model_dump())
analysis = analyzer.analyze(
    inventory_markdown, id_external_inventory=502, id_request="req-8a32"
)
db.improvement_plan.insert_one(analysis.plan.model_dump(by_alias=True, exclude={"id"}))
db.aeko_metrics.insert_one(analysis.aeko_metrics.model_dump())
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
   `AekoMessenger.set_tools()` write to a single process-wide runtime, and *any* write to
   it drops the agents so the next run rebuilds them. Call them **at startup**, not per
   request. (`set_tools` is a `classmethod` for exactly this reason: registering tools on
   one instance would silently rebuild the agents behind every other instance's back.)
3. **The SDK keeps no session state at all.** The conversation lives in the `AekoSession`
   you pass to every `send_message()` call, and the SDK updates that object in place
   before returning. Nothing is cached between calls, so any worker can serve any
   conversation — and the process never accumulates sessions it cannot evict.

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

`set_tools()` replaces the whole registry, so pass every agent's tools in one call.
Appending to an already-registered list is the one write the runtime cannot observe, and
the agents would go on using the tools they were built with.

### 3. The conversational flow

```python
from aeko import AekoMessage, AekoMessenger, AekoSession, AekoUser, AekoUserMemory

# Built once per user — it holds nothing about any conversation.
messenger = AekoMessenger(
    AekoUser(id_external_user=1001, role="ESG analyst",
             usecase="Tracks the boiler fleet's gas substitution."),
    [AekoUserMemory(field="preferred_language", description="Answers in Portuguese")],
)

# Rehydrated per request, from what you persisted.
session = AekoSession(
    id="64b8f0a1c9e1a2b3c4d5e6f3",
    id_user="64b8f0a1c9e1a2b3c4d5e6f1",
    name="Scope 1 review",
    messages=[AekoMessage(input="What is scope 3?", output="Scope 3 covers...")],
)

response = messenger.send_message(
    "Our scope 1 jumped 12% this quarter. Where do I look?", session,
    id_request="req-8a31",
    "Our scope 1 jumped 12% this quarter. Where do I look?", session,
    id_request="req-8a31",
)
```

`id_request` is **required and keyword-only**. It is yours: whatever your API correlates
this request by — a trace id, a job id, the HTTP request id. The SDK reads no database and
invents nothing, so it cannot derive one; it echoes yours back on the returned
[event tracking](#5-event-tracking). It never reaches a prompt and is never logged.

`id_request` is **required and keyword-only**. It is yours: whatever your API correlates
this request by — a trace id, a job id, the HTTP request id. The SDK reads no database and
invents nothing, so it cannot derive one; it echoes yours back on the returned
[event tracking](#5-event-tracking). It never reaches a prompt and is never logged.

The two documents your database already holds go in as they are, so there is no separate
"history" format to translate: `session.messages` **is** the conversation, replayed to the
agents oldest first. Nothing is kept between calls — pass the session on every request and
any worker can serve it.

**Only the 10 most recent turns are replayed** (`SESSION_HISTORY_USAGE`, in
`aeko.config.messenger`). Send the session as long as it is: a conversation of 500 turns
costs a run exactly what
one of 10 does, and you do not have to slice anything on the way in. What the SDK caps is
what the agents *read*, never what you persist — the turn it answers is appended to the
session you passed, and every earlier turn is still there when the call returns.

`user.role` and `user.usecase` become the business context every agent reads. The
identifiers (`_id`, `id_external_user`, `id_user`) never reach a prompt: they are there so
you can correlate documents, and a model can do nothing with them.

`send_message()` returns an `AekoMessageResponse`:

| Field | Meaning |
| --- | --- |
| `message` | The turn, as one entry of `session.messages` — ready to append. |
| `id_session` / `id_user` | The `_id`s of the session and user this answer belongs to, echoed back from the session you sent in. |
| `agents_called` | Names of the agents that contributed, in call order. |
| `approved` | Whether the output guardrail approved the answer. |
| `guardrail_retries` | How many times the guardrail sent the draft back. |
| `aeko_metrics` | What the request cost and went through — see [Event tracking](#5-event-tracking). |
| `aeko_metrics` | What the request cost and went through — see [Event tracking](#5-event-tracking). |

Only `message` belongs in the `session.messages` array; everything else describes *how*
the run reached the answer, or which documents it belongs to, and is deliberately not part
of the persisted entry — `session.messages[]` carries no identifiers of its own. The identifiers
Only `message` belongs in the `session.messages` array; everything else describes *how*
the run reached the answer, or which documents it belongs to, and is deliberately not part
of the persisted entry — `session.messages[]` carries no identifiers of its own. The identifiers
are still returned so a response is enough on its own to file and log the answer against
the right conversation. The turn itself mirrors the collection exactly:

| Field | Meaning |
| --- | --- |
| `input` | What the user sent. |
| `output` | The final user-facing text, already stripped of the agents' internal routing markers. **Empty when the guardrail never approved a draft.** |
| `submitted_at` | When the turn was answered (UTC). |

**What the turn cost is not on the turn.** The model that served it and the tokens it
burned live on `aeko_metrics.used_agents`, per agent invocation — a finer account of the
same thing (see [Event tracking](#5-event-tracking)). A rolled-up copy here would be a
second record of one fact, free to drift from the first and impossible to tell apart once
it had. To answer "what did this conversation cost", sum the invocations:

```python
sum(agent.input_tokens for agent in response.aeko_metrics.used_agents)
```

An answered turn is appended to the `AekoSession` you passed in, and `updated_at` is
bumped — the same object, updated in place, so you can persist exactly what you handed
over. **Only a final result is recorded**: a turn the guardrail rejected is *not*
appended, because a draft that never reached the user cannot become context for the next
question.

**User memories.** Hand the `user_memory` documents to the constructor and **every one of
them** is rendered into the same business context the user's role and usecase go into, so
every agent of the run reads them:

```python
messenger = AekoMessenger(user, memories)   # list[AekoUserMemory]
```

Each memory becomes one `"- <field>: <description>"` line (`AekoUserMemory.to_prompt_line()`)
under a `Memórias do usuário:` heading, with `expires_at` and the identifiers left out — a
model can act on what was remembered, not on when the row stops being valid. There is no
cap here, unlike the conversation: a memory is already the condensed form of something the
user told you once, and dropping any of them would silently un-remember it.

**Deciding which memories are still valid is your API's job**: filter `expires_at` before
handing the list over. A user with no memories is normal — the argument is optional, and
no empty section reaches the prompt.

### 4. The inventory flow

```python
from aeko import AekoInventoryAnalyzer

analyzer = AekoInventoryAnalyzer()

# Optional: a company's first report legitimately has no previous one.
analyzer.set_context("2025 report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

analysis = analyzer.analyze(
    inventory_markdown, id_external_inventory=502, id_request="req-8a32"
)
plan = analysis.plan
analysis = analyzer.analyze(
    inventory_markdown, id_external_inventory=502, id_request="req-8a32"
)
plan = analysis.plan
```

`analyze()` expects the inventory **rendered as Markdown** — a table is the natural shape.
It runs with `report_max_tokens` instead of the chat cap, since this flow writes a full
report that the chat-sized cap would truncate. `id_external_inventory` is what ties the
resulting plan back to the inventory; the SDK never reads your database, so it cannot be
derived here, and neither can `id_request` — both are required and keyword-only.
derived here, and neither can `id_request` — both are required and keyword-only.

It returns an `AekoAnalysisResponse`, an envelope of two things: `plan`, the document to
persist, and `aeko_metrics`, what producing it cost (see
[Event tracking](#5-event-tracking)). The metrics are an envelope field rather than a field
of the plan because the `improvement_plan` collection has no column for a latency — a plan
carrying its own runtime would be a document the collection never described.

`analysis.plan` is an `AekoImprovementPlan`, mirroring one document of the collection:
It returns an `AekoAnalysisResponse`, an envelope of two things: `plan`, the document to
persist, and `aeko_metrics`, what producing it cost (see
[Event tracking](#5-event-tracking)). The metrics are an envelope field rather than a field
of the plan because the `improvement_plan` collection has no column for a latency — a plan
carrying its own runtime would be a document the collection never described.

`analysis.plan` is an `AekoImprovementPlan`, mirroring one document of the collection:

| Field | Meaning |
| --- | --- |
| `id` | The document's `_id`. Always `None` on a fresh plan — the database owns it. |
| `id_external_inventory` | The inventory the plan was produced from. |
| `defined_problem` | The problem the analysis identified. |
| `method` | What to do about it. |
| `reasoning` | Why that method addresses that problem. |
| `updated_at` | When the plan was produced (UTC). |

The continuous improvement coordinator is instructed — in its scope, its tasks and every
one of its few-shot examples — to write exactly those three text fields under three fixed
headings, and the SDK reads them straight back out:

```text
## Problema definido
...

## Método
...

## Raciocínio
...
```

Headings rather than a JSON object, for the same reason the agents route on a literal
`Next agent:` line: this flow runs with the report token cap, and a truncated JSON object
is unparseable and costs the whole plan, while a truncated last section still yields the
ones written before it. Only those three headings delimit a section, so a subtitle the
agent writes mid-answer never cuts its own text short — and case and accents are ignored
when matching them, so `## RACIOCINIO` is read as `## Raciocínio`.

An answer that misses a section is **sent back to the coordinator to be rewritten**, with
the missing headings named and the original request attached, up to four times
(`PLAN_FORMAT_MAX_RETRIES`). The retry happens inside the graph and only re-asks the
coordinator — the inventory, pollutant and green gas analysts already ran, and their
findings stay in the state. If the format never comes, `analyze()` raises
`MalformedAgentOutputError` rather than padding the plan with guesses: the SDK will not
hand you a plan whose fields it invented. The model is also never given a say in `_id` or
`updated_at` — only the three content fields are read back from it.

Budget for it: a plan that takes every retry costs five coordinator calls at the report
token cap, on top of the analysts. A well-formed answer costs exactly one.

There is no `approved` field: this flow ends at the continuous improvement coordinator, a
terminal node, and never reaches the output guardrail.

### 5. Event tracking

Both entry points hand back an `AekoMetrics` describing the request that just ran —
`response.aeko_metrics` on a chat turn, `analysis.aeko_metrics` on an analysis. It exists
because the SDK writes to no database: what it observes about a run leaves with the run's
answer or is lost when the process moves on.

| Field | Meaning |
| --- | --- |
| `id_request` | The `id_request` you passed in, echoed back. |
| `latency` | How long the whole request took, in whole milliseconds. |
| `error_description` | Why it failed, or `None`. Filled for a turn the guardrail never approved, even though that turn returns normally. |
| `flow` | `"conversational"` for `send_message()`, `"analytical"` for `analyze()`. |
| `used_agents` | One `AekoAgentMetrics` per agent **invocation**, in call order. |

Each entry of `used_agents`:

| Field | Meaning |
| --- | --- |
| `name` | The agent, under the exact name the graph routes it by. |
| `input_tokens` / `output_tokens` | What that single invocation consumed, the whole tool-calling loop included. |
| `llm` | The model that served it. Comma-separated if the cross-model fallback fired mid-call. |
| `used_tools` | The tools it **actually called**, in call order — not the ones registered for it. |

**One entry per call, not per agent.** The guardrail's retry loop runs the same agents
again and again, and a turn that paid for four routings is not a turn that paid for one:

```python
tracking = response.aeko_metrics

[agent.name for agent in tracking.used_agents]
# ['Roteador', 'Analista de Poluentes', 'Orquestrador', 'Guardrail de Saída',
#  'Roteador', 'Orquestrador', 'Guardrail de Saída', ...]

sum(agent.input_tokens for agent in tracking.used_agents)
# 176 — what the whole turn cost, guardrail retries included
```

A turn with a rejected guardrail can reach ~16 entries. Budget the document size
accordingly.

**A failed request still reports.** `analyze()` raises `MalformedAgentOutputError` when
the coordinator never produces the plan's three sections, and there is no response left to
carry the metrics — so they are attached to the exception instead:

```python
try:
    analysis = analyzer.analyze(inventory, id_external_inventory=502, id_request=rid)
except AekoError as exc:
    if exc.aeko_metrics:                      # None for errors raised outside a request
        db.aeko_metrics.insert_one(exc.aeko_metrics.model_dump())
    raise
```

`AekoError.aeko_metrics` is `None` for an error raised outside any request — a refused
`Aeko.config()`, or tools registered for an agent that does not exist.

**This does not replace the logs.** The SDK still writes one
`[aeko-sdk] [module] [datetime] ...` line per request to its own `aeko` logger, and the
event tracking is built from the same observations rather than from a second pass — the
agents one lists are the agents the other lists, in the same order.

### 6. Full example: a stateless FastAPI service

This is the shape Aeko was designed for: an HTTP API with more than one worker, where
conversation history lives in **your** database and is handed back to the SDK on every
request.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from aeko import (
    Aeko,
    AekoError,
    AekoError,
    AekoInventoryAnalyzer,
    AekoMessenger,
    AekoNotConfiguredError,
    AekoSession,
    AekoTool,
    AekoUser,
    AekoUserMemory,
)

from .settings import settings
from .tools import buscar_norma, consulta_precos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Process-wide setup: exactly once, before the first request.
    Aeko.config(settings.GEMINI_API_KEY, report_max_tokens=12288)
    AekoMessenger.set_tools({
        "FAQ": [buscar_norma],
        "Coordenador de Melhoria Contínua": [
            AekoTool(tool=consulta_precos, description="Price the equipment you recommend."),
        ],
    })
    yield


app = FastAPI(lifespan=lifespan)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    request_id: str
    request_id: str


@app.post("/chat")
def chat(body: ChatRequest):
    # 1. Read the documents — this worker may never have seen this session before.
    #    The DTOs take them exactly as they come out of the collections.
    session = AekoSession.model_validate(db.session.find_one({"_id": body.session_id}))
    user = AekoUser.model_validate(db.user.find_one({"_id": session.id_user}))

    #    Expiring the memories is yours to do: the SDK renders every one it gets.
    memories = [
        AekoUserMemory.model_validate(document)
        for document in db.user_memory.find({"id_user": user.id, "expires_at": None})
    ]

    messenger = AekoMessenger(user, memories)

    # 2. Run it. Blocking call: keep it off the event loop in production
    #    (`run_in_threadpool`, a task queue, or a sync worker).
    try:
        response = messenger.send_message(
            body.message, session, id_request=body.request_id
        )
        response = messenger.send_message(
            body.message, session, id_request=body.request_id
        )
    except AekoNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 3. Persist what the run cost, whether or not it produced an answer.
    db.aeko_metrics.insert_one(response.aeko_metrics.model_dump())

    # 4. The guardrail can refuse every draft — there is no answer to persist.
    # 3. Persist what the run cost, whether or not it produced an answer.
    db.aeko_metrics.insert_one(response.aeko_metrics.model_dump())

    # 4. The guardrail can refuse every draft — there is no answer to persist.
    if not response.message.output:
        raise HTTPException(
            status_code=502,
            detail="The output guardrail rejected every draft. Please rephrase.",
        )

    # 5. Persist the turn so the next request (on any worker) can replay it.
    # 5. Persist the turn so the next request (on any worker) can replay it.
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
    request_id: str
    request_id: str
    previous_report: str | None = None


@app.post("/inventory")
def inventory(body: InventoryRequest):
    analyzer = AekoInventoryAnalyzer()

    if body.previous_report:
        analyzer.set_context(body.previous_report)

    try:
        analysis = analyzer.analyze(
            body.inventory_markdown,
            id_external_inventory=body.id_external_inventory,
            id_request=body.request_id,
        )
    except AekoError as exc:
        # A failed analysis has no response to carry its metrics — the exception does.
        if exc.aeko_metrics:
            db.aeko_metrics.insert_one(exc.aeko_metrics.model_dump())
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.aeko_metrics.insert_one(analysis.aeko_metrics.model_dump())
    try:
        analysis = analyzer.analyze(
            body.inventory_markdown,
            id_external_inventory=body.id_external_inventory,
            id_request=body.request_id,
        )
    except AekoError as exc:
        # A failed analysis has no response to carry its metrics — the exception does.
        if exc.aeko_metrics:
            db.aeko_metrics.insert_one(exc.aeko_metrics.model_dump())
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.aeko_metrics.insert_one(analysis.aeko_metrics.model_dump())

    # `exclude={"id"}`: the plan is new, so let MongoDB generate its `_id`.
    db.improvement_plan.insert_one(analysis.plan.model_dump(by_alias=True, exclude={"id"}))
    db.improvement_plan.insert_one(analysis.plan.model_dump(by_alias=True, exclude={"id"}))

    return analysis.plan.model_dump(exclude={"id"})
    return analysis.plan.model_dump(exclude={"id"})
```

Notes for production:

- **`send_message()` and `analyze()` are synchronous and slow** (several model calls per
  run, more when the guardrail retries). Don't block an async event loop with them — use
  a threadpool, a background worker, or a task queue.
- **Never call `Aeko.config()` or `set_tools()` per request.** Both rebuild every agent
  process-wide; doing it under load throws away warm agents for every concurrent run.
- **The SDK holds no session state.** Read the `AekoSession` document and pass it to
  `send_message()` on every request; any worker can then serve any conversation.
- Configuration and registered tools are shared mutable process state, so treat startup as
  the only place that writes them.

### 7. Error handling
### 7. Error handling

Every error the SDK raises inherits from `AekoError`, so one `except` covers the SDK:

| Exception | Raised when | Typical response |
| --- | --- | --- |
| `AekoNotConfiguredError` | `Aeko.config()` was never called, or the key is empty/not a string. | `503` — a deployment problem, not a user one. |
| `UnknownAgentError` | `set_tools()` got a key that is not an agent name. Carries `.agent` and `.known_agents`. | Fail at startup. |
| `MalformedAgentOutputError` | An agent's answer did not match the shape its prompt demands — today, the improvement plan's three headings — and did not recover after four rewrites. | `502` — retry the analysis rather than persisting a guess. |
| `AekoError` | Base class for all of the above. | Catch-all. |

Every `AekoError` raised *inside* a request carries that request's
[event tracking](#5-event-tracking) on `.aeko_metrics`, so a run that failed can still be
persisted. It is `None` for errors raised outside one — `AekoNotConfiguredError` from a
refused `Aeko.config()`, or `UnknownAgentError` from `set_tools()`.

Every `AekoError` raised *inside* a request carries that request's
[event tracking](#5-event-tracking) on `.aeko_metrics`, so a run that failed can still be
persisted. It is `None` for errors raised outside one — `AekoNotConfiguredError` from a
refused `Aeko.config()`, or `UnknownAgentError` from `set_tools()`.

```python
from aeko import AekoError

try:
    response = messenger.send_message(text, session, id_request=request_id)
    response = messenger.send_message(text, session, id_request=request_id)
except AekoError as exc:
    logger.exception("Aeko failed")
    if exc.aeko_metrics:
        db.aeko_metrics.insert_one(exc.aeko_metrics.model_dump())
    if exc.aeko_metrics:
        db.aeko_metrics.insert_one(exc.aeko_metrics.model_dump())
    raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Remember that a *rejected* answer is not an exception — it is a run that returns
normally with an empty `message.output` and `approved=False`. Its event tracking still
records it as a failure, in `error_description`, which is the only place that outcome is
written down.

---

## Migrating from 2.x

Two signatures changed, and nothing else. There is no shim: a 2.x call fails immediately
with a `TypeError`, which is the point — an event tracking nobody can correlate is not
worth producing.

```python
# 2.x
reply = messenger.send_message(text, session)
plan = analyzer.analyze(markdown, id_external_inventory=502)

# 3.1
reply = messenger.send_message(text, session, id_request=request_id)
plan = analyzer.analyze(
    markdown, id_external_inventory=502, id_request=request_id
).plan
```

| What | Change |
| --- | --- |
| `send_message()` | Add `id_request=` — required, keyword-only. Returns the same `AekoMessageResponse`, now with an `aeko_metrics` field. |
| `analyze()` | Add `id_request=`. **Returns `AekoAnalysisResponse`, not `AekoImprovementPlan`** — read the plan off `.plan`. |
| `AekoMessageResponse(...)` built by hand | `aeko_metrics` is a required field. |
| `AekoMessage` | Lost `llm`, `input_tokens` and `output_tokens`. Read the cost off `aeko_metrics.used_agents` instead — and stop writing those three to `session.messages`, or ignore them on the documents you already have. |
| Nothing else | The DTOs, the agent names, the tools, the graph and the logs are untouched. |

If you build an `AekoMessageResponse` yourself (tests, fixtures), the smallest valid
tracking is `AekoMetrics(id_request="...", flow="conversational")`.

---

## Migrating from 1.x

Every name below changed in 2.0. There is no deprecation shim: the 1.x calls fail at
import or call time rather than quietly doing something else.

Every 1.x DTO was a plain dataclass describing a *call*. Every 2.0 DTO is a Pydantic model
mirroring a *collection*, which is what makes the hand-off to the database lossless.

| 1.x | 2.0 |
| --- | --- |
| `SessionInfo(session_id, user_info, turns)` | `AekoSession` — the `session` document itself, `messages` included |
| `MessageResponse(session_id, answer, ...)` | `AekoMessageResponse` — `.answer` becomes `.message.output`, a full `AekoMessage` |
| `InventoryAnalysisResponse(answer, agents_called, context_used)` | `AekoImprovementPlan` — three named fields instead of one `answer` blob |
| — | `AekoUser`, `AekoUserMemory`, `AekoMessage` are new |
| `SessionNotPreparedError` | removed — there is nothing to prepare |
| — | `MalformedAgentOutputError` is new |
| `messenger.prepare(session_id, user_info, history) -> SessionInfo` | removed — construct with `AekoMessenger(user, memories)` |
| `send_message(text) -> MessageResponse` | `send_message(text, session) -> AekoMessageResponse` |
| `analyze(inventory) -> InventoryAnalysisResponse` | `analyze(inventory, *, id_external_inventory) -> AekoImprovementPlan` |

**The shape of the change.** In 1.x the messenger owned a process-wide dictionary of
sessions, and you announced a conversation to it before using it. In 2.0 the conversation
is a document you already have:

```python
# 1.x
messenger = AekoMessenger()
messenger.prepare(session_id, user_info="ESG analyst", history=[...])
result = messenger.send_message("Our scope 1 jumped 12%.")
answer = result.answer

# 2.0
messenger = AekoMessenger(user, memories)                    # who is asking
response = messenger.send_message("Our scope 1 jumped 12%.", session)
answer = response.message.output                             # may be "" — check it
```

Four consequences worth planning for:

1. **You must persist the session.** The SDK caches nothing, so the turn appended to
   `session.messages` is lost unless you write it back. In exchange, any worker can serve
   any conversation.
2. **`user_info` is now structured.** The free-form string that went into `prepare()` is
   `AekoUser.role` plus `AekoUser.usecase`, and anything else you were packing into it
   probably belongs in `AekoUserMemory` — which is rendered in a section of its own.
3. **An answer can be empty.** `.answer` was always populated; `.message.output` is `""`
   when the guardrail rejected every draft, and that is a successful run, not an
   exception. `approved` tells you which happened.
4. **The improvement plan is structured.** `analyze()` returns `defined_problem`, `method`
   and `reasoning` instead of one `answer` string, and raises `MalformedAgentOutputError`
   when the coordinator never produces them — code that used to split the report apart
   should read the fields directly. `context_used` is gone; you know whether you called
   `set_context()`.

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
| *constructor* | `AekoMessenger(user: AekoUser, memories: Sequence[AekoUserMemory] \| None = None)` |
| `send_message` | `send_message(message: str, session: AekoSession, *, id_request: str) -> AekoMessageResponse` |
| `send_message` | `send_message(message: str, session: AekoSession, *, id_request: str) -> AekoMessageResponse` |

**`AekoInventoryAnalyzer`** — report entry point.

| Member | Signature |
| --- | --- |
| *constructor* | `AekoInventoryAnalyzer()` |
| `set_context` | `set_context(context: str) -> None` |
| `analyze` | `analyze(inventory: str, *, id_external_inventory: int, id_request: str) -> AekoAnalysisResponse` |
| `analyze` | `analyze(inventory: str, *, id_external_inventory: int, id_request: str) -> AekoAnalysisResponse` |

**Data objects.** Every DTO that crosses the API boundary is a Pydantic model mirroring one
MongoDB collection, field for field:

| DTO | Collection |
| --- | --- |
| `AekoUser` | `user` |
| `AekoUserMemory` | `user_memory` |
| `AekoSession` | `session` |
| `AekoMessage` | `session.messages[]` |
| `AekoImprovementPlan` | `improvement_plan` |

`model_validate(document)` takes a raw document and `model_dump(by_alias=True)` gives one
back — `_id` included, under that exact name — so the hand-off is lossless in both
directions.

`AekoMessageResponse` (the turn plus the run's metadata), `AekoAnalysisResponse` (the plan
plus the run's metadata), `AekoMetrics`, `AekoAgentMetrics` and `AekoTool` are SDK-only and
mirror no collection. The two `*Metrics` models are plain Pydantic models — persist them
wherever your telemetry lives; the SDK has no opinion on the collection's name.
directions.

`AekoMessageResponse` (the turn plus the run's metadata), `AekoAnalysisResponse` (the plan
plus the run's metadata), `AekoMetrics`, `AekoAgentMetrics` and `AekoTool` are SDK-only and
mirror no collection. The two `*Metrics` models are plain Pydantic models — persist them
wherever your telemetry lives; the SDK has no opinion on the collection's name.

The identifiers (`_id`, `id_external_user`, `id_user`, `id_external_inventory`) and
`expires_at` are carried across the boundary in both directions and echoed back on
`AekoMessageResponse` — they are how you correlate and log what came out of a run. What they
never do is reach a prompt: a model can act on a role or a usecase, not on an ObjectId.

**Timestamps.** The SDK stamps a timestamp only on a document it produces itself, and
never invents one for a document it merely received:

| Field | Filled by | Why |
| --- | --- | --- |
| `AekoMessage.submitted_at` | SDK | The turn is created here; only the SDK knows when. |
| `AekoImprovementPlan.updated_at` | SDK | The plan is produced here. |
| `AekoSession.updated_at` | SDK | The SDK is what updates the session, by appending the turn. |
| `AekoSession.created_at` | **You** | The session already exists by the time it reaches `send_message()`. |
| `AekoUserMemory.created_at` | **You** | The memory was recorded by your API; the SDK only reads it into the prompt. |

So `AekoSession`, `AekoUser` and `AekoUserMemory` default their timestamps to `None` rather than to
"now": a session read from the database brings its own `created_at` through untouched, and
one built in Python without it would otherwise be handed a creation date that is simply
false. The consequence to watch for is on the way back — a *new* session built in Python
and dumped straight into MongoDB writes `created_at: null`. Set it at insert time, or drop
the empty fields:

```python
session.model_dump(by_alias=True, exclude_none=True)
```

**Exceptions**: `AekoError`, `AekoNotConfiguredError`, `MalformedAgentOutputError`,
`UnknownAgentError`. Every one raised inside a request carries that request's
`.aeko_metrics`.
`UnknownAgentError`. Every one raised inside a request carries that request's
`.aeko_metrics`.
**Constants**: `AGENT_NAMES`, `__version__`.

**Defaults and limits**

| Setting | Default | Where it lives |
| --- | --- | --- |
| `fast_model` | `gemini-3.1-flash-lite` | `Aeko.config()` |
| `slow_model` | `gemini-3.5-flash` | `Aeko.config()` |
| `max_tokens` | `1024` | `Aeko.config()` |
| `report_max_tokens` | `8192` | `Aeko.config()` |
| Guardrail retry cap | `3` | fixed — `GUARD_RAIL_MAX_RETRIES` |
| Plan rewrite cap | `4` | fixed — `PLAN_FORMAT_MAX_RETRIES` |
| Replayed turns | `10` | fixed — `SESSION_HISTORY_USAGE` |

## Development

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
pytest
```

The test suite runs against a scripted fake chat model and never calls Gemini, so no API
key is needed to run it.

`requirements.txt` pins the exact versions the suite is green against; `pyproject.toml`
declares the same dependencies as ranges, so installing the SDK next to other LangChain
packages does not deadlock a consumer's resolver.

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
from aeko import (Aeko, AekoInventoryAnalyzer, AekoMessenger, AekoSession, AekoUser,
                  AekoUserMemory)

Aeko.config("SUA_CHAVE_GEMINI")          # obrigatório: o SDK não lê variáveis de ambiente

usuario = AekoUser.model_validate(db.user.find_one({"_id": id_usuario}))
sessao = AekoSession.model_validate(db.session.find_one({"_id": id_sessao}))
memorias = [AekoUserMemory.model_validate(documento)
            for documento in db.user_memory.find({"id_user": id_usuario})]

messenger = AekoMessenger(usuario, memorias)
resposta = messenger.send_message("Como reduzo o escopo 1 da nossa caldeira?", sessao,
                                  id_request="req-8a31")
resposta = messenger.send_message("Como reduzo o escopo 1 da nossa caldeira?", sessao,
                                  id_request="req-8a31")
print(resposta.message.output)
db.session.update_one({"_id": id_sessao},
                      {"$push": {"messages": resposta.message.model_dump()}})
db.aeko_metrics.insert_one(resposta.aeko_metrics.model_dump())
db.aeko_metrics.insert_one(resposta.aeko_metrics.model_dump())

analisador = AekoInventoryAnalyzer()
analise = analisador.analyze(inventario_em_markdown, id_external_inventory=502,
                             id_request="req-8a32")
db.improvement_plan.insert_one(analise.plan.model_dump(by_alias=True, exclude={"id"}))
db.aeko_metrics.insert_one(analise.aeko_metrics.model_dump())
```

### O que mudou na 3.1

A 3.1 faz o SDK **relatar o que cada requisição custou**. Os dois métodos passaram a exigir
um `id_request` (obrigatório e keyword-only) e devolvem um `AekoMetrics` junto da resposta:
latência em milissegundos, descrição do erro quando houve, o fluxo (`conversational` ou
`analytical`) e uma entrada por **chamada** de agente, com tokens de entrada e saída,
modelo usado e as tools que aquele agente realmente chamou.

Três mudanças quebram compatibilidade: a assinatura dos dois métodos; `analyze()`, que
agora devolve `AekoAnalysisResponse` (`.plan` + `.aeko_metrics`) em vez de
`AekoImprovementPlan`; e o `AekoMessage`, que ficou só com `input`, `output` e
`submitted_at` — `llm`, `input_tokens` e `output_tokens` saíram, porque a `AekoMetrics` já
conta a mesma coisa por chamada de agente, e dois registros do mesmo fato divergem. Quando o run falha e levanta exceção, as métricas vão anexadas nela
(`exc.aeko_metrics`), porque uma requisição que falhou é justamente a que mais interessa
persistir. Os logs continuam iguais — isto acrescenta uma segunda leitura do que o run já
observava, não substitui o stream que você lê no terminal.

### O que mudou na 2.0

A fronteira entre o SDK e a API que o consome foi refeita, e **toda mudança quebra
compatibilidade**: `SessionInfo` e `InventoryAnalysisResponse` saíram, `prepare()` e
`SessionNotPreparedError` sumiram, `send_message()` passou a receber a sessão e devolver
`AekoMessageResponse`, e `analyze()` devolve `AekoImprovementPlan`. O mapa completo de
nomes antigos para novos está em [Migrating from 1.x](#migrating-from-1x).

### Cinco pontos que definem a integração
### Cinco pontos que definem a integração

1. **Configure no startup, nunca por requisição.** `Aeko.config()` e
   `AekoMessenger.set_tools()` alteram um runtime único do processo e reconstroem todos os
   agentes.
2. **As DTOs espelham as collections.** `AekoUser`, `AekoUserMemory`, `AekoSession`,
   `AekoMessage` e `AekoImprovementPlan` são modelos Pydantic com os mesmos campos dos
   documentos, `_id` incluso: `model_validate(documento)` entra e
   `model_dump(by_alias=True)` volta igual. O SDK não fala com o banco — quem lê e grava
   é a API.
3. **O SDK não guarda sessão.** Passe o documento `AekoSession` em todo `send_message()`:
   a conversa é o próprio `session.messages`, o SDK o atualiza in-place e qualquer worker
   atende qualquer sessão. Só os **10 turnos mais recentes** são reenviados aos agentes —
   mande a sessão inteira, o corte é do que o modelo lê, nunca do que você persiste. Já as
   memórias do usuário vão no construtor (`AekoMessenger(user, memories)`) e são
   renderizadas **todas**, sem corte, no contexto que todo agente lê — filtrar as
   expiradas é com a API.
4. **Toda resposta carrega métricas.** `resposta.aeko_metrics` e
   `analise.aeko_metrics` trazem latência, erro, fluxo e o custo por chamada de agente —
   persista onde sua telemetria vive. Numa exceção, as métricas vêm em `exc.aeko_metrics`.
5. **Resposta vazia não é exceção.** Se o `Guardrail de Saída` reprovar todas as
4. **Toda resposta carrega métricas.** `resposta.aeko_metrics` e
   `analise.aeko_metrics` trazem latência, erro, fluxo e o custo por chamada de agente —
   persista onde sua telemetria vive. Numa exceção, as métricas vêm em `exc.aeko_metrics`.
5. **Resposta vazia não é exceção.** Se o `Guardrail de Saída` reprovar todas as
   tentativas (limite de 3 devoluções), o run termina com `message.output == ""` e
   `approved is False` — verifique sempre antes de persistir. Já um plano de melhoria fora
   do formato levanta `MalformedAgentOutputError` — depois de o Coordenador ser convidado a
   reescrever a resposta até quatro vezes —, em vez de devolver campos inventados.

Os nomes dos agentes (`Roteador`, `FAQ`, `Orquestrador`, `Guardrail de Saída`,
`Análista de inventários`, `Analista de Poluentes`, `Analista de Gases Verdes`,
`Coordenador de Melhoria Contínua`) são também as chaves de roteamento — use-os
exatamente como escritos, inclusive acentuação. A seção em inglês acima tem o detalhamento
completo, incluindo um exemplo de serviço FastAPI stateless.

## License

MIT — see [LICENSE](LICENSE).
