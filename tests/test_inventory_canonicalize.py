"""Unit tests for deterministic inventory payload canonicalization."""

import pytest


from aeko.config.dto import AekoInventoryCatalogs

from aeko.config.inventory import canonicalize_inventory_payload

from tests.conftest import catalog_kwargs


@pytest.fixture
def catalogs() -> AekoInventoryCatalogs:
    """Catalogs."""
    return AekoInventoryCatalogs.model_validate(catalog_kwargs())


def test_coerces_numeric_and_bool_strings_and_fills_optionals(catalogs):
    """Coerces numeric and bool strings and fills optionals."""
    payload = {
        "emissions": [
            {
                "quantity_co2e": "10",
                "gas": "1",
                "scope": "1",
                "category": "1",
                "is_reduction": "false",
            }
        ]
    }

    result = canonicalize_inventory_payload(payload, catalogs)

    assert result == {
        "description": None,
        "start_period": None,
        "end_period": None,
        "emissions": [
            {
                "quantity_co2e": 10.0,
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


def test_missing_emissions_defaults_to_empty_list(catalogs):
    """Missing emissions defaults to empty list."""
    assert canonicalize_inventory_payload({}, catalogs)["emissions"] == []


def test_reduction_keeps_gas_and_clears_is_upstream(catalogs):
    """Reduction keeps gas and clears is upstream."""
    payload = {
        "emissions": [
            {
                "quantity_co2e": 40,
                "gas": 1,
                "category": 2,
                "is_upstream": True,
                "is_reduction": True,
            }
        ]
    }

    item = canonicalize_inventory_payload(payload, catalogs)["emissions"][0]

    assert item["is_upstream"] is None

    assert item["gas"] == 1


def test_drops_extra_root_and_item_keys(catalogs):
    """Drops extra root and item keys."""
    payload = {
        "id_inventory": 99,
        "description": "x",
        "emissions": [
            {"quantity_co2e": 1, "category": 1, "is_reduction": False, "note": "drop"}
        ],
    }

    result = canonicalize_inventory_payload(payload, catalogs)

    assert "id_inventory" not in result

    assert "note" not in result["emissions"][0]


def test_is_upstream_derived_from_category_classification(catalogs):
    """Is upstream derived from category classification."""
    upstream = canonicalize_inventory_payload(
        {
            "emissions": [
                {
                    "quantity_co2e": 1,
                    "category": 2,
                    "is_reduction": False,
                    "is_upstream": False,
                }
            ]
        },
        catalogs,
    )["emissions"][0]["is_upstream"]

    downstream = canonicalize_inventory_payload(
        {
            "emissions": [
                {
                    "quantity_co2e": 1,
                    "category": 3,
                    "is_reduction": False,
                    "is_upstream": True,
                }
            ]
        },
        catalogs,
    )["emissions"][0]["is_upstream"]

    assert upstream is True

    assert downstream is False


def test_is_upstream_none_when_category_unknown_or_null_classification(catalogs):
    """Is upstream none when category unknown or null classification."""
    missing = canonicalize_inventory_payload(
        {"emissions": [{"quantity_co2e": 1, "category": 999, "is_reduction": False}]},
        catalogs,
    )["emissions"][0]["is_upstream"]

    null_class = canonicalize_inventory_payload(
        {"emissions": [{"quantity_co2e": 1, "category": 1, "is_reduction": False}]},
        catalogs,
    )["emissions"][0]["is_upstream"]

    assert missing is None

    assert null_class is None


def test_null_and_bool_string_coercion_on_optionals(catalogs):
    """Null and bool string coercion on optionals."""
    payload = {
        "description": "NULL",
        "start_period": "2023-01-01",
        "emissions": [
            {
                "quantity_co2e": "12.5",
                "supplier_data_percentage": "50",
                "methodology_description": "false",
                "category": "2",
                "is_reduction": "TRUE",
            }
        ],
    }

    result = canonicalize_inventory_payload(payload, catalogs)

    assert result["description"] is None

    assert result["emissions"][0]["quantity_co2e"] == 12.5

    assert result["emissions"][0]["supplier_data_percentage"] == 50.0

    assert result["emissions"][0]["methodology_description"] == "false"

    assert result["emissions"][0]["category"] == 2

    assert result["emissions"][0]["is_reduction"] is True


def test_does_not_invent_missing_gas_scope_or_quantity(catalogs):
    """Does not invent missing gas scope or quantity."""
    item = canonicalize_inventory_payload(
        {"emissions": [{"category": 1, "is_reduction": False}]},
        catalogs,
    )["emissions"][0]

    assert "quantity_co2e" not in item

    assert "gas" not in item

    assert "scope" not in item

    assert item["methodology_description"] is None

    assert item["supplier_data_percentage"] is None


def test_non_list_emissions_becomes_empty(catalogs):
    """Non list emissions becomes empty."""
    assert (
        canonicalize_inventory_payload({"emissions": "bad"}, catalogs)["emissions"]
        == []
    )


def test_non_dict_payload_yields_empty_inventory(catalogs):
    """Non dict payload yields empty inventory."""
    result = canonicalize_inventory_payload([], catalogs)

    assert result["emissions"] == []

    assert result["description"] is None


def test_missing_is_reduction_derives_is_upstream(catalogs):
    """Missing is reduction derives is upstream."""
    item = canonicalize_inventory_payload(
        {"emissions": [{"quantity_co2e": 1, "category": 2}]},
        catalogs,
    )["emissions"][0]

    assert "is_reduction" not in item

    assert item["is_upstream"] is True


def test_unrecognized_coercion_inputs_pass_through(catalogs):
    """Unrecognized coercion inputs pass through."""
    item = canonicalize_inventory_payload(
        {
            "emissions": [
                {
                    "quantity_co2e": True,
                    "category": {"bad": 1},
                    "gas": {"bad": 1},
                    "is_reduction": "null",
                    "supplier_data_percentage": True,
                }
            ]
        },
        catalogs,
    )["emissions"][0]

    assert item["quantity_co2e"] == 1.0

    assert item["category"] == {"bad": 1}

    assert item["gas"] == {"bad": 1}

    assert item["supplier_data_percentage"] == 1.0

    assert item["is_reduction"] is None

    assert item["is_upstream"] is None


def test_unrecognized_bool_string_is_left_unchanged(catalogs):
    """Unrecognized bool string is left unchanged."""
    item = canonicalize_inventory_payload(
        {
            "emissions": [
                {"quantity_co2e": 1, "category": 1, "is_reduction": "n/a"},
            ]
        },
        catalogs,
    )["emissions"][0]

    assert item["is_reduction"] == "n/a"


def test_uncoercible_numeric_strings_are_left_unchanged(catalogs):
    """Uncoercible numeric strings are left unchanged."""
    item = canonicalize_inventory_payload(
        {
            "emissions": [
                {
                    "quantity_co2e": "n/a",
                    "gas": "CO2",
                    "scope": "1200 tCO2e",
                    "category": 1,
                    "is_reduction": False,
                    "supplier_data_percentage": "n/a",
                }
            ]
        },
        catalogs,
    )["emissions"][0]

    assert item["quantity_co2e"] == "n/a"

    assert item["gas"] == "CO2"

    assert item["scope"] == "1200 tCO2e"

    assert item["supplier_data_percentage"] == "n/a"


def test_null_quantity_and_numeric_catalog_ids(catalogs):
    """Null quantity and numeric catalog ids."""
    item = canonicalize_inventory_payload(
        {
            "emissions": [
                {
                    "quantity_co2e": "null",
                    "gas": True,
                    "scope": 2.0,
                    "category": 1,
                    "is_reduction": 0,
                }
            ]
        },
        catalogs,
    )["emissions"][0]

    assert item["quantity_co2e"] is None

    assert item["gas"] == 1

    assert item["scope"] == 2

    assert item["is_reduction"] == 0


def test_non_numeric_quantity_co2e_is_left_unchanged(catalogs):
    """Non numeric quantity co2e is left unchanged."""
    item = canonicalize_inventory_payload(
        {"emissions": [{"quantity_co2e": [], "category": 1, "is_reduction": False}]},
        catalogs,
    )["emissions"][0]

    assert item["quantity_co2e"] == []


def test_non_dict_emission_entry_is_normalized(catalogs):
    """Non dict emission entry is normalized."""
    item = canonicalize_inventory_payload({"emissions": ["skip"]}, catalogs)[
        "emissions"
    ][0]

    assert item == {
        "methodology_description": None,
        "supplier_data_percentage": None,
        "is_upstream": None,
    }


def test_preserves_native_python_types(catalogs):
    """Preserves native python types."""
    payload = {
        "description": "ok",
        "emissions": [
            {
                "quantity_co2e": 3,
                "gas": None,
                "scope": 1,
                "category": 2,
                "is_reduction": False,
                "is_upstream": True,
            }
        ],
    }

    item = canonicalize_inventory_payload(payload, catalogs)["emissions"][0]

    assert item["quantity_co2e"] == 3.0

    assert item["gas"] is None

    assert item["is_upstream"] is True
