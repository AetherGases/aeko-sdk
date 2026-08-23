from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from aeko.config.aeko import Aeko
from aeko.config.exceptions import AekoNotConfiguredError
from aeko.engine.agents.llms import create_llms
from aeko.engine.runtime import DEFAULT_FAST_MODEL, DEFAULT_MAX_TOKENS, DEFAULT_SLOW_MODEL

API_KEY = "fake-api-key"

# Model ids that the stub below is told to fail on, to exercise the fallback.
UNAVAILABLE: set[str] = set()


def _stub_generate(self, messages: list[BaseMessage], stop: Any = None,
                   run_manager: Any = None, **kwargs: Any) -> ChatResult:
    """Answer as the real client would, without ever calling Gemini."""

    if self.model in UNAVAILABLE:
        raise RuntimeError(f"{self.model} is unavailable")

    return ChatResult(
        generations=[ChatGeneration(message=AIMessage(content=f"resposta de {self.model}"))]
    )


@pytest.fixture(autouse=True)
def stubbed_gemini():
    """Stub the Gemini client's generation call for every test in this module."""

    UNAVAILABLE.clear()
    with patch.object(ChatGoogleGenerativeAI, "_generate", _stub_generate):
        yield
    UNAVAILABLE.clear()


@pytest.fixture
def llms():
    Aeko.config(API_KEY)
    return create_llms()


def test_create_llms_requires_configuration():
    with pytest.raises(AekoNotConfiguredError):
        create_llms()


def test_create_llms_does_not_read_the_environment(monkeypatch):
    # The SDK is consumed by an API that passes the key in; a silent env
    # fallback would let a misconfigured deployment run on the wrong key.
    monkeypatch.setenv("GEMINI_API_KEY", "key-from-the-environment")

    with pytest.raises(AekoNotConfiguredError):
        create_llms()


def test_fast_llm_responds(llms):
    fast_llm, _ = llms

    response = fast_llm.invoke("What is the capital of France?")

    assert response.content


def test_slow_llm_responds(llms):
    _, slow_llm = llms

    response = slow_llm.invoke("What is the capital of Germany?")

    assert response.content


def test_llms_use_the_configured_models(llms):
    fast_llm, slow_llm = llms

    assert fast_llm.runnable.model.endswith(DEFAULT_FAST_MODEL)
    assert slow_llm.runnable.model.endswith(DEFAULT_SLOW_MODEL)


def test_configured_models_and_cap_are_honored():
    Aeko.config(API_KEY, fast_model="gemini-outro-rapido", slow_model="gemini-outro-lento",
                max_tokens=4096)

    fast_llm, slow_llm = create_llms()

    assert fast_llm.runnable.model.endswith("gemini-outro-rapido")
    assert slow_llm.runnable.model.endswith("gemini-outro-lento")
    assert fast_llm.runnable.max_output_tokens == 4096


def test_explicit_arguments_override_the_configuration():
    Aeko.config(API_KEY)

    fast_llm, _ = create_llms("outra-chave", fast_model="gemini-sob-medida", max_tokens=8192)

    assert fast_llm.runnable.model.endswith("gemini-sob-medida")
    assert fast_llm.runnable.max_output_tokens == 8192


def test_default_token_cap_is_the_conversational_one(llms):
    fast_llm, _ = llms

    assert fast_llm.runnable.max_output_tokens == DEFAULT_MAX_TOKENS


def test_fast_llm_falls_back_to_the_slow_one(llms):
    fast_llm, _ = llms
    UNAVAILABLE.add(fast_llm.runnable.model)

    response = fast_llm.invoke("What is the capital of France?")

    assert DEFAULT_SLOW_MODEL in response.content


def test_slow_llm_falls_back_to_the_fast_one(llms):
    _, slow_llm = llms
    UNAVAILABLE.add(slow_llm.runnable.model)

    response = slow_llm.invoke("What is the capital of Germany?")

    assert DEFAULT_FAST_MODEL in response.content
