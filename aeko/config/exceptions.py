class AekoError(Exception):
    """Base class for every error raised by the Aeko SDK."""


class AekoNotConfiguredError(AekoError):
    """Raised when the SDK is used before `Aeko.config()` supplies an API key."""


class SessionNotPreparedError(AekoError):
    """Raised when `AekoMessenger.send_message()` runs before `.prepare()`."""


class MalformedAgentOutputError(AekoError):
    """
    Raised when an agent's answer does not match the shape its prompt demands.

    Only raised where the SDK has to turn an answer into a database document —
    today, the continuous improvement coordinator's `ImprovementPlan`. Failing
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
