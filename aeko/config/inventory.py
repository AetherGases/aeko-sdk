import json

from aeko.config._text import strip_routing_marker
from aeko.config.dto import ImprovementPlan
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.engine.graph.builder import get_app
from aeko.engine.graph.state import create_initial_state
from aeko.engine.runtime import RUNTIME

INVENTORY_ENTRY_POINT = "Análista de inventários"

# The fields the continuous improvement coordinator is instructed to answer
# with, and the only ones read back from it. Everything else an
# `ImprovementPlan` carries — `_id`, `updated_at` — belongs to the database and
# to this SDK respectively, so the model is never given a say in them.
PLAN_FIELDS = ("defined_problem", "method", "reasoning")


def _json_object_in(answer: str) -> dict:
    """
    Extract the JSON object the coordinator was told to answer with.

    Reads from the first "{" to the last "}" rather than parsing the whole
    string, which tolerates the one deviation a model still makes now and
    then — wrapping the object in a code fence or a closing remark — without
    tolerating an answer that simply isn't the requested object.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.

    Returns:
        dict: The decoded object.

    Raises:
        MalformedAgentOutputError: If no JSON object can be decoded.
    """

    start, end = answer.find("{"), answer.rfind("}")

    if start == -1 or end < start:
        raise MalformedAgentOutputError(
            "O Coordenador de Melhoria Contínua não retornou um objeto JSON: "
            f"{answer!r}"
        )

    try:
        payload = json.loads(answer[start:end + 1])
    except json.JSONDecodeError as error:
        raise MalformedAgentOutputError(
            f"O plano de melhoria retornado não é um JSON válido: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise MalformedAgentOutputError(
            f"O plano de melhoria retornado não é um objeto JSON: {payload!r}"
        )

    return payload


def _to_improvement_plan(answer: str, id_external_inventory: int) -> ImprovementPlan:
    """
    Turn the coordinator's answer into the document the API will persist.

    Only `PLAN_FIELDS` are read from the model. An answer missing any of them is
    rejected outright instead of being padded with defaults: the alternative is
    handing the API a plan whose fields were invented here, which it would then
    store as if the analysis had produced them.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.
        id_external_inventory: The analyzed inventory's id in the platform.

    Returns:
        ImprovementPlan: The plan, ready to be written to "improvement_plan".

    Raises:
        MalformedAgentOutputError: If the answer isn't the requested JSON object,
            or leaves any of the plan's fields empty.
    """

    payload = _json_object_in(answer)

    missing = [
        field for field in PLAN_FIELDS
        if not isinstance(payload.get(field), str) or not payload[field].strip()
    ]

    if missing:
        raise MalformedAgentOutputError(
            "O plano de melhoria retornado não preencheu: " + ", ".join(missing)
        )

    return ImprovementPlan(
        id_external_inventory=id_external_inventory,
        defined_problem=payload["defined_problem"],
        method=payload["method"],
        reasoning=payload["reasoning"],
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

    def analyze(self, inventory: str, id_external_inventory: int) -> ImprovementPlan:
        """
        Analyze a GHG inventory and return the improvement plan.

        Runs with the report token cap rather than the conversational one: this
        flow writes a full report, which the chat-sized cap would truncate.

        Args:
            inventory: The inventory spreadsheet, rendered as Markdown.
            id_external_inventory: The inventory's id in the Aether platform,
                which is what ties the resulting plan back to it. The SDK never
                reads the database, so this cannot be derived here.

        Returns:
            ImprovementPlan: The plan, mirroring one document of the
                "improvement_plan" collection.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
            MalformedAgentOutputError: If the coordinator's answer doesn't match
                the shape its prompt demands.
        """

        state = create_initial_state(inventory, company_context=self._context)

        result = get_app().invoke(
            state,
            config={
                "configurable": {
                    "entry_point": INVENTORY_ENTRY_POINT,
                    "max_tokens": RUNTIME.report_max_tokens,
                }
            },
        )

        messages = result.get("messages") or []
        final = messages[-1] if len(messages) > len(state["messages"]) else None
        answer = "" if final is None else strip_routing_marker(
            final.get("content", "") if isinstance(final, dict) else getattr(final, "content", "")
        )

        return _to_improvement_plan(answer, id_external_inventory)
