"""Process-wide runtime configuration and chat-content normalization."""

from __future__ import annotations


from dataclasses import MISSING, dataclass, field, fields

from typing import TYPE_CHECKING, Any


from langchain_core.messages import AIMessage


from aeko.engine.constants import (
    DEFAULT_FAST_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REPORT_MAX_TOKENS,
    DEFAULT_SLOW_MODEL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
)


if TYPE_CHECKING:
    from aeko.config.dto import AekoTool


@dataclass
class AekoRuntime:
    """
    The single place the whole SDK reads its configuration from.

    `AekoTool` is imported only under TYPE_CHECKING so this engine module never
    loads `aeko.config` at import time: that package is the public facade built
    on top of this engine.

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

    temperature: float = DEFAULT_TEMPERATURE

    top_p: float = DEFAULT_TOP_P

    top_k: int = DEFAULT_TOP_K

    tools: dict[str, list["AekoTool"]] = field(default_factory=dict)

    agents: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __setattr__(self, name: str, value: Any) -> None:
        """
        Set a field and, unless it is the derived registry, invalidate it.

        The agents are built from these settings, so writing any of them — from
        `Aeko.config()`, from `set_tools()`, or directly — drops them. Doing it
        here rather than in `configure()` is what makes the invalidation an
        invariant instead of a convention nobody can be made to follow. The
        `agents` mapping may not exist yet while `__init__` sets the settings.

        Args:
            name: The field being set.
            value: Its new value.
        """

        super().__setattr__(name, value)

        if name != "agents":
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

        The exception class is imported inside the method so this module never
        loads `aeko.config` at import time.

        Returns:
            str: The configured API key.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` was never called.
        """

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
        `create_agents` is imported inside the method so this module never loads
        the agent package at import time. The registry is read into a local
        before returning so a concurrent write that empties `agents` cannot
        raise KeyError on the way out.

        Args:
            max_tokens: The cap the agents should be built with, or None for the
                configured conversational one.

        Returns:
            dict[str, Any]: The agents, keyed by the names the graph routes by.

        Raises:
            AekoNotConfiguredError: If `Aeko.config()` was never called.
        """

        from aeko.engine.agents.agents import create_agents

        max_tokens = max_tokens or self.max_tokens

        agents = self.agents.get(max_tokens)

        if agents is None:
            agents = create_agents(max_tokens=max_tokens)

            self.agents[max_tokens] = agents

        return agents

    def reset(self) -> None:
        """
        Restore every default, clear registered tools, and drop the agents.

        Assignment is used instead of `configure()` because that helper skips
        None values, and None is the default the api_key has to go back to.
        """

        for spec in fields(self):
            if spec.name == "agents":
                continue

            setattr(
                self,
                spec.name,
                spec.default_factory() if spec.default is MISSING else spec.default,
            )


RUNTIME = AekoRuntime()


def text_of(content: Any) -> str:
    """
    Reduce a chat model's content to the plain text the SDK reads it as.

    Since Gemini 3, a model that used a tool answers in *content blocks* — a
    list like `[{"type": "reasoning", ...}, {"type": "text", "text": "..."}]` —
    instead of a bare string. Everything downstream of an agent treats its
    output as text it can split on (the "Next agent: " marker, the plan's
    section headings), so a list reaching any of them raises `AttributeError`
    the moment a tool is actually called. This is the one place that difference
    is absorbed.

    The work is delegated to `AIMessage.text` rather than reimplemented, for
    two reasons: whatever block types LangChain adds next are handled without
    this function knowing about them, and it already drops the blocks that are
    not text — reasoning above all, which is the model's private thinking and
    must never reach a user-facing answer.

    Args:
        content: A message's content, or an agent executor's "output": either a
            string, or a list of content blocks.

    Returns:
        str: The concatenated text blocks, or the string unchanged when the
            content already was one.
    """

    if isinstance(content, str):
        return content

    return AIMessage(content=content).text
