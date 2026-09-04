from aeko.shared import AekoMetrics


class AekoError(Exception):
    """
    Base class for every error raised by the Aeko SDK.

    Attributes:
        aeko_metrics: What the failed request cost and went through, attached
            on the way out of `processing()` (see aeko/shared/logger.py). A
            request that raised has no return value left to carry it, and a
            failed request is the one the API most needs to have persisted.
            None for an error raised outside any request — a refused
            configuration, or tools registered for an agent that doesn't exist.
    """

    aeko_metrics: AekoMetrics | None = None


class AekoNotConfiguredError(AekoError):
    """Raised when the SDK is used before `Aeko.config()` supplies an API key."""


class MalformedAgentOutputError(AekoError):
    """
    Raised when an agent's answer does not match the shape its prompt demands.

    Only raised where the SDK has to turn an answer into a database document —
    today, the continuous improvement coordinator's `AekoImprovementPlan`. Failing
    here is deliberate: the alternative is handing the API a plan whose fields
    were guessed from prose, which it would then persist as if it were real.
    """


class UnknownAgentError(AekoError):
    """Raised when tools are registered for an agent name that doesn't exist."""

    def __init__(self, agent: str, known_agents: tuple[str, ...]):
        self.agent = agent
        self.known_agents = known_agents
        super().__init__(
            f"'{agent}' is not a known agent. Valid names: {', '.join(known_agents)}."
        )
