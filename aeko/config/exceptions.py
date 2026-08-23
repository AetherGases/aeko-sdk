class AekoError(Exception):
    """Base class for every error raised by the Aeko SDK."""


class AekoNotConfiguredError(AekoError):
    """Raised when the SDK is used before `Aeko.config()` supplies an API key."""


class SessionNotPreparedError(AekoError):
    """Raised when `AekoMessenger.send_message()` runs before `.prepare()`."""


class UnknownAgentError(AekoError):
    """Raised when tools are registered for an agent name that doesn't exist."""

    def __init__(self, agent: str, known_agents: tuple[str, ...]):
        self.agent = agent
        self.known_agents = known_agents
        super().__init__(
            f"'{agent}' is not a known agent. Valid names: {', '.join(known_agents)}."
        )
