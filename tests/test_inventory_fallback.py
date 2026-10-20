"""Unit tests for inventory recovery when salvage cannot parse coordinator JSON."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aeko.config.dto import AekoInventoryCatalogs
from aeko.config.exceptions import MalformedAgentOutputError
from aeko.config.inventory_fallback import (
    INVENTORY_SCHEMA_MAX_RETRIES,
    fill_inventory_schema,
    recover_extracted_inventory,
)
from tests.conftest import catalog_kwargs


@pytest.fixture
def catalogs() -> AekoInventoryCatalogs:
    return AekoInventoryCatalogs.model_validate(catalog_kwargs())


def _valid_payload() -> dict:
    return {
        "description": "Inventario 2023",
        "start_period": "2023-01-01",
        "end_period": "2023-12-31",
        "emissions": [
            {
                "quantity_co2e": 1200.0,
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


def test_salvage_dict_skips_fill_schema(catalogs):
    payload = _valid_payload()
    fill_schema = Mock()
    salvage = Mock(return_value=payload)
    canonicalize = Mock(side_effect=lambda raw, _catalogs: raw)

    result = recover_extracted_inventory(
        "coordinator answer",
        catalogs,
        "inventory markdown body",
        salvage=salvage,
        canonicalize=canonicalize,
        fill_schema=fill_schema,
    )

    salvage.assert_called_once_with("coordinator answer")
    canonicalize.assert_called_once_with(payload, catalogs)
    fill_schema.assert_not_called()
    assert result.description == "Inventario 2023"


def test_salvage_dict_semantic_failure_raises_without_schema(catalogs):
    payload = _valid_payload()
    payload["emissions"][0]["gas"] = 99
    fill_schema = Mock()

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=payload),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
            fill_schema=fill_schema,
        )

    fill_schema.assert_not_called()


def test_fill_schema_receives_markdown_and_catalogs_only(catalogs):
    fill_schema = Mock(return_value=_valid_payload())

    recover_extracted_inventory(
        "must not reach fill_schema",
        catalogs,
        "the inventory markdown",
        salvage=Mock(return_value=None),
        canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
        fill_schema=fill_schema,
    )

    fill_schema.assert_called_once_with("the inventory markdown", catalogs)


def test_schema_succeeds_on_first_attempt_when_salvage_is_none(catalogs):
    fill_schema = Mock(return_value=_valid_payload())

    result = recover_extracted_inventory(
        "answer",
        catalogs,
        "markdown",
        salvage=Mock(return_value=None),
        canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
        fill_schema=fill_schema,
    )

    assert fill_schema.call_count == 1
    assert result.start_period == "2023-01-01"


def test_schema_retries_after_raise_then_succeeds(catalogs):
    valid = _valid_payload()
    fill_schema = Mock(side_effect=[RuntimeError("model blew up"), valid])

    result = recover_extracted_inventory(
        "answer",
        catalogs,
        "markdown",
        salvage=Mock(return_value=None),
        canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
        fill_schema=fill_schema,
    )

    assert fill_schema.call_count == INVENTORY_SCHEMA_MAX_RETRIES
    assert result.description == "Inventario 2023"


def test_non_dict_and_list_roots_consume_schema_attempts(catalogs):
    fill_schema = Mock(side_effect=["not a dict", []])

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
            fill_schema=fill_schema,
        )

    assert fill_schema.call_count == INVENTORY_SCHEMA_MAX_RETRIES


def test_schema_stops_at_cap_without_valid_dict(catalogs):
    fill_schema = Mock(side_effect=[RuntimeError("a"), "text", RuntimeError("b")])

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
            fill_schema=fill_schema,
        )

    assert fill_schema.call_count == INVENTORY_SCHEMA_MAX_RETRIES


def test_semantic_failure_after_schema_does_not_call_schema_again(catalogs):
    bad = _valid_payload()
    bad["emissions"][0]["gas"] = 99
    fill_schema = Mock(return_value=bad)

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
            fill_schema=fill_schema,
        )

    assert fill_schema.call_count == 1


def test_fill_inventory_schema_stub_raises(catalogs):
    with pytest.raises(MalformedAgentOutputError):
        fill_inventory_schema("markdown", catalogs)


def test_default_fill_schema_stub_exhausts_retries(catalogs):
    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
        )


def test_lazy_defaults_import_salvage_and_canonicalize(catalogs, monkeypatch):
    payload = _valid_payload()
    salvage_fn = Mock(return_value=payload)
    canonicalize_fn = Mock(side_effect=lambda raw, _catalogs: raw)
    monkeypatch.setitem(
        sys.modules,
        "aeko.config.inventory_salvage",
        SimpleNamespace(salvage_inventory_payload=salvage_fn),
    )
    monkeypatch.setitem(
        sys.modules,
        "aeko.config.inventory_canonicalize",
        SimpleNamespace(canonicalize_inventory_payload=canonicalize_fn),
    )

    result = recover_extracted_inventory(
        "answer",
        catalogs,
        "markdown",
        fill_schema=Mock(return_value=payload),
    )

    salvage_fn.assert_called_once_with("answer")
    canonicalize_fn.assert_called_once_with(payload, catalogs)
    assert result.emissions[0].gas == 1
