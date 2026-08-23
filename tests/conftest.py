"""
Shared test doubles.

No test in this suite is allowed to reach the Gemini API: the SDK is exercised
end to end against a scripted chat model instead, so the suite is deterministic,
free, and runnable without credentials.
"""

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

from system.config.aeko import Aeko
from system.config.messenger import AekoMessenger
from system.engine.graph import nodes

# Every agent's system prompt states its identity in this exact shape (see
# `_render_instructions` in system/engine/prompts/builder.py), which is what
# lets one fake model answer as whichever agent is calling it.
PERSONA_MARKER = "Voce é o agente: "

DEFAULT_FAKE_RESPONSE = "Resposta simulada.\nNext agent: Nenhum"


def agent_name_from(messages: list[BaseMessage]) -> str:
    """
    Recover which agent a prompt belongs to from its system message.

    Args:
        messages: The messages the model was called with.

    Returns:
        str: The agent's name, or an empty string when it can't be determined.
    """

    system_content = next((m.content for m in messages if isinstance(m, SystemMessage)), "")

    if PERSONA_MARKER not in system_content:
        return ""

    return system_content.split(PERSONA_MARKER, 1)[1].split(" - ", 1)[0]


class FakeChatModel(BaseChatModel):
    """
    A chat model that answers with a scripted response per agent.

    Attributes:
        responses: Agent name to the exact output that agent should return,
            including its "Next agent: ..." marker.
        default_response: Returned for any agent without a scripted response.
        failing_models: Model ids that should raise instead of answering, used
            to exercise the fallback wiring.
        calls: One (agent name, last human message) tuple per invocation, in
            call order, so tests can assert what an agent actually received.
        system_prompts: Agent name to the system prompt it was last called
            with, so tests can assert on the rendered instructions.
    """

    responses: dict[str, str] = {}
    default_response: str = DEFAULT_FAKE_RESPONSE
    failing_models: tuple[str, ...] = ()
    model: str = "fake-model"
    calls: list[tuple[str, str]] = []
    system_prompts: dict[str, str] = {}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        if self.model in self.failing_models:
            raise RuntimeError(f"model {self.model} is unavailable")

        agent = agent_name_from(messages)
        human = next(
            (m.content for m in reversed(messages) if not isinstance(m, SystemMessage)), ""
        )
        self.calls.append((agent, str(human)))
        self.system_prompts[agent] = str(
            next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        )

        content = self.responses.get(agent, self.default_response).replace("{agent}", agent)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def bind_tools(self, tools, *, tool_choice: str | None = None, **kwargs: Any) -> Runnable:
        return self

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def prompt_for(self, agent: str) -> str:
        """
        Return the last message the given agent was called with.

        Args:
            agent: The agent's name.

        Returns:
            str: The message content, or an empty string if it was never called.
        """

        return next((text for name, text in reversed(self.calls) if name == agent), "")

    def system_prompt_for(self, agent: str) -> str:
        """
        Return the system prompt the given agent was last called with.

        Args:
            agent: The agent's name.

        Returns:
            str: The rendered system prompt, or an empty string if it was never
                called.
        """

        return self.system_prompts.get(agent, "")

    def agents_called(self) -> list[str]:
        """
        Return the agents invoked so far, in call order, without repeats.

        Returns:
            list[str]: The agent names.
        """

        seen = []
        for name, _ in self.calls:
            if name not in seen:
                seen.append(name)

        return seen


@pytest.fixture(autouse=True)
def reset_aeko():
    """Keep configuration, tools and sessions from leaking between tests."""

    Aeko.reset()
    AekoMessenger._sessions.clear()
    yield
    Aeko.reset()
    AekoMessenger._sessions.clear()


@pytest.fixture
def use_fake_llm(monkeypatch):
    """
    Replace both LLMs with a scripted fake, for the whole agent stack.

    Patching at `create_llms` keeps everything above it real — the prompts, the
    agent executors, the graph — while never opening a socket.

    Returns:
        Callable[..., FakeChatModel]: Installs the fake and returns it, so tests
            can inspect `.calls` afterwards.
    """

    def _use(responses: dict[str, str] | None = None, **kwargs: Any) -> FakeChatModel:
        fake = FakeChatModel(responses=responses or {}, **kwargs)
        monkeypatch.setattr(
            "system.engine.agents.agents.create_llms", lambda *a, **k: (fake, fake)
        )
        nodes.reset_agents()
        return fake

    yield _use

    nodes.reset_agents()
