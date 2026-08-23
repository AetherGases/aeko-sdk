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
`MessageResponse.answer` is `""` and `approved` is `False`. Always check it.

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

Runtime dependencies (`langchain-core`, `langchain-classic`, `langgraph`,
`langchain-google-genai`) are installed with the package.

## Installation

```bash
pip install aeko
```

Distribution and import name are the same: `aeko`. (The GitHub repository is `aeko-sdk`.)

## Quickstart

```python
from aeko import Aeko, AekoMessenger, AekoInventoryAnalyzer

# 1. Configure the SDK once, for the whole process.
Aeko.config("YOUR_GEMINI_API_KEY")

# 2. Chat.
messenger = AekoMessenger()
messenger.prepare(session_id="session-42", user_info="Ana, ESG analyst at ACME Chemicals")

reply = messenger.send_message("What is the difference between scope 1 and scope 2?")
print(reply.answer)
print(reply.agents_called)   # e.g. ['FAQ']
print(reply.approved)        # guardrail verdict

# 3. Analyze an inventory.
analyzer = AekoInventoryAnalyzer()
analyzer.set_context("Last report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

report = analyzer.analyze(inventory_markdown)
print(report.answer)         # the improvement plan
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
messenger = AekoMessenger()

session = messenger.prepare(
    session_id="session-42",
    user_info="Ana, ESG analyst at ACME Chemicals, 380 employees, resin manufacturing",
)
# session.turns -> how many prior turns the session holds

response = messenger.send_message("Our scope 1 jumped 12% this quarter. Where do I look?")
```

`user_info` is free-form and is forwarded to every agent as company context — the richer
it is, the more grounded the answers.

`send_message()` returns a `MessageResponse`:

| Field | Meaning |
| --- | --- |
| `session_id` | The session the answer belongs to. |
| `answer` | The final user-facing text, already stripped of the agents' internal routing markers. **Empty when the guardrail never approved a draft.** |
| `agents_called` | Names of the agents that contributed, in call order. |
| `approved` | Whether the output guardrail approved the answer. |
| `guardrail_retries` | How many times the guardrail sent the draft back. |

Calling `send_message()` before `prepare()` raises `SessionNotPreparedError`.

**Resuming a session on another process.** Pass the prior turns to `prepare()` and the
session is rehydrated from your own storage. History is accepted either as
`{"role", "content"}` dicts or as LangChain message objects, oldest first:

```python
messenger.prepare(
    session_id="session-42",
    user_info=user_info,
    history=[
        {"role": "user", "content": "What is scope 3?"},
        {"role": "assistant", "content": "Scope 3 covers..."},
    ],
)
```

Passing `history` **replaces** whatever this process held for that id. Omitting it keeps
(or starts) the in-process session.

### 4. The inventory flow

```python
from aeko import AekoInventoryAnalyzer

analyzer = AekoInventoryAnalyzer()

# Optional: a company's first report legitimately has no previous one.
analyzer.set_context("2025 report: 12,400 tCO2e, scope 1 dominated by the boiler fleet.")

report = analyzer.analyze(inventory_markdown)
```

`analyze()` expects the inventory **rendered as Markdown** — a table is the natural shape.
It runs with `report_max_tokens` instead of the chat cap, since this flow writes a full
report that the chat-sized cap would truncate.

It returns an `InventoryAnalysisResponse`:

| Field | Meaning |
| --- | --- |
| `answer` | The final improvement plan. |
| `agents_called` | Names of the agents that contributed. |
| `context_used` | Whether a previous-report context was set beforehand. |

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
    user_info: str
    message: str


@app.post("/chat")
def chat(body: ChatRequest):
    # 1. Rehydrate the session from your own storage — this worker may never
    #    have seen this session_id before.
    history = db.load_turns(body.session_id)

    messenger = AekoMessenger()
    messenger.prepare(body.session_id, body.user_info, history=history)

    # 2. Run it. Blocking call: keep it off the event loop in production
    #    (`run_in_threadpool`, a task queue, or a sync worker).
    try:
        response = messenger.send_message(body.message)
    except AekoNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 3. The guardrail can refuse every draft — there is no answer to persist.
    if not response.answer:
        raise HTTPException(
            status_code=502,
            detail="The output guardrail rejected every draft. Please rephrase.",
        )

    # 4. Persist the turn so the next request (on any worker) can replay it.
    db.save_turn(body.session_id, body.message, response.answer)

    return {
        "answer": response.answer,
        "agents": response.agents_called,
        "approved": response.approved,
        "guardrail_retries": response.guardrail_retries,
    }


