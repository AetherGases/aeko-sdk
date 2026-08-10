import os

import pytest
from dotenv import load_dotenv

from system.engine.agents.llms import create_llms

load_dotenv()

requires_gemini_api_key = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is not set; these tests call the real Gemini API.",
)


@pytest.fixture(scope="module")
def llms():
    return create_llms()


def test_get_envs():
    assert os.getenv("GEMINI_API_KEY") is not None, "GEMINI_API_KEY environment variable is not set."


@requires_gemini_api_key
def test_fast_llm_responds(llms):
    fast_llm, _ = llms

    response = fast_llm.invoke("What is the capital of France?")

    assert response.content


@requires_gemini_api_key
def test_slow_llm_responds(llms):
    _, slow_llm = llms

    response = slow_llm.invoke("What is the capital of Germany?")

    assert response.content
