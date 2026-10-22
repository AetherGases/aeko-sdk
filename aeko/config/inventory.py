"""Inventory analysis API, payload salvage, canonicalization and schema recovery."""

import json

import re


from pydantic import BaseModel, ConfigDict, Field, ValidationError


from aeko.config.constants import (
    INVENTORY_LOG_MODULE,
    INVENTORY_SCHEMA_MAX_RETRIES,
)

from aeko.config.dto import (
    AekoAnalysisResponse,
    AekoCatalogItem,
    AekoCategoryCatalogItem,
    AekoExtractedInventory,
    AekoImprovementPlan,
    AekoInventoryCatalogs,
)

from aeko.config.exceptions import MalformedAgentOutputError

from aeko.engine.agents.llms import create_llms

from aeko.engine.graph.builder import get_app

from aeko.engine.graph.constants import INVENTORY_ENTRY_POINT, PLAN_FORMAT_MAX_RETRIES

from aeko.engine.graph.state import create_initial_state

from aeko.engine.prompts import PLAN_SECTIONS

from aeko.engine.runtime import RUNTIME, text_of

from aeko.shared import Flow, agent_call, processing

from aeko.shared.text import (
    _comparable,
    _heading_in,
    parse_sections,
    strip_routing_marker,
)


PLAN_FIELDS = tuple(PLAN_SECTIONS)


_INVENTORY_BLOCK = re.compile(r"```inventory\s*\n(.*?)```", re.DOTALL)

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

_PYTHON_NONE = re.compile(r"\bNone\b")

_PYTHON_TRUE = re.compile(r"\bTrue\b")

_PYTHON_FALSE = re.compile(r"\bFalse\b")

_ROOT_KEYS = frozenset({"description", "start_period", "end_period", "emissions"})

_ITEM_KEYS = frozenset(
    {
        "quantity_co2e",
        "methodology_description",
        "supplier_data_percentage",
        "gas",
        "scope",
        "category",
        "is_upstream",
        "is_reduction",
    }
)

_ROOT_OPTIONAL = ("description", "start_period", "end_period")

_ITEM_OPTIONAL = ("methodology_description", "supplier_data_percentage")

_ITEM_INTS = ("gas", "scope", "category")


def _plan_sections_in(answer: str) -> dict[str, str]:
    """
    Read the plan sections an answer actually filled in.

    Strips the routing marker and the recovered inventory JSON first, so the
    same reading applies whether the answer comes from the graph's final
    message or straight out of the agent mid-run, and so the JSON payload
    cannot leak into `reasoning`.

    Args:
        answer: The coordinator's answer, with or without its routing marker.

    Returns:
        dict[str, str]: The sections that carry text, keyed by plan field. A
            section left empty counts as never written.
    """

    sections = parse_sections(
        strip_routing_marker(_without_inventory_block(answer)),
        PLAN_SECTIONS,
    )

    return {field: text for field, text in sections.items() if text}


def _format_problems_in(answer: str) -> list[str]:
    """
    List what the coordinator still has to fix in an answer, for the agent to read.

    Handed to the graph as the run's "validate_answer" (see
    `_coordenador_melhoria_node`), which is what lets a format slip cost one
    more call to the coordinator instead of the whole analysis. It is phrased
    for the model, not for the caller: these lines go back into a prompt.

    Args:
        answer: The coordinator's raw answer.

    Returns:
        list[str]: One complaint per missing section, empty when the answer is
            ready to become a plan.
    """

    sections = _plan_sections_in(answer)

    return [
        f'A seção "## {PLAN_SECTIONS[field]}" está ausente ou vazia, e é obrigatória.'
        for field in PLAN_FIELDS
        if field not in sections
    ]


