"""Fallback recovery when coordinator inventory JSON cannot be salvaged."""

from pydantic import ValidationError

from aeko.config.dto import AekoExtractedInventory, AekoInventoryCatalogs
from aeko.config.exceptions import MalformedAgentOutputError

INVENTORY_SCHEMA_MAX_RETRIES = 2


def _catalog_validation_context(catalogs: AekoInventoryCatalogs) -> dict:
    """
    Build the Pydantic context an extracted inventory is validated against.

    Args:
        catalogs: The catalogs of the same `analyze()` call.

    Returns:
        dict: Id sets and category classifications keyed for the emission
            validator.
    """

    return {
        "gas_ids": {item.id for item in catalogs.gases},
        "scope_ids": {item.id for item in catalogs.scopes},
        "category_ids": {item.id for item in catalogs.categories},
        "classifications": {
            item.id: item.classification for item in catalogs.categories
        },
    }


def fill_inventory_schema(
    inventory_markdown: str, catalogs: AekoInventoryCatalogs
) -> dict:
    """
    Production hook for constrained schema generation when salvage fails.

    Task 5 replaces this body with a real coordinator call. Until then, callers
    that rely on the default hook receive an explicit failure instead of a
    silent placeholder payload.

    Args:
        inventory_markdown: The inventory document markdown from the API call.
        catalogs: Auxiliary catalogs rendered into the schema prompt.

    Returns:
        dict: A root inventory object matching `AekoExtractedInventory`.

    Raises:
        MalformedAgentOutputError: Always, until task 5 implements generation.
    """

    raise MalformedAgentOutputError(
        "A geração de inventário estruturado por schema ainda não está "
        "disponível nesta versão do SDK."
    )


def _validate_canonical_inventory(
    payload: dict,
    catalogs: AekoInventoryCatalogs,
    canonicalize,
) -> AekoExtractedInventory:
    """
    Canonicalize a raw payload and validate it against the persistence contract.

    Args:
        payload: Raw inventory object from salvage or schema generation.
        catalogs: Catalogs that bound every foreign key in emissions.
        canonicalize: Callable that normalizes shape and derived fields.

    Returns:
        AekoExtractedInventory: The validated inventory ready for Postgres.

    Raises:
        MalformedAgentOutputError: If Pydantic rejects the canonical payload.
    """

    canonical = canonicalize(payload, catalogs)
    context = _catalog_validation_context(catalogs)
    try:
        return AekoExtractedInventory.model_validate(canonical, context=context)
    except ValidationError as exc:
        raise MalformedAgentOutputError(
            f"O inventário estruturado viola o contrato de persistência: {exc}"
        ) from exc


def recover_extracted_inventory(
    answer: str,
    catalogs: AekoInventoryCatalogs,
    inventory_markdown: str,
    *,
    salvage=None,
    canonicalize=None,
    fill_schema=None,
) -> AekoExtractedInventory:
    """
    Recover structured inventory from the coordinator answer or schema fallback.

    Salvage runs first on the coordinator text. When it yields nothing, schema
    generation may run up to `INVENTORY_SCHEMA_MAX_RETRIES` times. Semantic
    validation failures after a dict is obtained do not trigger further schema
    calls.

    Args:
        answer: The coordinator's answer, already stripped of its routing marker.
        catalogs: Auxiliary catalogs for the same analyze call.
        inventory_markdown: Inventory document markdown passed to schema fill.
        salvage: Optional override for `salvage_inventory_payload`.
        canonicalize: Optional override for `canonicalize_inventory_payload`.
        fill_schema: Optional override for `fill_inventory_schema`.

    Returns:
        AekoExtractedInventory: Payload validated against catalogs.

    Raises:
        MalformedAgentOutputError: When salvage and schema recovery both fail,
            or when the recovered payload violates the inventory contract.
    """

    if salvage is None:
        from aeko.config.inventory_salvage import salvage_inventory_payload

        salvage = salvage_inventory_payload
    if canonicalize is None:
        from aeko.config.inventory_canonicalize import canonicalize_inventory_payload

        canonicalize = canonicalize_inventory_payload
    if fill_schema is None:
        fill_schema = fill_inventory_schema

    salvaged = salvage(answer)
    if salvaged is not None:
        return _validate_canonical_inventory(salvaged, catalogs, canonicalize)

    for _ in range(INVENTORY_SCHEMA_MAX_RETRIES):
        try:
            candidate = fill_schema(inventory_markdown, catalogs)
        except Exception:
            continue
        if not isinstance(candidate, dict):
            continue
        return _validate_canonical_inventory(candidate, catalogs, canonicalize)

    raise MalformedAgentOutputError(
        "O Coordenador de Melhoria Contínua não devolveu um inventário "
        "estruturado recuperável, nem após "
        f"{INVENTORY_SCHEMA_MAX_RETRIES} tentativas de schema."
    )
