"""Deterministic normalization of coordinator inventory JSON before validation."""

from aeko.config.dto import AekoInventoryCatalogs

_ROOT_KEYS = frozenset({"description", "start_period", "end_period", "emissions"})
_ITEM_KEYS = frozenset(
    {
        "quantity_co2e",
        "methodology_description",
        "supplier_data_percentage",
        "gas",
        "scope",
        "category",
        "is_upstream",
        "is_reduction",
    }
)
_ROOT_OPTIONAL = ("description", "start_period", "end_period")
_ITEM_OPTIONAL = ("methodology_description", "supplier_data_percentage")
_ITEM_INTS = ("gas", "scope", "category")


def _coerce_null_string(value: object) -> object:
    if isinstance(value, str) and value.casefold() == "null":
        return None
    return value


def _coerce_bool(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.casefold()
        if folded == "true":
            return True
        if folded == "false":
            return False
        if folded == "null":
            return None
    return value


def _coerce_float(value: object) -> object:
    value = _coerce_null_string(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return value


def _coerce_int(value: object) -> object:
    value = _coerce_null_string(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(float(value))
    return value


def _category_classifications(catalogs: AekoInventoryCatalogs) -> dict[int, str | None]:
    return {row.id: row.classification for row in catalogs.categories}


def _derive_is_upstream(
    category_id: object,
    classifications: dict[int, str | None],
    is_reduction: bool,
) -> bool | None:
    if is_reduction:
        return None
    if category_id is None or not isinstance(category_id, int):
        return None
    classification = classifications.get(category_id)
    if classification == "UPSTREAM":
        return True
    if classification == "DOWNSTREAM":
        return False
    return None


def _canonicalize_emission_item(
    raw: object,
    classifications: dict[int, str | None],
) -> dict:
    source = raw if isinstance(raw, dict) else {}
    filtered = {key: source[key] for key in source if key in _ITEM_KEYS}
    item: dict = {}

    if "quantity_co2e" in filtered:
        item["quantity_co2e"] = _coerce_float(filtered["quantity_co2e"])

    for key in _ITEM_INTS:
        if key in filtered:
            item[key] = _coerce_int(filtered[key])

    for key in _ITEM_OPTIONAL:
        if key in filtered:
            coerced = _coerce_null_string(filtered[key])
            if key == "supplier_data_percentage" and coerced is not None:
                item[key] = _coerce_float(coerced)
            else:
                item[key] = coerced
        else:
            item[key] = None

    is_reduction = False
    if "is_reduction" in filtered:
        coerced = _coerce_bool(filtered["is_reduction"])
        item["is_reduction"] = coerced
        is_reduction = coerced is True

    category_id = item.get("category")
    item["is_upstream"] = _derive_is_upstream(category_id, classifications, is_reduction)

    return item


def canonicalize_inventory_payload(payload: dict, catalogs: AekoInventoryCatalogs) -> dict:
    """
    Normalize a salvaged inventory dict to the extracted-inventory contract shape.

    Coerces stringified numbers and booleans, fills missing optional fields with
    None, defaults missing ``emissions`` to an empty list, drops unknown keys,
    and derives ``is_upstream`` from category catalog classifications. Does not
    validate foreign keys or invent missing ``gas``, ``scope``, or ``quantity_co2e``.

    Args:
        payload: Raw JSON object from the coordinator.
        catalogs: Gas, scope and category rows for the same ``analyze()`` call.

    Returns:
        dict: A contract-shaped inventory payload ready for Pydantic validation.
    """

    source = payload if isinstance(payload, dict) else {}
    root = {key: source[key] for key in source if key in _ROOT_KEYS}
    result: dict = {}

    for key in _ROOT_OPTIONAL:
        if key in root:
            result[key] = _coerce_null_string(root[key])
        else:
            result[key] = None

    emissions_raw = root.get("emissions", [])
    if not isinstance(emissions_raw, list):
        emissions_raw = []

    classifications = _category_classifications(catalogs)
    result["emissions"] = [
        _canonicalize_emission_item(entry, classifications) for entry in emissions_raw
    ]

    return result
