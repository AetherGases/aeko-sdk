from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field

from aeko.shared.context import Flow, current_run

# The attribute a failed request's event tracking is attached to, on the way
# out. A request that raised has no return value to carry it, and a request
# that failed is the one the API most needs to have persisted.
METRICS_ATTR = "aeko_metrics"

# How each flow is named in what the API persists. Deliberately not `Flow.value`:
# that wording opens the log's header line ("Conversational processing finished
# in ..."), and the two are read by different audiences — a person scanning a
# terminal, and a column of a database that has to go on meaning the same thing
# across releases. Renaming one must never silently rename the other.
EVENT_TRACKING_FLOWS = {
    Flow.CONVERSATIONAL: "conversational",
    Flow.REPORT: "analytical",
}


class AekoAgentMetrics(BaseModel):
    """
    What one agent invocation of a request consumed.

    One entry per call, not per agent: the output guardrail's retry loop runs
    the same agents again and again, and a turn that paid for four routings is
    not a turn that paid for one. This is the same accounting the log's agent
    list is written from, so the two can never disagree.

    Attributes:
        name: The agent, under the exact name the graph routes it by.
        input_tokens: Prompt tokens this single invocation consumed, the whole
            tool-calling loop included.
        output_tokens: Completion tokens it produced.
        llm: The model that served it, as the provider reported it. More than
            one name when the cross-model fallback fired inside the call.
        used_tools: The tools it actually reached for, in call order — not the
            ones registered for it, which are the same on every run and would
            say nothing about this one.
    """

    name: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    llm: str = ""
    used_tools: list[str] = Field(default_factory=list)


class AekoMetrics(BaseModel):
    """
    Everything one request is worth recording, handed back for the API to store.

    The SDK writes to no database, so what it observes about a request has to
    travel out with the request's answer or it is lost when the process moves
    on. This is that hand-off — returned alongside the answer on the way out,
    and attached to the exception when there is no answer to return.

    It is never part of a document the API persists as-is: it rides *beside*
    the turn and beside the plan, because neither collection has a field for it.

    Attributes:
        id_request: What the API correlates this request by. The SDK never
            invents one — it is supplied at the call and echoed back here.
        latency: How long the whole request took, in whole milliseconds.
        error_description: Why the request failed, or None when it did not. A
            conversational turn the guardrail never approved counts as failed
            even though it returns normally.
        flow: "conversational" for `send_message`, "analytical" for `analyze`.
        used_agents: The agent invocations, in call order, one entry per call.
    """

    id_request: str
    latency: int = Field(default=0, ge=0)
    error_description: str | None = None
    flow: str
    used_agents: list[AekoAgentMetrics] = Field(default_factory=list)


class AgentCallCollector(UsageMetadataCallbackHandler):
    """
    What one agent invocation consumed, gathered from the invocation itself.

    Inherits the token accounting rather than reimplementing it: LangChain's
    own handler already reads `usage_metadata` and the model name off every
    `on_llm_end`, which is exactly what a per-agent breakdown needs. The one
    thing it does not do is notice a tool being called, which is added here.

    Passed to a single `AgentExecutor.invoke()` through its config, never
    installed process-wide, so an agent that looped through a tool and answered
    on the turn after reports the whole loop as the one call it was — and the
    run-wide callback the messenger already holds goes on collecting the total
    undisturbed.

    Attributes:
        tools_called: The tools this invocation ran, in call order.
    """

    def __init__(self) -> None:
        """Open a collector on one agent invocation."""

        super().__init__()
        self.tools_called: list[str] = []

    def on_tool_start(self, serialized: dict[str, Any], input_str: str,
                      **kwargs: Any) -> None:
        """
        Record one tool invocation, in the order the agent reached for it.

        Args:
            serialized: The tool, as LangChain describes it to a handler.
            input_str: What the tool was called with. Deliberately not read: a
                tool's arguments are the user's own data, and this is written
                to a database the user did not ask to be quoted in.
            **kwargs: The rest of the callback's payload.
        """

        name = (serialized or {}).get("name") or kwargs.get("name")

        if name:
            self.tools_called.append(str(name))

    @property
    def llm(self) -> str:
        """
        Return the model(s) that served this call.

        Returns:
            str: The model names, joined the same way a turn's `llm` field
                joins them, or an empty string when the provider reported none.
        """

        return ", ".join(self.usage_metadata)

    @property
    def input_tokens(self) -> int:
        """
        Return the prompt tokens this call consumed.

        Returns:
            int: The total across every model that served it.
        """

        return sum(usage.get("input_tokens", 0) for usage in self.usage_metadata.values())

    @property
    def output_tokens(self) -> int:
        """
        Return the completion tokens this call produced.

        Returns:
            int: The total across every model that served it.
        """

        return sum(usage.get("output_tokens", 0) for usage in self.usage_metadata.values())


@dataclass
class AgentCall:
    """
    One agent invocation, as both the log line and the event tracking read it.

    A single record rather than one per reader: the log lists the agents a
    request called and so does the event tracking, and keeping two accounts of
    the same calls is how they end up disagreeing.

    Attributes:
        name: The agent, under the exact name the graph routes it by.
        seconds: How long the invocation took.
        input_tokens: Prompt tokens it consumed.
        output_tokens: Completion tokens it produced.
        llm: The model that served it.
        used_tools: The tools it actually called, in call order.
    """

    name: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    llm: str = ""
    used_tools: list[str] = field(default_factory=list)

    def to_metrics(self) -> AekoAgentMetrics:
        """
        Render this call as the entry the API persists.

        The duration is left out: it is the log's way of showing where a slow
        request spent itself, while what the API records per request is the
        one `latency` of the whole thing.

        Returns:
            AekoAgentMetrics: This call, as one entry of `used_agents`.
        """

        return AekoAgentMetrics(
            name=self.name,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            llm=self.llm,
            used_tools=list(self.used_tools),
        )


@contextmanager
def agent_call(agent_name: str) -> Iterator[AgentCallCollector]:
    """
    Measure one agent invocation and record it into the request it belongs to.

    Yields the collector to hand to the invocation's `callbacks`, which is what
    the tokens, the model and the tools are read from — the graph never has to
    know any of that is being counted, only to pass the handle along.

    Nothing is written here. The call becomes one entry of the single record
    its request writes when it ends, and one entry of that request's event
    tracking. A call made outside any request is dropped rather than attributed
    to whatever ran last: the graph can be driven directly, and a run nobody
    opened has no request to belong to.

    The recording happens on the way out whatever happened, so an agent that
    raised is still listed — which is what says where a failed request got to.

    Args:
        agent_name: The agent, under the exact name the graph routes it by.

    Yields:
        AgentCallCollector: The callback handler to invoke the agent with.
    """

    collector = AgentCallCollector()
    started = perf_counter()

    try:
        yield collector
    finally:
        run = current_run()

        if run is not None:
            run.agent_called(AgentCall(
                name=agent_name,
                seconds=perf_counter() - started,
                input_tokens=collector.input_tokens,
                output_tokens=collector.output_tokens,
                llm=collector.llm,
                used_tools=list(collector.tools_called),
            ))
