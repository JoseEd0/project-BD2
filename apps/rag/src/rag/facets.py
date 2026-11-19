"""Los valores de los filtros de búsqueda: los años y las categorías del corpus, con cuántos
papers tiene cada uno. Dos agregaciones sobre `rag_papers`, que tiene un registro por paper."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from minigestor_client import AsyncClient

from .schema import PAPERS_TABLE

YEARS_SQL = (
    f"SELECT year, COUNT(*) AS papers FROM {PAPERS_TABLE} "
    "WHERE year IS NOT NULL GROUP BY year ORDER BY year"
)
CATEGORIES_SQL = (
    f"SELECT primary_category, COUNT(*) AS papers FROM {PAPERS_TABLE} "
    "WHERE primary_category IS NOT NULL GROUP BY primary_category ORDER BY primary_category"
)


class YearFacet(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    papers: int


class CategoryFacet(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    papers: int


class Facets(BaseModel):
    """Los años (sin los documentos que no tienen uno) y las categorías primarias, en orden."""

    years: list[YearFacet]
    categories: list[CategoryFacet]


async def facets_of(engine: AsyncClient) -> Facets:
    """Los años y las categorías de `rag_papers` con su número de papers."""
    years = await engine.fetch_all(YEARS_SQL)
    categories = await engine.fetch_all(CATEGORIES_SQL)
    return Facets(
        years=[YearFacet(year=row["year"], papers=row["papers"]) for row in years],
        categories=[
            CategoryFacet(category=row["primary_category"], papers=row["papers"])
            for row in categories
        ],
    )
