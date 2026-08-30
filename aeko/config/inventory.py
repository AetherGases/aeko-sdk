from aeko.config._text import parse_sections, strip_routing_marker
from aeko.config.dto import AekoImprovementPlan
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.engine.graph.builder import get_app
from aeko.engine.graph.nodes import PLAN_FORMAT_MAX_RETRIES
from aeko.engine.graph.state import create_initial_state
from aeko.engine.prompts import PLAN_SECTIONS
from aeko.engine.runtime import RUNTIME

INVENTORY_ENTRY_POINT = "Análista de inventários"

# The fields the continuous improvement coordinator is instructed to answer
# with, and the only ones read back from it. They come from the same mapping
# its prompt is written from (see `PLAN_SECTIONS`), so the sections the agent
# is taught and the ones read here cannot drift apart. Everything else an
# `AekoImprovementPlan` carries — `_id`, `updated_at` — belongs to the database
# and to this SDK respectively, so the model is never given a say in them.
PLAN_FIELDS = tuple(PLAN_SECTIONS)


def _plan_sections_in(answer: str) -> dict[str, str]:
    """
    Read the plan sections an answer actually filled in.

    Strips the routing marker itself, so the same reading applies whether the
    answer comes from the graph's final message or straight out of the agent
    mid-run, when the node is still deciding whether to ask for a rewrite.

    Args:
        answer: The coordinator's answer, with or without its routing marker.

    Returns:
        dict[str, str]: The sections that carry text, keyed by plan field. A
            section left empty counts as never written.
    """

    sections = parse_sections(strip_routing_marker(answer), PLAN_SECTIONS)

    return {field: text for field, text in sections.items() if text} # Because section.name can be None


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

    def analyze(self, inventory: str, *, id_external_inventory: int) -> AekoImprovementPlan:
        """
        Analyze a GHG inventory and return the improvement plan.

        Runs with the report token cap rather than the conversational one: this
        flow writes a full report, which the chat-sized cap would truncate.

        An answer that doesn't carry the three plan sections is sent back to the
        coordinator to be rewritten, up to `PLAN_FORMAT_MAX_RETRIES` times, from
        inside the graph — only the coordinator answers again, not the analysts
        before it. The error below is what an exhausted retry looks like.

        Args:
            inventory: The inventory spreadsheet, rendered as Markdown.
            id_external_inventory: The inventory's id in the Aether platform,
                which is what ties the resulting plan back to it. The SDK never
                reads the database, so this cannot be derived here. Keyword-only:
                two arguments that are both "the inventory" are worth naming at
                every call site.

        Returns:
            AekoImprovementPlan: The plan, mirroring one document of the
                "improvement_plan" collection.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
            MalformedAgentOutputError: If the coordinator's answer still doesn't
                match the shape its prompt demands after every retry.
        """

        state = create_initial_state(inventory, company_context=self._context)

        result = get_app().invoke(
            state,
            config={
                "configurable": {
                    "entry_point": INVENTORY_ENTRY_POINT,
                    "max_tokens": RUNTIME.report_max_tokens,
                    # What this flow needs the coordinator's answer to look
                    # like. The graph itself has no opinion on that, so the
                    # node asks for a rewrite through this and nothing else
                    # in the engine has to know what a plan is.
                    "validate_answer": _format_problems_in,
                }
            },
        )

        messages = result.get("messages") or []
        final = messages[-1] if messages else None
        answer = "" if final is None else strip_routing_marker(
            final.get("content", "") if isinstance(final, dict) else getattr(final, "content", "")
        )

        return _to_improvement_plan(answer, id_external_inventory)
