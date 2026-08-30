from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from typing import TYPE_CHECKING, Any

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
    The single place the whole SDK reads its configuration from.

    Everything `Aeko.config()` and `AekoMessenger.set_tools()` set lives here,
    and so does the one thing derived from it: the agent registry, built on
    first use and dropped the moment any setting is written, so a run can never
    be served agents made with stale settings.

    Attributes:
        api_key: The Gemini API key. There is no environment fallback: it must
            be supplied through `Aeko.config()`.
        fast_model: Model id backing the router, FAQ, orchestrator and guardrail.
        slow_model: Model id backing the specialist analysts.
        max_tokens: Output cap for the conversational flow.
        report_max_tokens: Output cap for the inventory report flow.
        tools: Agent name to the tools registered for it.
        agents: Derived, not a setting: the agent registry, keyed by the output
            token cap it was built with. Populated by `agents_for()`.
    """

    api_key: str | None = None
    fast_model: str = DEFAULT_FAST_MODEL
    slow_model: str = DEFAULT_SLOW_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    report_max_tokens: int = DEFAULT_REPORT_MAX_TOKENS
    tools: dict[str, list["AekoTool"]] = field(default_factory=dict)

    agents: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __setattr__(self, name: str, value: Any) -> None:
        """
        Set a field and, unless it is the derived registry, invalidate it.

        The agents are built from these settings, so writing any of them — from
        `Aeko.config()`, from `set_tools()`, or directly — drops them. Doing it
        here rather than in `configure()` is what makes the invalidation an
        invariant instead of a convention nobody can be made to follow.

        Args:
            name: The field being set.
            value: Its new value.
        """

        super().__setattr__(name, value)

        if name != "agents":
            # `agents` does not exist yet while __init__ sets the settings.
            (self.__dict__.get("agents") or {}).clear()

    def configure(self, **settings: Any) -> None:
        """
        Apply the given settings, ignoring the ones left as None.

        A convenience over plain assignment, not a requirement for it: the
        invalidation lives in `__setattr__`, so a direct write is just as safe.
        What this adds is skipping None values and rejecting unknown names.

        Args:
            **settings: Setting name to its new value. A None value means "keep
                whatever is configured", which is how the optional overrides in
                `Aeko.config()` stay optional.

        Raises:
            AttributeError: If a name is not one of the runtime's settings.
        """

        settable = {spec.name for spec in fields(self)} - {"agents"}

        for name, value in settings.items():
            if name not in settable:
                raise AttributeError(f"{name!r} is not a setting of AekoRuntime.")

            if value is not None:
                setattr(self, name, value)

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

    def agents_for(self, max_tokens: int | None = None) -> dict[str, Any]:
        """
        Return the agent registry for an output token cap, building it once.

        The registry is keyed by the cap because that is the only setting a
        single run may deviate from: the inventory flow needs `report_max_tokens`
        where a chat turn needs `max_tokens`, and they cannot share agents.

        Args:
            max_tokens: The cap the agents should be built with, or None for the
                configured conversational one.

        Returns:
            dict[str, Any]: The agents, keyed by the names the graph routes by.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` was never called.
        """

        # Imported here (not at module level) for the same no-cycle reason as
        # the TYPE_CHECKING block above.
        from aeko.engine.agents.agents import create_agents

        max_tokens = max_tokens or self.max_tokens

        # Read into a local before returning: a concurrent write to any setting
        # empties `agents` (see __setattr__), and indexing it again on the way
        # out would raise KeyError if that landed in between. Losing the race
        # this way only costs a rebuild the next call.
        agents = self.agents.get(max_tokens)

        if agents is None:
            agents = create_agents(max_tokens=max_tokens)
            self.agents[max_tokens] = agents

        return agents

    def reset(self) -> None:
        """Restore every default, clear registered tools, and drop the agents."""

        # Not `configure()`: it skips None values, and None is the default the
        # api_key has to go back to. Each `setattr` drops the agents on its own.
        for spec in fields(self):
            if spec.name == "agents":
                continue

            setattr(
                self,
                spec.name,
                spec.default_factory() if spec.default is MISSING else spec.default,
            )


RUNTIME = AekoRuntime()
