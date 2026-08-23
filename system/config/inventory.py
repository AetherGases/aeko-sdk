from system.config._text import strip_routing_marker
from system.config.dto import InventoryAnalysisResponse
from system.engine.graph.builder import get_app
from system.engine.graph.state import create_initial_state
from system.engine.runtime import RUNTIME

INVENTORY_ENTRY_POINT = "Análista de inventários"


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

    def analyze(self, inventory: str) -> InventoryAnalysisResponse:
        """
        Analyze a GHG inventory and return the improvement plan.

        Runs with the report token cap rather than the conversational one: this
        flow writes a full report, which the chat-sized cap would truncate.

        Args:
            inventory: The inventory spreadsheet, rendered as Markdown.

        Returns:
            InventoryAnalysisResponse: The improvement plan and which agents
                contributed to it.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` hasn't been called.
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

        return InventoryAnalysisResponse(
            answer=answer,
            agents_called=list(result.get("previous_agents") or {}),
            context_used=bool(self._context),
        )
