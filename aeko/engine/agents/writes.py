"""Defer the calls of writing tools until the Consolidador commits them."""

from dataclasses import dataclass

from typing import Any


from langchain_core.tools import StructuredTool


from aeko.shared import current_run


@dataclass
class WriteIntent:
    """
    One call a writing tool received during a run, waiting to be committed.

    Attributes:
        name: The tool's name.
        tool: The real tool, which only the Consolidador invokes.
        args: The arguments the agent called it with.
        status: "staged" until the Consolidador commits it.
    """

    name: str

    tool: Any

    args: dict[str, Any]

    status: str = "staged"


def is_writing(tool: Any) -> bool:
    """Tell whether a tool's metadata flags it as one that changes state."""

    return (getattr(tool, "metadata", None) or {}).get("is_writing") is True


def stage_writes(tool: Any) -> Any:
    """
    Wrap a writing tool so that, inside a run, its calls are recorded, not made.

    Name, description and args schema are kept, so the prompt and the binding
    are identical. With no run bound there is nobody to commit later, so the
    real tool runs at once. Only the sync path is staged (shortcut: the agents
    are invoked synchronously; add a coroutine if that changes).

    Args:
        tool: A LangChain tool.

    Returns:
        Any: The tool itself when it is not flagged writing, otherwise the
            staging wrapper.
    """

    if not is_writing(tool):
        return tool

    def stage(**args: Any) -> Any:
        """Record the call, or make it when there is no run to commit it."""

        run = current_run()

        if run is None:
            return tool.invoke(args)

        run.pending_writes.append(WriteIntent(tool.name, tool, args))

        return {"staged": True}

    return StructuredTool.from_function(
        func=stage,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=tool.return_direct,
        metadata=tool.metadata,
    )
