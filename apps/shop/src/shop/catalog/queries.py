"""Lecturas del catálogo que comparten el API y el recomendador, y los `$n` de una lista."""

from __future__ import annotations

from minigestor_client import AsyncClient, Record

from .schema import CLIP, PRODUCTS

FIRST_PARAMETER = 1
PRODUCT_COLUMNS = (
    "id, titulo, marca, departamento, categoria, ruta_categoria, precio, valoracion, "
    "valoraciones, imagen"
)
CLIP_SEARCH = (
    f"SELECT id, SIMILARITY_SCORE() AS puntaje FROM {CLIP} "
    "WHERE SIMILAR_TO(embedding, $1, k = $2) USING HNSW"
)


def placeholders(count: int, first: int = FIRST_PARAMETER) -> str:
    """`$first, $first+1, …`: `count` parámetros seguidos, para un `IN (...)`."""
    return ", ".join(f"${number}" for number in range(first, first + count))


def products_by_ids(count: int) -> str:
    """Las fichas de `count` productos, con un parámetro por id."""
    return f"SELECT {PRODUCT_COLUMNS} FROM {PRODUCTS} WHERE id IN ({placeholders(count)})"


async def fetch_products(engine: AsyncClient, ids: list[str]) -> dict[str, Record]:
    """Las fichas de `ids` por id; las que no existen no están."""
    if not ids:
        return {}
    found = await engine.fetch_all(products_by_ids(len(ids)), ids)
    return {str(row["id"]): row for row in found}
