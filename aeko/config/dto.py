"""Data transfer objects exposed by the SDK configuration layer."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from aeko.shared import AekoMetrics
from aeko.config.constants import LOG_ONLY_FIELDS



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


class AekoCatalogItem(BaseModel):
    """
    One row of a gas or scope catalog sent by ms-aeko into `analyze()`.

    The integer `id` is the Postgres primary key the extracted inventory must
    reuse as a foreign key. `name` is only a label for the model to read.

    Attributes:
        id: The catalog row's id in the caller's database.
        name: A human-readable label (common name, formula, or another wording
            the caller chooses).
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str


class AekoCategoryCatalogItem(BaseModel):
    """
    One row of the category catalog sent by ms-aeko into `analyze()`.

    `classification` decides `is_upstream` on a non-reduction emission: true
    for UPSTREAM, false for DOWNSTREAM, and None when the classification itself
    is null (typical of Scope 1/2 categories).

    Attributes:
        id: The catalog row's id in the caller's database.
        name: A human-readable label.
        classification: `UPSTREAM`, `DOWNSTREAM`, or None. An empty string is
            not a null classification.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    classification: Literal["UPSTREAM", "DOWNSTREAM"] | None = None


class AekoInventoryCatalogs(BaseModel):
    """
    The three auxiliary catalogs a single `analyze()` call is allowed to cite.

    Duplicate ids inside one catalog are a call error: the SDK would otherwise
    have no way to tell which row an extracted foreign key referred to.

    Attributes:
        gases: Rows of the `gas` table, each with `id` and `name`.
        scopes: Rows of the `scope` table, each with `id` and `name`.
        categories: Rows of the `category` table, each with `id`, `name` and
            `classification`.
    """

    model_config = ConfigDict(extra="forbid")

    gases: list[AekoCatalogItem]
    scopes: list[AekoCatalogItem]
    categories: list[AekoCategoryCatalogItem]

    @field_validator("gases", "scopes", "categories")
    @classmethod
    def ids_are_unique(cls, items: list) -> list:
        """
        Reject a catalog that lists the same id more than once.

        Args:
            items: The catalog rows being validated.

        Returns:
            list: The same rows, when every id is unique.

        Raises:
            ValueError: If two rows share an id.
        """

        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog ids must be unique")
        return items


class AekoInventoryEmission(BaseModel):
    """
    One extracted line destined for Postgres `emission` or `reduction`.

    `is_reduction` selects the table. An emission (`false`) must cite gas,
    scope and category ids from the catalogs of the same call, and its
    `is_upstream` must match that category's classification. A reduction
    (`true`) keeps `quantity_co2e` and `category` and leaves the extra fields
    as None.

    Attributes:
        quantity_co2e: The extracted quantity. Must be >= 0 for an emission.
        methodology_description: Optional methodology text, at most 150
            characters. Must be None on a reduction.
        supplier_data_percentage: Optional percentage in [0, 100]. Must be
            None on a reduction.
        gas: Catalog id of the gas. Required on an emission, None on a reduction.
        scope: Catalog id of the scope. Required on an emission, None on a reduction.
        category: Catalog id of the category. Required on both kinds of row.
        is_upstream: Derived from the category classification on an emission;
            None on a reduction.
        is_reduction: False writes to `emission`; true writes to `reduction`.
    """

    model_config = ConfigDict(extra="forbid")

    quantity_co2e: float
    methodology_description: str | None = None
    supplier_data_percentage: float | None = None
    gas: int | None = None
    scope: int | None = None
    category: int | None = None
    is_upstream: bool | None = None
    is_reduction: bool

    @field_validator("methodology_description")
    @classmethod
    def methodology_fits_varchar(cls, value: str | None) -> str | None:
        """
        Reject a methodology longer than the Postgres VARCHAR(150).

        Args:
            value: The extracted methodology, or None when the markdown had none.

        Returns:
            str | None: The same value, when it fits.

        Raises:
            ValueError: If the string is longer than 150 characters.
        """

        if value is not None and len(value) > 150:
            raise ValueError("methodology_description must be at most 150 characters")
        return value

    @field_validator("supplier_data_percentage")
    @classmethod
    def percentage_is_a_ratio_of_one_hundred(cls, value: float | None) -> float | None:
        """
        Reject a supplier-data percentage outside [0, 100].

        Args:
            value: The extracted percentage, or None when the markdown had none.

        Returns:
            float | None: The same value, when it is in range.

        Raises:
            ValueError: If the number is outside [0, 100].
        """

        if value is not None and not 0 <= value <= 100:
            raise ValueError("supplier_data_percentage must be in [0, 100]")
        return value

    @model_validator(mode="after")
    def matches_the_row_kind_and_catalogs(self, info: ValidationInfo) -> "AekoInventoryEmission":
        """
        Enforce emission vs reduction rules and catalog membership.

        Catalog membership is checked only when the caller supplied a context
        with `gas_ids`, `scope_ids`, `category_ids` and `classifications`,
        which `analyze()` always does. Direct construction without that
        context still validates the row kind (which fields may be filled).

        Args:
            info: Pydantic validation info, whose `context` may carry catalogs.

        Returns:
            AekoInventoryEmission: This row, when it is persistable.

        Raises:
            ValueError: If the row kind, ids or `is_upstream` are inconsistent.
        """

        catalogs: dict[str, Any] = info.context or {}

        if self.is_reduction:
            extras = (
                self.methodology_description,
                self.supplier_data_percentage,
                self.gas,
                self.scope,
                self.is_upstream,
            )
            if any(value is not None for value in extras):
                raise ValueError("reduction rows must leave extra fields as null")
            if self.category is None:
                raise ValueError("reduction rows require a category id")
            category_ids = catalogs.get("category_ids")
            if category_ids is not None and self.category not in category_ids:
                raise ValueError("category id is not in the categories catalog")
            return self

        if self.quantity_co2e < 0:
            raise ValueError("quantity_co2e must be >= 0 for an emission")
        if self.gas is None or self.scope is None or self.category is None:
            raise ValueError("emission rows require gas, scope and category ids")

        gas_ids = catalogs.get("gas_ids")
        scope_ids = catalogs.get("scope_ids")
        category_ids = catalogs.get("category_ids")
        classifications = catalogs.get("classifications")

        if gas_ids is not None and self.gas not in gas_ids:
            raise ValueError("gas id is not in the gases catalog")
        if scope_ids is not None and self.scope not in scope_ids:
            raise ValueError("scope id is not in the scopes catalog")
        if category_ids is not None and self.category not in category_ids:
            raise ValueError("category id is not in the categories catalog")

        if classifications is not None:
            classification = classifications.get(self.category)
            if classification == "UPSTREAM":
                expected: bool | None = True
            elif classification == "DOWNSTREAM":
                expected = False
            else:
                expected = None
            if self.is_upstream is not expected:
                raise ValueError("is_upstream does not match the category classification")

        return self


class AekoExtractedInventory(BaseModel):
    """
    The structured inventory `analyze()` returns next to the improvement plan.

    This is not a Mongo document. It mirrors the Postgres `inventory` row
    (description and period) plus the lines to persist as `emission` or
    `reduction`. `id_inventory` and timestamps are owned by the microservices
    that write the tables, so they are not here.

    Attributes:
        description: Maps to `inventory.description`. At most 150 characters.
        start_period: ISO 8601 `YYYY-MM-DD`, mapping to
            `inventory.inventorying_period_start`.
        end_period: ISO 8601 `YYYY-MM-DD`, mapping to
            `inventory.inventorying_period_end`. Must be on or after
            `start_period` when both are present.
        emissions: Lines for `emission` (`is_reduction=false`) or `reduction`
            (`is_reduction=true`). An empty list is valid when the markdown
            had no extractable `quantity_co2e`.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    start_period: str | None = None
    end_period: str | None = None
    emissions: list[AekoInventoryEmission] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def description_fits_varchar(cls, value: str | None) -> str | None:
        """
        Reject a description longer than the Postgres VARCHAR(150).

        Args:
            value: The extracted description, or None.

        Returns:
            str | None: The same value, when it fits.

        Raises:
            ValueError: If the string is longer than 150 characters.
        """

        if value is not None and len(value) > 150:
            raise ValueError("description must be at most 150 characters")
        return value

    @field_validator("start_period", "end_period")
    @classmethod
    def period_is_iso_date(cls, value: str | None) -> str | None:
        """
        Reject a period that is not a calendar date `YYYY-MM-DD`.

        Args:
            value: The extracted period, or None.

        Returns:
            str | None: The same value, when it is an ISO date.

        Raises:
            ValueError: If the string is not `YYYY-MM-DD`.
        """

        if value is None:
            return value
        if len(value) != 10:
            raise ValueError("periods must be ISO 8601 dates (YYYY-MM-DD)")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("periods must be ISO 8601 dates (YYYY-MM-DD)") from exc
        return value

    @model_validator(mode="after")
    def end_is_not_before_start(self) -> "AekoExtractedInventory":
        """
        Reject a period whose end precedes its start.

        Returns:
            AekoExtractedInventory: This inventory, when the period is ordered.

        Raises:
            ValueError: If both dates are present and `end_period` is earlier.
        """

        if self.start_period and self.end_period and self.end_period < self.start_period:
            raise ValueError("end_period must be on or after start_period")
        return self


