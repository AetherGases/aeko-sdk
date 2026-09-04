from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aeko.shared import AekoMetrics

# Fields that exist only so the API can persist and correlate documents. They
# are deliberately left out of every prompt rendering below: a model that reads
# them gains nothing and may start echoing internal identifiers back at the
# user.
LOG_ONLY_FIELDS = ("_id", "id", "id_external_user", "id_user", "expires_at")


def _now() -> datetime:
    """
    Current UTC time, used as the default for timestamps the SDK itself writes.

    Returns:
        datetime: The current time, timezone-aware, in UTC.
    """

    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AekoTool:
    """
    A tool made available to one agent, with the description the model reads.

    The description is rendered into the agent's "# Ferramentas Disponiveis"
    prompt section *and* the same tool object is bound to the agent's executor,
    so what the prompt promises and what the agent can actually call always come
    from this single declaration.

    Attributes:
        tool: The LangChain tool object to bind to the agent.
        description: How the agent should decide to use it. Falls back to the
            tool's own `.description` when left empty.
    """

    tool: Any
    description: str = ""

    @property
    def name(self) -> str:
        """
        The tool's name, as the model will see it in a tool call.

        Returns:
            str: The wrapped tool's `.name`.
        """

        return getattr(self.tool, "name", type(self.tool).__name__)

    def to_prompt_line(self) -> str:
        """
        Render this tool as one line of the prompt's tool section.

        Returns:
            str: A "<name> - <description>" line, matching the format the
                existing prompt specs already use.
        """

        description = self.description or getattr(self.tool, "description", "")
        return f"{self.name} - {description}".rstrip(" -")

    @classmethod
    def wrap(cls, tool: "AekoTool | Any") -> "AekoTool":
        """
        Normalize a caller-supplied tool into an `AekoTool`.

        Args:
            tool: Either an `AekoTool` or a bare LangChain tool, in which case
                its own `.description` is used.

        Returns:
            AekoTool: The normalized tool.
        """

        return tool if isinstance(tool, cls) else cls(tool=tool)


class AekoUser(BaseModel):
    """
    Who is asking, mirroring one document of the "user" collection.

    The SDK never reads the database — the consuming API does, and hands the
    document over as-is. `model_validate(document)` accepts it unchanged and
    `model_dump(by_alias=True)` gives it back with the collection's own field
    names, `_id` included, so the round trip is lossless in both directions.

    Attributes:
        id: The document's `_id`. Owned by the database; the SDK only carries it.
        id_external_user: The user's id in the Aether platform.
        role: The user's role, e.g. "environment analyzer".
        usecase: What this user has been using the assistant for. Empty for a
            user who has not been characterized yet.
    """

    # Populating by field name is enabled so callers can build a DTO in Python
    # with `id=...` rather than the awkward `**{"_id": ...}`.
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    id_external_user: int
    role: str
    usecase: str = ""

    def to_prompt_context(self) -> str:
        """
        Render the user as the business context an agent should read.

        Only `role` and `usecase` are rendered: the identifiers exist for the
        API's bookkeeping (see `LOG_ONLY_FIELDS`) and carry nothing a model
        could act on.

        Returns:
            str: One labelled line per populated field, or an empty string when
                there is nothing worth telling the agents.
        """

        lines = []

        if self.role:
            lines.append(f"Cargo/função do usuário: {self.role}")

        if self.usecase:
            lines.append(f"Como o usuário costuma usar o sistema: {self.usecase}")

        return "\n".join(lines)


