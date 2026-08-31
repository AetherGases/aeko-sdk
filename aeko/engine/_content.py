from typing import Any

from langchain_core.messages import AIMessage


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
