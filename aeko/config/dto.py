from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AekoTool:
    """
    A tool made available to one agent, with the description the model reads.

    The description is rendered into the agent's "# Ferramentas Disponiveis"
    prompt section *and* the same tool object is bound to the agent's executor,
    so what the prompt promises and what the agent can actually call always come
    from this single declaration.

    Attributes:
        tool: The LangChain tool object to bind to the agent.
        description: How the agent should decide to use it. Falls back to the
            tool's own `.description` when left empty.
    """

    tool: Any
    description: str = ""

    @property
    def name(self) -> str:
        """
        The tool's name, as the model will see it in a tool call.

        Returns:
            str: The wrapped tool's `.name`.
        """

        return getattr(self.tool, "name", type(self.tool).__name__)

    def to_prompt_line(self) -> str:
        """
        Render this tool as one line of the prompt's tool section.

        Returns:
            str: A "<name> - <description>" line, matching the format the
                existing prompt specs already use.
        """

        description = self.description or getattr(self.tool, "description", "")
        return f"{self.name} - {description}".rstrip(" -")

    @classmethod
    def wrap(cls, tool: "AekoTool | Any") -> "AekoTool":
        """
        Normalize a caller-supplied tool into an `AekoTool`.

        Args:
            tool: Either an `AekoTool` or a bare LangChain tool, in which case
                its own `.description` is used.

        Returns:
            AekoTool: The normalized tool.
        """

        return tool if isinstance(tool, cls) else cls(tool=tool)


@dataclass(frozen=True)
class SessionInfo:
    """
    The handle returned by `AekoMessenger.prepare()`.

    Attributes:
        session_id: The conversation's id, used as the graph's thread id.
        user_info: Free-form information about who is asking, forwarded to the
            agents as company context.
        turns: How many messages the session was hydrated with.
    """

    session_id: str
    user_info: str
    turns: int = 0


@dataclass(frozen=True)
class MessageResponse:
    """
    The answer returned by `AekoMessenger.send_message()`.

    Attributes:
        session_id: The session this answer belongs to.
        answer: The final user-facing text.
        agents_called: Names of the agents that contributed, in call order.
        approved: Whether the output guardrail approved the answer.
        guardrail_retries: How many times the guardrail sent the draft back.
    """

    session_id: str
    answer: str
    agents_called: list[str] = field(default_factory=list)
    approved: bool = False
    guardrail_retries: int = 0


@dataclass(frozen=True)
class InventoryAnalysisResponse:
    """
    The report returned by `AekoInventoryAnalyzer.analyze()`.

    This flow ends at the continuous improvement coordinator, a terminal node
    that never passes through the output guardrail — hence no `approved` field,
    unlike `MessageResponse`.

    Attributes:
        answer: The final improvement plan text.
        agents_called: Names of the agents that contributed, in call order.
        context_used: Whether a previous-report context was set beforehand.
    """

    answer: str
    agents_called: list[str] = field(default_factory=list)
    context_used: bool = False
