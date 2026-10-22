"""Tests for constrained inventory schema fill used after salvage fails."""

from types import SimpleNamespace

from unittest.mock import Mock


import pytest

from pydantic import ValidationError


from aeko import Aeko

from aeko.config.constants import INVENTORY_LOG_MODULE

from aeko.config.dto import AekoExtractedInventory, AekoInventoryCatalogs

from aeko.config.exceptions import MalformedAgentOutputError

from aeko.config.inventory import (
    _InventoryFillShape,
    _fill_inventory_from_schema,
    _render_catalogs,
)

from aeko.config.inventory import recover_extracted_inventory

from aeko.engine.runtime import RUNTIME

from aeko.shared import Flow, processing

from tests.conftest import catalog_kwargs

from tests.test_config import API_KEY, REQUEST_ID


INVENTORY_MD = "| Escopo | tCO2e |\n|---|---|\n| 1 | 1200 |"


@pytest.fixture
def configured():
    """Configured."""
    Aeko.config(API_KEY)


@pytest.fixture
def catalogs() -> AekoInventoryCatalogs:
    """Catalogs."""
    return AekoInventoryCatalogs.model_validate(catalog_kwargs())


def _valid_payload() -> dict:
    """Valid payload."""
    return {
        "description": "Inventario 2023",
        "start_period": "2023-01-01",
        "end_period": "2023-12-31",
        "emissions": [],
    }


def _install_structured_llm(monkeypatch, result, *, capture=None):
    """Install structured llm."""
    capture = capture if capture is not None else {}

    class Structured:
        def invoke(self, prompt, config=None):
            """Invoke."""
            capture["prompt"] = prompt

            capture["config"] = config

            capture["callbacks"] = None if config is None else config.get("callbacks")

            if isinstance(result, BaseException):
                raise result

            return result

    class SlowLlm:
        def with_structured_output(self, schema):
            """With structured output."""
            capture["schema"] = schema

            return Structured()

    def _create_llms(max_tokens=None):
        """Create llms."""
        capture["max_tokens"] = max_tokens

        return object(), SlowLlm()

    monkeypatch.setattr("aeko.config.inventory.create_llms", _create_llms)

    return capture


def test_fill_records_coordinator_agent_call(configured, catalogs, monkeypatch):
    """Fill records coordinator agent call."""
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

    assert capture["schema"] is _InventoryFillShape


def test_fill_prompt_is_markdown_and_catalogs_only(configured, catalogs, monkeypatch):
    """Fill prompt is markdown and catalogs only."""
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
    """Fill returns model dump when structured output is a model."""
    model = AekoExtractedInventory.model_validate(_valid_payload())

    _install_structured_llm(monkeypatch, model)

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        filled = _fill_inventory_from_schema(INVENTORY_MD, catalogs)

    assert filled == model.model_dump()


def test_fill_raises_when_structured_output_fails(configured, catalogs, monkeypatch):
    """Fill raises when structured output fails."""
    _install_structured_llm(monkeypatch, RuntimeError("structured output blew up"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        with pytest.raises(RuntimeError, match="structured output blew up"):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)


def test_fill_raises_when_structured_output_is_not_a_dict_or_model(
    configured, catalogs, monkeypatch
):
    """Fill raises when structured output is not a dict or model."""
    _install_structured_llm(monkeypatch, SimpleNamespace(value="nope"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        with pytest.raises(TypeError):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)


def _negative_quantity_payload() -> dict:
    """Negative quantity payload."""
    return {
        "description": "Inventario 2023",
        "start_period": "2023-01-01",
        "end_period": "2023-12-31",
        "emissions": [
            {
                "quantity_co2e": -1,
                "methodology_description": None,
                "supplier_data_percentage": None,
                "gas": 1,
                "scope": 1,
                "category": 1,
                "is_upstream": None,
                "is_reduction": False,
            }
        ],
    }


def test_fill_returns_dict_when_structured_output_quantity_is_negative(
    configured, catalogs, monkeypatch
):
    """Fill returns dict when structured output quantity is negative."""
    payload = _negative_quantity_payload()

    capture = {}

    class Structured:
        def invoke(self, prompt, config=None):
            """Invoke."""
            return capture["schema"].model_validate(payload)

    class SlowLlm:
        def with_structured_output(self, schema):
            """With structured output."""
            capture["schema"] = schema

            return Structured()

    monkeypatch.setattr(
        "aeko.config.inventory.create_llms",
        lambda max_tokens=None: (object(), SlowLlm()),
    )

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        filled = _fill_inventory_from_schema(INVENTORY_MD, catalogs)

    assert filled["emissions"][0]["quantity_co2e"] == -1

    with pytest.raises(ValidationError):
        AekoExtractedInventory.model_validate(filled)


def test_recover_does_not_retry_fill_when_schema_returns_negative_quantity(
    configured, catalogs, monkeypatch
):
    """Recover does not retry fill when schema returns negative quantity."""
    payload = _negative_quantity_payload()

    capture = {"invoke_count": 0}

    class Structured:
        def invoke(self, prompt, config=None):
            """Invoke."""
            capture["invoke_count"] += 1

            return capture["schema"].model_validate(payload)

    class SlowLlm:
        def with_structured_output(self, schema):
            """With structured output."""
            capture["schema"] = schema

            return Structured()

    monkeypatch.setattr(
        "aeko.config.inventory.create_llms",
        lambda max_tokens=None: (object(), SlowLlm()),
    )

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID):
        with pytest.raises(MalformedAgentOutputError):
            recover_extracted_inventory(
                "answer",
                catalogs,
                INVENTORY_MD,
                salvage=Mock(return_value=None),
                fill_schema=_fill_inventory_from_schema,
            )

    assert capture["invoke_count"] == 1


def test_fill_still_records_the_agent_when_invoke_raises(
    configured, catalogs, monkeypatch
):
    """Fill still records the agent when invoke raises."""
    _install_structured_llm(monkeypatch, RuntimeError("model down"))

    with processing(Flow.REPORT, INVENTORY_LOG_MODULE, REQUEST_ID) as run:
        with pytest.raises(RuntimeError):
            _fill_inventory_from_schema(INVENTORY_MD, catalogs)

        metrics = run.event_tracking()

    assert [agent.name for agent in metrics.used_agents] == [
        "Coordenador de Melhoria Contínua"
    ]