def _without_inventory_block(answer: str) -> str:
    """
    Remove recovered inventory JSON from a coordinator answer, if present.

    The dedicated ```inventory fence is stripped first. When that fence is
    missing, the same unfenced object salvage reads after the reasoning
    section is removed, so it cannot leak into the persisted plan.

    Args:
        answer: The coordinator's answer.

    Returns:
        str: The answer without the recovered inventory JSON, stripped.
    """

    match = _INVENTORY_BLOCK.search(answer)

    if match is not None:
        return (answer[: match.start()] + answer[match.end() :]).strip()

    plan_text = strip_routing_marker(answer)

    tail = _tail_after_reasoning(plan_text)

    if not tail:
        return answer

    snippet = _extract_json_object(tail)

    if snippet is None:
        return answer

    index = answer.find(snippet)

    if index < 0:
        return answer

    return (answer[:index] + answer[index + len(snippet) :]).strip()


def _render_catalogs(catalogs: AekoInventoryCatalogs) -> str:
    """
    Render the catalogs as the prompt section inventory-flow agents read.

    Args:
        catalogs: The catalogs of this call.

    Returns:
        str: A labelled list of id + name, with category classification.
    """

    def _lines(
        title: str, items: list[AekoCatalogItem] | list[AekoCategoryCatalogItem]
    ) -> list[str]:
        """Render one catalog as a titled bullet list."""

        rendered = [f"{title}:"]

        for item in items:
            if isinstance(item, AekoCategoryCatalogItem):
                classification = (
                    "nula" if item.classification is None else item.classification
                )

                rendered.append(
                    f"- {item.id}: {item.name} (classification: {classification})"
                )

            else:
                rendered.append(f"- {item.id}: {item.name}")

        return rendered

    parts = [
        "Catálogos auxiliares vigentes. Use somente os ids listados abaixo.",
        *_lines("Gases", catalogs.gases),
        *_lines("Escopos", catalogs.scopes),
        *_lines("Categorias", catalogs.categories),
    ]

    return "\n".join(parts)


class _InventoryFillEmission(BaseModel):
    """Dump-only emission row used to constrain schema generation."""

    model_config = ConfigDict(extra="allow")

    quantity_co2e: object = None

    methodology_description: object = None

    supplier_data_percentage: object = None

    gas: object = None

    scope: object = None

    category: object = None

    is_upstream: object = None

    is_reduction: object = None


class _InventoryFillShape(BaseModel):
    """Dump-only inventory shape without persistence validators."""

    model_config = ConfigDict(extra="allow")

    description: object = None

    start_period: object = None

    end_period: object = None

    emissions: list[_InventoryFillEmission] = Field(default_factory=list)


def _fill_inventory_from_schema(
    inventory_markdown: str, catalogs: AekoInventoryCatalogs
) -> dict:
    """
    Fill the extracted-inventory schema from markdown and catalogs only.

    Uses the slow Gemini model with constrained structured output against a
    dump-only shape, so persistence validators do not run here. The
    coordinator answer, broken JSON and plan sections are deliberately left
    out: this is the salvage fallback, not a rewrite of the frozen plan.
    Failures propagate so `recover_extracted_inventory` can count the attempt.

    Args:
        inventory_markdown: The inventory document markdown from `analyze()`.
        catalogs: Auxiliary catalogs of the same call.

    Returns:
        dict: A root object matching extracted-inventory fields.

    Raises:
        TypeError: If structured output is neither a dict nor a dumpable model.
    """

    _, slow_llm = create_llms(max_tokens=RUNTIME.report_max_tokens)

    structured = slow_llm.with_structured_output(_InventoryFillShape)

    prompt = f"{inventory_markdown}\n\n{_render_catalogs(catalogs)}"

    with agent_call("Coordenador de Melhoria Contínua") as call:
        result = structured.invoke(prompt, config={"callbacks": [call]})

    if isinstance(result, dict):
        return result

    if hasattr(result, "model_dump"):
        return result.model_dump()

    raise TypeError("A geração estruturada do inventário não devolveu um objeto.")


