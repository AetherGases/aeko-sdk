"""Unit tests for inventory recovery when salvage cannot parse coordinator JSON."""

from unittest.mock import Mock


import pytest


from aeko.config.dto import AekoInventoryCatalogs

from aeko.config.exceptions import MalformedAgentOutputError

from aeko.config.constants import INVENTORY_SCHEMA_MAX_RETRIES

from aeko.config.inventory import (
    fill_inventory_schema,
    recover_extracted_inventory,
)

from tests.conftest import catalog_kwargs


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
    """Salvage dict skips fill schema."""
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
    """Salvage dict semantic failure raises without schema."""
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


def test_uncoercible_quantity_string_raises_malformed_without_schema(catalogs):
    """Uncoercible quantity string raises malformed without schema."""
    payload = _valid_payload()

    payload["emissions"][0]["quantity_co2e"] = "n/a"

    payload["emissions"][0]["gas"] = "CO2"

    fill_schema = Mock()

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=payload),
            fill_schema=fill_schema,
        )

    fill_schema.assert_not_called()


def test_canonicalize_value_error_is_mapped_to_malformed(catalogs):
    """Canonicalize value error is mapped to malformed."""
    fill_schema = Mock()

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=_valid_payload()),
            canonicalize=Mock(side_effect=ValueError("cannot coerce")),
            fill_schema=fill_schema,
        )

    fill_schema.assert_not_called()


def test_canonicalize_type_error_is_mapped_to_malformed(catalogs):
    """Canonicalize type error is mapped to malformed."""
    fill_schema = Mock()

    with pytest.raises(MalformedAgentOutputError):
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=_valid_payload()),
            canonicalize=Mock(side_effect=TypeError("cannot coerce")),
            fill_schema=fill_schema,
        )

    fill_schema.assert_not_called()


def test_fill_schema_receives_markdown_and_catalogs_only(catalogs):
    """Fill schema receives markdown and catalogs only."""
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
    """Schema succeeds on first attempt when salvage is none."""
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
    """Schema retries after raise then succeeds."""
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
    """Non dict and list roots consume schema attempts."""
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
    """Schema stops at cap without valid dict."""
    first = RuntimeError("a")

    fill_schema = Mock(side_effect=[first, "text"])

    with pytest.raises(MalformedAgentOutputError) as captured:
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
            fill_schema=fill_schema,
        )

    assert fill_schema.call_count == INVENTORY_SCHEMA_MAX_RETRIES

    assert captured.value.__cause__ is first


def test_semantic_failure_after_schema_does_not_call_schema_again(catalogs):
    """Semantic failure after schema does not call schema again."""
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


def test_fill_inventory_schema_delegates_to_production_helper(catalogs, monkeypatch):
    """Fill inventory schema delegates to production helper."""
    payload = _valid_payload()

    helper = Mock(return_value=payload)

    monkeypatch.setattr(
        "aeko.config.inventory._fill_inventory_from_schema",
        helper,
    )

    result = fill_inventory_schema("markdown", catalogs)

    helper.assert_called_once_with("markdown", catalogs)

    assert result == payload


def test_default_fill_schema_exhausts_retries(catalogs, monkeypatch):
    """Default fill schema exhausts retries."""
    boom = RuntimeError("model blew up")

    helper = Mock(side_effect=boom)

    monkeypatch.setattr(
        "aeko.config.inventory._fill_inventory_from_schema",
        helper,
    )

    with pytest.raises(MalformedAgentOutputError) as captured:
        recover_extracted_inventory(
            "answer",
            catalogs,
            "markdown",
            salvage=Mock(return_value=None),
            canonicalize=Mock(side_effect=lambda raw, _catalogs: raw),
        )

    assert helper.call_count == INVENTORY_SCHEMA_MAX_RETRIES

    assert captured.value.__cause__ is boom


def test_lazy_defaults_import_salvage_and_canonicalize(catalogs, monkeypatch):
    """Lazy defaults import salvage and canonicalize."""
    payload = _valid_payload()

    salvage_fn = Mock(return_value=payload)

    canonicalize_fn = Mock(side_effect=lambda raw, _catalogs: raw)

    monkeypatch.setattr(
        "aeko.config.inventory.salvage_inventory_payload",
        salvage_fn,
    )

    monkeypatch.setattr(
        "aeko.config.inventory.canonicalize_inventory_payload",
        canonicalize_fn,
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
