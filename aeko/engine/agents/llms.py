from langchain_google_genai import ChatGoogleGenerativeAI

from aeko.engine.runtime import RUNTIME


def create_llms(api_key: str | None = None, *, fast_model: str | None = None,
                slow_model: str | None = None, max_tokens: int | None = None):
    """
    Create the fast and slow chat models used across the agent system.

    Every setting falls back to the process-wide runtime configured through
    `Aeko.config()`. There is deliberately no environment-variable fallback for
    the API key: the SDK is consumed by an API that passes the key in, and a
    silent fallback would let a misconfigured deployment run against the wrong
    credentials instead of failing loudly.

    Args:
        api_key: The Gemini API key. Defaults to the configured one.
        fast_model: Model id for the fast LLM. Defaults to the configured one.
        slow_model: Model id for the slow LLM. Defaults to the configured one.
        max_tokens: Output token cap for both models. Defaults to the
            configured conversational cap.

    Returns:
        tuple[BaseChatModel, BaseChatModel]: The fast LLM (with the slow LLM as
            fallback) and the slow LLM (with the fast LLM as fallback).

    Raises:
        AekoNotConfiguredError: If no API key was given or configured.
    """

    api_key = api_key or RUNTIME.require_api_key()
    fast_model = fast_model or RUNTIME.fast_model
    slow_model = slow_model or RUNTIME.slow_model
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