def _to_improvement_plan(
    answer: str, id_external_inventory: int
) -> AekoImprovementPlan:
    """
    Turn the coordinator's answer into the document the API will persist.

    Only `PLAN_FIELDS` are read from the model, each from the section its
    prompt names. An answer missing any of them is rejected outright instead of
    being padded with defaults: the alternative is handing the API a plan whose
    fields were invented here, which it would then store as if the analysis had
    produced them.

    By the time an answer reaches this point the coordinator has already been
    asked to fix it up to `PLAN_FORMAT_MAX_RETRIES` times, so failing here means
    the format was never produced — not that it slipped once.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.
        id_external_inventory: The analyzed inventory's id in the platform.

    Returns:
        AekoImprovementPlan: The plan, ready to be written to "improvement_plan".

    Raises:
        MalformedAgentOutputError: If the answer isn't written in the requested
            sections, or leaves any of them empty.
    """

    sections = _plan_sections_in(answer)

    if not sections:
        raise MalformedAgentOutputError(
            "O Coordenador de Melhoria Contínua não respondeu nas seções pedidas, "
            f"nem após {PLAN_FORMAT_MAX_RETRIES} tentativas de correção: {answer!r}"
        )

    missing = [field for field in PLAN_FIELDS if field not in sections]

    if missing:
        raise MalformedAgentOutputError(
            f"O plano de melhoria retornado não preencheu, nem após "
            f"{PLAN_FORMAT_MAX_RETRIES} tentativas de correção: "
            + ", ".join(f"{field} (## {PLAN_SECTIONS[field]})" for field in missing)
        )

    return AekoImprovementPlan(
        id_external_inventory=id_external_inventory,
        defined_problem=sections["defined_problem"],
        method=sections["method"],
        reasoning=sections["reasoning"],
    )


def _repair_serialization(raw: str) -> str:
    """
    Repair common coordinator JSON slips before parsing.

    Args:
        raw: A JSON snippet that may use Python literals or trailing commas.

    Returns:
        str: The snippet with those slips rewritten as JSON.
    """

    parts: list[str] = []

    in_string = False

    escape = False

    index = 0

    length = len(raw)

    while index < length:
        if in_string:
            parts.append(raw[index])

            if escape:
                escape = False

            elif raw[index] == "\\":
                escape = True

            elif raw[index] == '"':
                in_string = False

            index += 1

            continue

        trailing = _TRAILING_COMMA.match(raw, index)

        if trailing is not None:
            parts.append(trailing.group(1))

            index = trailing.end()

            continue

        none_match = _PYTHON_NONE.match(raw, index)

        if none_match is not None:
            parts.append("null")

            index = none_match.end()

            continue

        true_match = _PYTHON_TRUE.match(raw, index)

        if true_match is not None:
            parts.append("true")

            index = true_match.end()

            continue

        false_match = _PYTHON_FALSE.match(raw, index)

        if false_match is not None:
            parts.append("false")

            index = false_match.end()

            continue

        if raw[index] == '"':
            in_string = True

        parts.append(raw[index])

        index += 1

    return "".join(parts)


def _load_object(raw: str) -> dict | None:
    """
    Parse a JSON object after serialization repairs.

    Args:
        raw: A candidate JSON object.

    Returns:
        dict | None: The parsed object, or None when parsing fails or the
            result is not a dict.
    """

    try:
        parsed = json.loads(_repair_serialization(raw))

    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _tail_after_reasoning(text: str) -> str:
    """
    Collect the text that follows the plan's reasoning heading.

    Args:
        text: The coordinator answer without its routing marker.

    Returns:
        str: The tail after the last reasoning heading, stripped.
    """

    wanted = {_comparable(label): name for name, label in PLAN_SECTIONS.items()}

    collecting = False

    tail_lines: list[str] = []

    for line in text.splitlines():
        heading = _heading_in(line)

        name = wanted.get(_comparable(heading)) if heading is not None else None

        if name == "reasoning":
            collecting = True

            tail_lines = []

            continue

        if name is not None:
            collecting = False

            continue

        if collecting:
            tail_lines.append(line)

    return "\n".join(tail_lines).strip()


