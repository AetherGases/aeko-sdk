from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - import kept out of runtime to avoid a
    # cycle: aeko.config is the public facade built *on top of* this engine,
    # so the engine must never import it at module load time.
    from aeko.config.dto import AekoTool

DEFAULT_FAST_MODEL = "gemini-3.1-flash-lite"
DEFAULT_SLOW_MODEL = "gemini-3.5-flash"

# Chat answers are short; the inventory flow writes a full report and needs far
# more room (see AekoInventoryAnalyzer, which opts into REPORT_MAX_TOKENS).
DEFAULT_MAX_TOKENS = 1024
DEFAULT_REPORT_MAX_TOKENS = 8192


@dataclass
class AekoRuntime:
    """
    Process-wide configuration for the agent system.

    Holds everything `Aeko.config()` and `AekoMessenger.set_tools()` set, and
    notifies its listeners whenever that changes so caches built from it (the
    agent registry, the compiled graph) can be rebuilt instead of silently
    serving objects made with stale settings.

    Attributes:
        api_key: The Gemini API key. There is no environment fallback: it must
            be supplied through `Aeko.config()`.
        fast_model: Model id backing the router, FAQ, orchestrator and guardrail.
        slow_model: Model id backing the specialist analysts.
        max_tokens: Output cap for the conversational flow.
        report_max_tokens: Output cap for the inventory report flow.
        tools: Agent name to the tools registered for it.
        checkpointer: Optional LangGraph checkpointer for conversation memory.
    """

    api_key: str | None = None
    fast_model: str = DEFAULT_FAST_MODEL
    slow_model: str = DEFAULT_SLOW_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    report_max_tokens: int = DEFAULT_REPORT_MAX_TOKENS
    tools: dict[str, list["AekoTool"]] = field(default_factory=dict)
    checkpointer: Any = None

    _listeners: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def require_api_key(self) -> str:
        """
        Return the configured API key, refusing to fall back to anything else.

        Returns:
            str: The configured API key.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` was never called.
        """

        # Imported here (not at module level) for the same no-cycle reason as
        # the TYPE_CHECKING block above.
        from aeko.config.exceptions import AekoNotConfiguredError

        if not self.api_key:
            raise AekoNotConfiguredError(
                "Aeko is not configured. Call Aeko.config(<api_key>) before using the SDK."
            )

        return self.api_key

    def tools_for(self, agent: str) -> list["AekoTool"]:
        """
        Return the tools registered for one agent.

        Args:
            agent: The agent's name, as used by the graph.

        Returns:
            list[AekoTool]: Its tools, or an empty list when none were set.
        """

        return self.tools.get(agent, [])

    def on_change(self, listener: Callable[[], None]) -> None:
        """
        Register a callback invoked whenever the configuration changes.

        Args:
            listener: A zero-argument callable, typically a cache invalidator.
        """

        self._listeners.append(listener)

    def notify_changed(self) -> None:
        """Invoke every registered listener, invalidating derived caches."""

        for listener in self._listeners:
            listener()

    def reset(self) -> None:
        """Restore every default, clear registered tools, and invalidate caches."""

        self.api_key = None
        self.fast_model = DEFAULT_FAST_MODEL
        self.slow_model = DEFAULT_SLOW_MODEL
        self.max_tokens = DEFAULT_MAX_TOKENS
        self.report_max_tokens = DEFAULT_REPORT_MAX_TOKENS
        self.tools = {}
        self.checkpointer = None
        self.notify_changed()


RUNTIME = AekoRuntime()
