"""Inventory analysis API for generating improvement plans."""

import json
import re

from pydantic import ValidationError

from aeko.config._text import parse_sections, strip_routing_marker
from aeko.config.constants import INVENTORY_ENTRY_POINT, INVENTORY_LOG_MODULE
from aeko.config.dto import (
    AekoAnalysisResponse,
    AekoCatalogItem,
    AekoCategoryCatalogItem,
    AekoExtractedInventory,
    AekoImprovementPlan,
    AekoInventoryCatalogs,
)
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.engine._content import text_of
from aeko.engine.graph.builder import get_app
from aeko.engine.graph.nodes import PLAN_FORMAT_MAX_RETRIES
from aeko.engine.graph.state import create_initial_state
from aeko.engine.prompts import PLAN_SECTIONS
from aeko.engine.runtime import RUNTIME
from aeko.shared import Flow, processing

LOG_MODULE = INVENTORY_LOG_MODULE

PLAN_FIELDS = tuple(PLAN_SECTIONS)

_INVENTORY_BLOCK = re.compile(r"```inventory\s*\n(.*?)```", re.DOTALL)


def _plan_sections_in(answer: str) -> dict[str, str]:
    """
    Read the plan sections an answer actually filled in.

    Strips the routing marker and the extracted-inventory fence first, so the
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
        f"A seção \"## {PLAN_SECTIONS[field]}\" está ausente ou vazia, e é obrigatória."
        for field in PLAN_FIELDS
        if field not in sections
    ]


def _without_inventory_block(answer: str) -> str:
    """
    Remove the fenced inventory JSON from a coordinator answer, if present.

    Args:
        answer: The coordinator's answer.

    Returns:
        str: The answer without the ```inventory block, stripped.
    """

    match = _INVENTORY_BLOCK.search(answer)
    if match is None:
        return answer
    return (answer[:match.start()] + answer[match.end():]).strip()


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


def _render_catalogs(catalogs: AekoInventoryCatalogs) -> str:
    """
    Render the catalogs as the prompt section inventory-flow agents read.

    Args:
        catalogs: The catalogs of this call.

    Returns:
        str: A labelled list of id + name, with category classification.
    """

    def _lines(title: str, items: list[AekoCatalogItem] | list[AekoCategoryCatalogItem]) -> list[str]:
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


def _to_extracted_inventory(
    answer: str, catalogs: AekoInventoryCatalogs
) -> AekoExtractedInventory:
    """
    Read the structured inventory out of the coordinator's answer.

    The payload lives in a fenced ```inventory JSON block, separate from the
    three plan headings, so a truncated JSON object cannot take the plan
    sections down with it.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.
        catalogs: The catalogs of the same call, which every foreign key must
            belong to.

    Returns:
        AekoExtractedInventory: The payload to persist to Postgres.

    Raises:
        MalformedAgentOutputError: If the block is missing, is not JSON, or
            violates the inventory contract.
    """

    match = _INVENTORY_BLOCK.search(answer)
    if match is None:
        raise MalformedAgentOutputError(
            "O Coordenador de Melhoria Contínua não devolveu o inventário "
            "estruturado no bloco ```inventory pedido."
        )

    try:
        payload = json.loads(match.group(1).strip())
    except json.JSONDecodeError as exc:
        raise MalformedAgentOutputError(
            f"O inventário estruturado não é um JSON válido: {exc}"
        ) from exc

    try:
        return AekoExtractedInventory.model_validate(
            payload, context=_catalog_validation_context(catalogs)
        )
    except ValidationError as exc:
        raise MalformedAgentOutputError(
            f"O inventário estruturado viola o contrato de persistência: {exc}"
        ) from exc


def _to_improvement_plan(answer: str, id_external_inventory: int) -> AekoImprovementPlan:
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
        before it. A valid plan whose inventory block is missing or invalid is
        refused the same way, without handing the caller a partial payload.

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

        catalogs = AekoInventoryCatalogs.model_validate({
            "gases": gases,
            "scopes": scopes,
            "categories": categories,
        })

        state = create_initial_state(
            inventory,
            company_context=self._context,
            catalog_context=_render_catalogs(catalogs),
        )

        with processing(Flow.REPORT, LOG_MODULE, id_request) as run:
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
            answer = "" if final is None else strip_routing_marker(text_of(
                final.get("content", "") if isinstance(final, dict) else getattr(final, "content", "")
            ))

            plan = _to_improvement_plan(answer, id_external_inventory)
            extracted = _to_extracted_inventory(answer, catalogs)

        return AekoAnalysisResponse(
            plan=plan,
            inventory=extracted,
            aeko_metrics=run.event_tracking(),
        )
