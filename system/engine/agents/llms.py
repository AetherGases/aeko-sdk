from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
import os

load_dotenv()


def create_llms():
    """
    Create the fast and slow chat models used across the agent system.

    Each model falls back to the other on failure.

    Returns:
        tuple[BaseChatModel, BaseChatModel]: The fast LLM (with the slow LLM as
            fallback) and the slow LLM (with the fast LLM as fallback).
    """

    fast_llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=0.7,
        top_p=0.9,
        top_k=50,
        max_tokens=1024,
        api_key=os.getenv("GEMINI_API_KEY")
    )

    slow_llm = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash",
        temperature=0.7,
        top_p=0.9,
        top_k=50,
        max_tokens=1024,
        api_key=os.getenv("GEMINI_API_KEY")
    )

    fast_llm_with_fallback = fast_llm.with_fallbacks([slow_llm])
    slow_llm_with_fallback = slow_llm.with_fallbacks([fast_llm])

    return fast_llm_with_fallback, slow_llm_with_fallback