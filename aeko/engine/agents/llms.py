from langchain_google_genai import ChatGoogleGenerativeAI

from aeko.engine.runtime import RUNTIME


def create_llms(max_tokens: int | None = None):
    """
    Create the fast and slow chat models used across the agent system.

    Every setting comes from the process-wide runtime configured through
    `Aeko.config()`. There is deliberately no environment-variable fallback for
    the API key: the SDK is consumed by an API that passes the key in, and a
    silent fallback would let a misconfigured deployment run against the wrong
    credentials instead of failing loudly.

    Args:
        max_tokens: Output token cap for both models. Defaults to the
            configured conversational cap, which is how the inventory report
            flow asks for more room than a chat answer.

    Returns:
        tuple[BaseChatModel, BaseChatModel]: The fast LLM (with the slow LLM as
            fallback) and the slow LLM (with the fast LLM as fallback).

    Raises:
        AekoNotConfiguredError: If no API key was configured.
    """

    api_key = RUNTIME.require_api_key()
    fast_model = RUNTIME.fast_model
    slow_model = RUNTIME.slow_model
    max_tokens = max_tokens or RUNTIME.max_tokens

    fast_llm = ChatGoogleGenerativeAI(
        model=fast_model,
        temperature=0.7,
        top_p=0.9,
        top_k=50,
        max_tokens=max_tokens,
        api_key=api_key,
    )

    slow_llm = ChatGoogleGenerativeAI(
        model=slow_model,
        temperature=0.7,
        top_p=0.9,
        top_k=50,
        max_tokens=max_tokens,
        api_key=api_key,
    )

    fast_llm_with_fallback = fast_llm.with_fallbacks([slow_llm])
    slow_llm_with_fallback = slow_llm.with_fallbacks([fast_llm])

    return fast_llm_with_fallback, slow_llm_with_fallback