class AekoUserMemory(BaseModel):
    """
    One remembered fact about a user, mirroring the "user_memory" collection.

    The API reads the collection and hands the memories to `AekoMessenger`,
    which renders every one of them into the business context each agent of the
    run reads. This model is that hand-off, so a memory reaches the prompt in
    the one shape the agents' instructions describe.

    Attributes:
        id: The document's `_id`. Owned by the database.
        id_user: The `_id` of the user this memory belongs to.
        field: What the memory is about, e.g. "preferred_language".
        description: The remembered fact itself.
        created_at: When it was recorded.
        expires_at: When it stops being valid. The API is what enforces this;
            the SDK only carries it, and never shows it to a model.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    id_user: str | None = None
    field: str
    description: str
    created_at: datetime | None = None
    expires_at: datetime | None = None

    def to_prompt_line(self) -> str:
        """
        Render this memory as the single line an agent should read.

        Deliberately omits every identifier and `expires_at`: whether a memory
        is still valid is a filter for the API to apply before handing it over,
        not a judgement call to delegate to a model.

        Returns:
            str: A "<field>: <description>" line.
        """

        return f"{self.field}: {self.description}"


class AekoMessage(BaseModel):
    """
    One exchanged turn, mirroring an entry of "session.messages".

    What the turn *cost* is deliberately not here. The model that served it and
    the tokens it burned are reported per agent invocation on the request's
    `AekoMetrics`, which is a finer account of the same thing — carrying a
    rolled-up copy alongside it would be two records of one fact, free to drift
    apart and impossible to tell apart once they had.

    Attributes:
        input: What the user sent.
        output: The answer delivered back. Empty when the run produced none —
            the output guardrail can reject a draft past its retry cap.
        submitted_at: When the turn was answered.
    """

    input: str
    output: str = ""
    submitted_at: datetime = Field(default_factory=_now)


class AekoSession(BaseModel):
    """
    A conversation, mirroring one document of the "session" collection.

    This is what `AekoMessenger.send_message()` takes: the API rehydrates the
    document it persisted, hands it over, and the SDK rebuilds the conversation
    from `messages` and appends the answered turn back to them in place. That
    is how a session resumed on another worker keeps its context without the
    API having to translate anything, and why the SDK caches no session of its
    own.

    Attributes:
        id: The document's `_id`. Owned by the database.
        id_user: The `_id` of the user holding this conversation.
        name: The conversation's display name.
        messages: The turns so far, oldest first.
        created_at: When the conversation started.
        updated_at: When it last received a turn.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    id_user: str | None = None
    name: str = ""
    messages: list[AekoMessage] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AekoMessageResponse(BaseModel):
    """
    The answer returned by `AekoMessenger.send_message()`.

    `message` is the only part that belongs in the database: it is exactly one
    entry of "session.messages", ready to be appended. Everything alongside it
    describes *how* the run reached that answer — useful for logging and
    debugging, and deliberately kept out of the persisted document.

    The identifiers are echoed back from the session that was sent in, for the
    same reason they exist at all: they are what lets the API file this answer
    against the right conversation and user, and log it, without having to
    remember out of band which run it asked for. They stay out of `message`
    because the collection's own entries do not carry them.

    Attributes:
        message: The turn, mirroring "session.messages".
        id_session: The `_id` of the session this answer belongs to.
        id_user: The `_id` of the user who asked.
        agents_called: Names of the agents that contributed, in call order.
        approved: Whether the output guardrail approved the answer.
        guardrail_retries: How many times the guardrail sent the draft back.
        aeko_metrics: What this request cost and went through, for the API to
            persist on its own. Kept out of `message` for the same reason the
            identifiers are: the collection's entries do not carry it.
    """

    message: AekoMessage
    aeko_metrics: AekoMetrics
    id_session: str | None = None
    id_user: str | None = None
    agents_called: list[str] = Field(default_factory=list)
    approved: bool = False
    guardrail_retries: int = Field(default=0, ge=0)


class AekoImprovementPlan(BaseModel):
    """
    The plan returned by `AekoInventoryAnalyzer.analyze()`, mirroring one
    document of the "improvement_plan" collection.

    The continuous improvement coordinator is instructed to answer in exactly
    these fields (see its prompt spec), so what the model writes and what the
    API persists are the same three pieces of text — no prose left for the
    caller to split apart.

    Attributes:
        id: The document's `_id`. Owned by the database.
        id_external_inventory: The analyzed inventory's id in the platform.
        defined_problem: The problem the analysis identified.
        method: What to do about it.
        reasoning: Why that method addresses that problem.
        updated_at: When the plan was produced.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    id_external_inventory: int
    defined_problem: str
    method: str
    reasoning: str
    updated_at: datetime = Field(default_factory=_now)


class AekoAnalysisResponse(BaseModel):
    """
    The answer returned by `AekoInventoryAnalyzer.analyze()`.

    `plan` is the only part that belongs in the "improvement_plan" collection:
    it is exactly one document of it, ready to be written. The event tracking
    beside it says how the analysis reached that plan, and the API persists it
    somewhere else — which is why it is an envelope around the plan rather than
    another field of it. A document carrying its own latency would be a
    document the collection never described.

    This mirrors what `AekoMessageResponse` already does for a chat turn, so
    both public flows hand back the same two things: what to store, and what it
    cost to produce.

    Attributes:
        plan: The improvement plan, mirroring "improvement_plan".
        aeko_metrics: What this request cost and went through.
    """

    plan: AekoImprovementPlan
    aeko_metrics: AekoMetrics
