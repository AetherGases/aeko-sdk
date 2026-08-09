from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
import os

load_dotenv()


def create_llms():
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