class AekoSummaryResponse(BaseModel):
    """
    The answer returned by `AekoMessenger.generate_summary()`.

    `summary` is the only part the memory worker persists as a `user_memory`
    description. The event tracking beside it says how the summary was produced,
    and the API persists it somewhere else — which is why it is an envelope
    around the text rather than another field of it.

    Attributes:
        summary: The conversation summary, ready to be written to "user_memory".
        aeko_metrics: What this request cost and went through.
    """

    summary: str
    aeko_metrics: AekoMetrics


class AekoAnalysisResponse(BaseModel):
    """
    The answer returned by `AekoInventoryAnalyzer.analyze()`.

    `plan` is the only part that belongs in the "improvement_plan" collection:
    it is exactly one document of it, ready to be written. `inventory` is the
    sibling payload for Postgres (`inventory` plus `emission`/`reduction`
    lines) and is not a Mongo document. The event tracking beside both says
    how the analysis reached them, and the API persists it somewhere else.

    This mirrors what `AekoMessageResponse` already does for a chat turn, so
    both public flows hand back what to store and what it cost to produce.

    Attributes:
        plan: The improvement plan, mirroring "improvement_plan".
        inventory: The structured inventory aligned with the Postgres schema.
        aeko_metrics: What this request cost and went through.
    """

    plan: AekoImprovementPlan
    inventory: AekoExtractedInventory
    aeko_metrics: AekoMetrics
