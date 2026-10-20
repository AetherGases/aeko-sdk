"""Tests for constrained inventory schema fill used after salvage fails."""

from types import SimpleNamespace

import pytest

from aeko import Aeko
from aeko.config.constants import INVENTORY_LOG_MODULE
from aeko.config.dto import AekoExtractedInventory, AekoInventoryCatalogs
from aeko.config.inventory import _fill_inventory_from_schema, _render_catalogs
from aeko.engine.runtime import RUNTIME
from aeko.shared import Flow, processing
from tests.conftest import catalog_kwargs
from tests.test_config import API_KEY, REQUEST_ID

INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"


@pytest.fixture
def configured():
    Aeko.config(API_KEY)


@pytest.fixture
def catalogs() -> AekoInventoryCatalogs:
    return AekoInventoryCatalogs.model_validate(catalog_kwargs())


def _valid_payload() -> dict:
    return {
        "description": "Inventario 2023",
        "start_period": "2023-01-01",
        "end_period": "2023-12-31",
        "emissions": [],
    }


def _install_structured_llm(monkeypatch, result, *, capture=None):
    capture = capture if capture is not None else {}

    class Structured:
        def invoke(self, prompt, config=None):
            capture["prompt"] = prompt
            capture["config"] = config
            capture["callbacks"] = None if config is None else config.get("callbacks")
            if isinstance(result, BaseException):
                raise result
            return result

    class SlowLlm:
        def with_structured_output(self, schema):
            capture["schema"] = schema
            return Structured()

    def _create_llms(max_tokens=None):
        capture["max_tokens"] = max_tokens
        return object(), SlowLlm()

    monkeypatch.setattr("aeko.config.inventory.create_llms", _create_llms)
    return capture


def test_fill_records_coordinator_agent_call(configured, catalogs, monkeypatch):
    payload = _valid_payload()
    capture = _install_structured_llm(monkeypatch, payload)

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID) as run:
        filled = _fill_inventory_from_schema(INVENTORY_MD, catalogs)
        metrics = run.event_tracking()

    assert filled == payload
    assert [agent.name for agent in metrics.used_agents] == [
        "Coordenador de Melhoria Contínua"
    ]
    assert capture["callbacks"] is not None
    assert capture["max_tokens"] == RUNTIME.report_max_tokens
    assert capture["schema"] is AekoExtractedInventory


def test_fill_prompt_is_markdown_and_catalogs_only(configured, catalogs, monkeypatch):
    capture = _install_structured_llm(monkeypatch, _valid_payload())

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        _fill_inventory_from_schema(INVENTORY_MD, catalogs)

    prompt = capture["prompt"]
    assert INVENTORY_MD in prompt
    assert _render_catalogs(catalogs) in prompt
    assert "```inventory" not in prompt
    assert "## Problema definido" not in prompt
    assert "## Método" not in prompt
    assert "## Raciocínio" not in prompt


def test_fill_returns_model_dump_when_structured_output_is_a_model(
    configured, catalogs, monkeypatch
):
    model = AekoExtractedInventory.model_validate(_valid_payload())
    _install_structured_llm(monkeypatch, model)

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        filled = _fill_inventory_from_schema(INVENTORY_MD, catalogs)

    assert filled == model.model_dump()


def test_fill_raises_when_structured_output_fails(configured, catalogs, monkeypatch):
    _install_structured_llm(monkeypatch, RuntimeError("structured output blew up"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        with pytest.raises(RuntimeError, match="structured output blew up"):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)


def test_fill_raises_when_structured_output_is_not_a_dict_or_model(
    configured, catalogs, monkeypatch
):
    _install_structured_llm(monkeypatch, SimpleNamespace(value="nope"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        with pytest.raises(TypeError):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)


def test_fill_still_records_the_agent_when_invoke_raises(
    configured, catalogs, monkeypatch
):
    _install_structured_llm(monkeypatch, RuntimeError("model down"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID) as run:
        with pytest.raises(RuntimeError):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)
        metrics = run.event_tracking()

    assert [agent.name for agent in metrics.used_agents] == [
        "Coordenador de Melhoria Contínua"
    ]