def _extract_json_object(text: str) -> str | None:
    """
    Extract a single top-level JSON object from text.

    Args:
        text: Text that may contain one JSON object.

    Returns:
        str | None: The object slice, or None when none is found or a second
            object follows it.
    """

    start = text.find("{")

    if start < 0:
        return None

    depth = 0

    in_string = False

    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escape:
                escape = False

            elif char == "\\":
                escape = True

            elif char == '"':
                in_string = False

            continue

        if char == '"':
            in_string = True

        elif char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                end = index + 1

                remainder = text[end:].strip()

                if remainder and "{" in remainder:
                    return None

                return text[start:end]

    return None


def salvage_inventory_payload(answer: str) -> dict | None:
    """
    Recover a structured inventory object from a coordinator answer without LLM calls.

    The coordinator may embed JSON in a dedicated ```inventory fence or, when that
    fence is missing, as a single object after the plan's reasoning section. Only
    minimal serialization repairs are applied before parsing.

    Args:
        answer: The coordinator's raw answer, with or without a routing marker.

    Returns:
        dict | None: The parsed inventory object when salvage succeeds, otherwise
            None when no payload is found or it cannot be recovered.
    """

    match = _INVENTORY_BLOCK.search(answer)

    if match is not None:
        return _load_object(match.group(1).strip())

    plan_text = strip_routing_marker(answer)

    tail = _tail_after_reasoning(plan_text)

    if not tail:
        return None

    snippet = _extract_json_object(tail)

    if snippet is None:
        return None

    return _load_object(snippet)


def _coerce_null_string(value: object) -> object:
    """
    Treat the string ``null`` as a missing value.

    Args:
        value: A raw JSON field.

    Returns:
        object: None when the value is the string ``null``, otherwise value.
    """

    if isinstance(value, str) and value.casefold() == "null":
        return None

    return value


def _coerce_bool(value: object) -> object:
    """
    Coerce stringified booleans, leaving other values unchanged.

    Args:
        value: A raw JSON field.

    Returns:
        object: A bool, None, or the original value.
    """

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        folded = value.casefold()

        if folded == "true":
            return True

        if folded == "false":
            return False

        if folded == "null":
            return None

    return value


def _coerce_float(value: object) -> object:
    """
    Coerce numeric strings and ints to float.

    Args:
        value: A raw JSON field.

    Returns:
        object: A float, None, or the original value when coercion fails.
    """

    value = _coerce_null_string(value)

    if value is None:
        return None

    if isinstance(value, bool):
        return float(int(value))

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value)

        except (ValueError, TypeError):
            return value

    return value


def _coerce_int(value: object) -> object:
    """
    Coerce numeric strings and floats to int.

    Args:
        value: A raw JSON field.

    Returns:
        object: An int, None, or the original value when coercion fails.
    """

    value = _coerce_null_string(value)

    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        try:
            return int(float(value))

        except (ValueError, TypeError):
            return value

    return value


def _category_classifications(catalogs: AekoInventoryCatalogs) -> dict[int, str | None]:
    """
    Map category ids to their catalog classification.

    Args:
        catalogs: The catalogs of the same ``analyze()`` call.

    Returns:
        dict[int, str | None]: Category id to UPSTREAM, DOWNSTREAM or None.
    """

    return {row.id: row.classification for row in catalogs.categories}


def _derive_is_upstream(
    category_id: object,
    classifications: dict[int, str | None],
    is_reduction: bool,
) -> bool | None:
    """
    Derive ``is_upstream`` from category classification.

    Args:
        category_id: The emission's category id after coercion.
        classifications: Category id to classification.
        is_reduction: Whether the row is a reduction rather than an emission.

    Returns:
        bool | None: True for UPSTREAM, False for DOWNSTREAM, None otherwise.
    """

    if is_reduction:
        return None

    if category_id is None or not isinstance(category_id, int):
        return None

    classification = classifications.get(category_id)

    if classification == "UPSTREAM":
        return True

    if classification == "DOWNSTREAM":
        return False

    return None