class InventoryRequest(BaseModel):
    inventory_markdown: str
    previous_report: str | None = None


@app.post("/inventory")
def inventory(body: InventoryRequest):
    analyzer = AekoInventoryAnalyzer()

    if body.previous_report:
        analyzer.set_context(body.previous_report)

    report = analyzer.analyze(body.inventory_markdown)

    return {"plan": report.answer, "agents": report.agents_called}
```

Notes for production:

- **`send_message()` and `analyze()` are synchronous and slow** (several model calls per
  run, more when the guardrail retries). Don't block an async event loop with them — use
  a threadpool, a background worker, or a task queue.
- **Never call `Aeko.config()` or `set_tools()` per request.** Both rebuild every agent
  process-wide; doing it under load throws away warm agents for every concurrent run.
- **Don't rely on in-process sessions across workers.** Always pass `history` — a session
  that was only ever built in worker A does not exist in worker B.
- Configuration and sessions are shared mutable process state, so treat startup as the
  only place that writes them.

### 6. Error handling

Every error the SDK raises inherits from `AekoError`, so one `except` covers the SDK:

| Exception | Raised when | Typical response |
| --- | --- | --- |
| `AekoNotConfiguredError` | `Aeko.config()` was never called, or the key is empty/not a string. | `503` — a deployment problem, not a user one. |
| `SessionNotPreparedError` | `send_message()` ran before `prepare()`. | `500` — an integration bug. |
| `UnknownAgentError` | `set_tools()` got a key that is not an agent name. Carries `.agent` and `.known_agents`. | Fail at startup. |
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
empty `answer` and `approved=False`.

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
| `prepare` | `prepare(session_id: str, user_info: str, history: Sequence[Any] \| None = None) -> SessionInfo` |
| `send_message` | `send_message(message: str) -> MessageResponse` |

**`AekoInventoryAnalyzer`** — report entry point.

| Member | Signature |
| --- | --- |
| `set_context` | `set_context(context: str) -> None` |
| `analyze` | `analyze(inventory: str) -> InventoryAnalysisResponse` |

**Data objects** (frozen dataclasses): `AekoTool`, `SessionInfo`, `MessageResponse`,
`InventoryAnalysisResponse`.
**Exceptions**: `AekoError`, `AekoNotConfiguredError`, `SessionNotPreparedError`,
`UnknownAgentError`.
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
from aeko import Aeko, AekoMessenger, AekoInventoryAnalyzer

Aeko.config("SUA_CHAVE_GEMINI")          # obrigatório: o SDK não lê variáveis de ambiente

messenger = AekoMessenger()
messenger.prepare(session_id="sessao-42", user_info="Ana, analista de ESG na ACME")
resposta = messenger.send_message("Como reduzo o escopo 1 da nossa caldeira?")
print(resposta.answer)

analisador = AekoInventoryAnalyzer()
relatorio = analisador.analyze(inventario_em_markdown)
print(relatorio.answer)
```

Três pontos que definem a integração:

1. **Configure no startup, nunca por requisição.** `Aeko.config()` e
   `AekoMessenger.set_tools()` alteram um runtime único do processo e reconstroem todos os
   agentes.
2. **A memória de sessão vive no processo.** Com mais de um worker, carregue o histórico
   do seu banco e passe em `prepare(session_id, user_info, history=...)`.
3. **Resposta vazia não é exceção.** Se o `Guardrail de Saída` reprovar todas as
   tentativas (limite de 3), o run termina com `answer == ""` e `approved is False` —
   verifique sempre antes de persistir.

Os nomes dos agentes (`Roteador`, `FAQ`, `Orquestrador`, `Guardrail de Saída`,
`Análista de inventários`, `Analista de Poluentes`, `Analista de Gases Verdes`,
`Coordenador de Melhoria Contínua`) são também as chaves de roteamento — use-os
exatamente como escritos, inclusive acentuação. A seção em inglês acima tem o detalhamento
completo, incluindo um exemplo de serviço FastAPI stateless.

## License

MIT — see [LICENSE](LICENSE).