def _canonicalize_emission_item(
    raw: object,
    classifications: dict[int, str | None],
) -> dict:
    """
    Normalize one emission row to the extracted-inventory item shape.

    Args:
        raw: A raw emission object, or a non-dict that becomes an empty item.
        classifications: Category id to classification.

    Returns:
        dict: The contract-shaped emission item.
    """

    source = raw if isinstance(raw, dict) else {}

    filtered = {key: source[key] for key in source if key in _ITEM_KEYS}

    item: dict = {}

    if "quantity_co2e" in filtered:
        item["quantity_co2e"] = _coerce_float(filtered["quantity_co2e"])

    for key in _ITEM_INTS:
        if key in filtered:
            item[key] = _coerce_int(filtered[key])

    for key in _ITEM_OPTIONAL:
        if key in filtered:
            coerced = _coerce_null_string(filtered[key])

            if key == "supplier_data_percentage" and coerced is not None:
                item[key] = _coerce_float(coerced)

            else:
                item[key] = coerced

        else:
            item[key] = None

    is_reduction = False

    if "is_reduction" in filtered:
        coerced = _coerce_bool(filtered["is_reduction"])

        item["is_reduction"] = coerced

        is_reduction = coerced is True

    category_id = item.get("category")

    item["is_upstream"] = _derive_is_upstream(
        category_id, classifications, is_reduction
    )

    return item


def canonicalize_inventory_payload(
    payload: dict, catalogs: AekoInventoryCatalogs
) -> dict:
    """
    Normalize a salvaged inventory dict to the extracted-inventory contract shape.

    Coerces stringified numbers and booleans, fills missing optional fields with
    None, defaults missing ``emissions`` to an empty list, drops unknown keys,
    and derives ``is_upstream`` from category catalog classifications. Does not
    validate foreign keys or invent missing ``gas``, ``scope``, or ``quantity_co2e``.

    Args:
        payload: Raw JSON object from the coordinator.
        catalogs: Gas, scope and category rows for the same ``analyze()`` call.

    Returns:
        dict: A contract-shaped inventory payload ready for Pydantic validation.
    """

    source = payload if isinstance(payload, dict) else {}

    root = {key: source[key] for key in source if key in _ROOT_KEYS}

    result: dict = {}

    for key in _ROOT_OPTIONAL:
        if key in root:
            result[key] = _coerce_null_string(root[key])

        else:
            result[key] = None

    emissions_raw = root.get("emissions", [])

    if not isinstance(emissions_raw, list):
        emissions_raw = []

    classifications = _category_classifications(catalogs)

    result["emissions"] = [
        _canonicalize_emission_item(entry, classifications) for entry in emissions_raw
    ]

    return result


def _catalog_validation_context(catalogs: AekoInventoryCatalogs) -> dict:
    """
    Build the Pydantic context an extracted inventory is validated against.

    Args:
        catalogs: The catalogs of the same `analyze()` call.

    Returns:
        dict: Id sets and category classifications keyed for the emission
            validator.
    """

    return {
        "gas_ids": {item.id for item in catalogs.gases},
        "scope_ids": {item.id for item in catalogs.scopes},
        "category_ids": {item.id for item in catalogs.categories},
        "classifications": {
            item.id: item.classification for item in catalogs.categories
        },
    }


def fill_inventory_schema(
    inventory_markdown: str, catalogs: AekoInventoryCatalogs
) -> dict:
    """
    Production hook for constrained schema generation when salvage fails.

    Delegates to `_fill_inventory_from_schema`. `analyze()` still injects that
    helper so contract tests can patch it by name.

    Args:
        inventory_markdown: The inventory document markdown from the API call.
        catalogs: Auxiliary catalogs rendered into the schema prompt.

    Returns:
        dict: A root inventory object matching `AekoExtractedInventory`.
    """

    return _fill_inventory_from_schema(inventory_markdown, catalogs)


def _validate_canonical_inventory(
    payload: dict,
    catalogs: AekoInventoryCatalogs,
    canonicalize,
) -> AekoExtractedInventory:
    """
    Canonicalize a raw payload and validate it against the persistence contract.

    Args:
        payload: Raw inventory object from salvage or schema generation.
        catalogs: Catalogs that bound every foreign key in emissions.
        canonicalize: Callable that normalizes shape and derived fields.

    Returns:
        AekoExtractedInventory: The validated inventory ready for Postgres.

    Raises:
        MalformedAgentOutputError: If Pydantic rejects the canonical payload.
    """

    try:
        canonical = canonicalize(payload, catalogs)

    except (ValueError, TypeError) as exc:
        raise MalformedAgentOutputError(
            f"O inventário estruturado viola o contrato de persistência: {exc}"
        ) from exc

    context = _catalog_validation_context(catalogs)

    try:
        return AekoExtractedInventory.model_validate(canonical, context=context)

    except ValidationError as exc:
        raise MalformedAgentOutputError(
            f"O inventário estruturado viola o contrato de persistência: {exc}"
        ) from exc


def recover_extracted_inventory(
    answer: str,
    catalogs: AekoInventoryCatalogs,
    inventory_markdown: str,
    *,
    salvage=None,
    canonicalize=None,
    fill_schema=None,
) -> AekoExtractedInventory:
    """
    Recover structured inventory from the coordinator answer or schema fallback.

    Salvage runs first on the coordinator text. When it yields nothing, schema
    generation may run up to `INVENTORY_SCHEMA_MAX_RETRIES` times. Semantic
    validation failures after a dict is obtained do not trigger further schema
    calls.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.
        catalogs: Auxiliary catalogs for the same analyze call.
        inventory_markdown: Inventory document markdown passed to schema fill.
        salvage: Optional override for `salvage_inventory_payload`.
        canonicalize: Optional override for `canonicalize_inventory_payload`.
        fill_schema: Optional override for `fill_inventory_schema`.

    Returns:
        AekoExtractedInventory: Payload validated against catalogs.

    Raises:
        MalformedAgentOutputError: When salvage and schema recovery both fail,
            or when the recovered payload violates the inventory contract.
    """

    if salvage is None:
        salvage = salvage_inventory_payload

    if canonicalize is None:
        canonicalize = canonicalize_inventory_payload

    if fill_schema is None:
        fill_schema = fill_inventory_schema

    salvaged = salvage(answer)

    if salvaged is not None:
        return _validate_canonical_inventory(salvaged, catalogs, canonicalize)

    last_exc: BaseException | None = None

    for _ in range(INVENTORY_SCHEMA_MAX_RETRIES):
        try:
            candidate = fill_schema(inventory_markdown, catalogs)

        except Exception as exc:
            last_exc = exc

            continue

        if not isinstance(candidate, dict):
            continue

        return _validate_canonical_inventory(candidate, catalogs, canonicalize)

    raise MalformedAgentOutputError(
        "O Coordenador de Melhoria Contínua não devolveu um inventário "
        "estruturado recuperável, nem após "
        f"{INVENTORY_SCHEMA_MAX_RETRIES} tentativas de schema."
    ) from last_exc


class AekoInventoryAnalyzer:
    """
    Report entry point: runs a GHG inventory through the analyst flow.

    Enters the graph at the inventory analyst instead of the router, which then
    routes the run through the pollutant and green gas analysts and ends at the
    continuous improvement coordinator — a terminal node, so this flow never
    passes through the output guardrail.
    """

    def __init__(self):
        """
        Open an analyzer with no previous report to build on.

        Unlike `AekoMessenger`, this takes no user: a plan is produced from the
        inventory and the previous report alone, and is filed against the
        inventory rather than against whoever asked for it.
        """

        self._context: str = ""

    def set_context(self, context: str) -> None:
        """
        Set the context carried over from the company's previous report.

        Args:
            context: Free-form information about the last report, forwarded to
                every agent so the new analysis can build on it. A company's
                first report legitimately has none, so this is optional.
        """

        self._context = context or ""

    def analyze(
        self,
        inventory: str,
        *,
        id_external_inventory: int,
        id_request: str,
        gases: list[AekoCatalogItem | dict],
        scopes: list[AekoCatalogItem | dict],
        categories: list[AekoCategoryCatalogItem | dict],
    ) -> AekoAnalysisResponse:
        """
        Analyze a GHG inventory and return the improvement plan and structured inventory.

        Runs with the report token cap rather than the conversational one: this
        flow writes a full report, which the chat-sized cap would truncate.

        The three catalogs are the only ids the extracted inventory may cite.
        They are validated before the graph runs: a malformed catalog is a call
        error, not a model-output error.

        An answer that doesn't carry the three plan sections is sent back to the
        coordinator to be rewritten, up to `PLAN_FORMAT_MAX_RETRIES` times, from
        inside the graph — only the coordinator answers again, not the analysts
        before it. The plan is parsed first; an invalid plan still fails before
        any schema fill. A valid plan whose inventory JSON cannot be salvaged is
        recovered with up to two constrained schema fills. An inventory that
        still cannot be validated is refused, without handing the caller a
        partial payload.

        Args:
            inventory: The inventory spreadsheet, rendered as Markdown.
            id_external_inventory: The inventory's id in the Aether platform,
                which is what ties the resulting plan back to it. The SDK never
                reads the database, so this cannot be derived here. Keyword-only:
                two arguments that are both "the inventory" are worth naming at
                every call site.
            id_request: What the API correlates this request by, echoed back in
                the returned event tracking. Required for the same reason
                `id_external_inventory` is: the SDK reads no database and has
                no way to derive one.
            gases: The gas catalog of this deployment, each item `{id, name}`.
            scopes: The scope catalog of this deployment, each item `{id, name}`.
            categories: The category catalog of this deployment, each item
                `{id, name, classification}` where classification is UPSTREAM,
                DOWNSTREAM or null.

        Returns:
            AekoAnalysisResponse: The plan to persist in "improvement_plan", the
                structured inventory aligned with Postgres, and what producing
                them cost.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
            ValidationError: If a catalog is missing fields, repeats an id, or
                uses a classification outside the allowed set.
            MalformedAgentOutputError: If the coordinator's answer still doesn't
                match the shape its prompt demands after every retry, or if the
                extracted inventory is missing or violates this contract. Its
                `aeko_metrics` carries what the failed analysis went through,
                since there is no response left to carry it back.
        """

        catalogs = AekoInventoryCatalogs.model_validate(
            {
                "gases": gases,
                "scopes": scopes,
                "categories": categories,
            }
        )

        state = create_initial_state(
            inventory,
            company_context=self._context,
            catalog_context=_render_catalogs(catalogs),
        )

        with processing(Flow.REPORT, INVENTORY_LOG_MODULE, id_request) as run:
            run.item("inventory", id_external_inventory)

            run.item("input", f"{len(inventory)} characters")

            result = get_app().invoke(
                state,
                config={
                    "configurable": {
                        "entry_point": INVENTORY_ENTRY_POINT,
                        "max_tokens": RUNTIME.report_max_tokens,
                        "validate_answer": _format_problems_in,
                    }
                },
            )

            messages = result.get("messages") or []

            final = messages[-1] if messages else None

            answer = (
                ""
                if final is None
                else strip_routing_marker(
                    text_of(
                        final.get("content", "")
                        if isinstance(final, dict)
                        else getattr(final, "content", "")
                    )
                )
            )

            plan = _to_improvement_plan(answer, id_external_inventory)

            extracted = recover_extracted_inventory(
                answer,
                catalogs,
                inventory,
                fill_schema=_fill_inventory_from_schema,
            )

        return AekoAnalysisResponse(
            plan=plan,
            inventory=extracted,
            aeko_metrics=run.event_tracking(),
        )
