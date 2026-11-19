"""Los filtros del catálogo (departamento y precio) como condiciones del `WHERE` del gestor.

Cada valor viaja como parámetro (`$n`); el texto del filtro solo pone columnas y operadores
fijos. Un producto sin precio no pasa un filtro de precio: es la semántica de `NULL` en SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

from appkit import BadRequestError
from shop.catalog.schema import DEPARTMENT_LENGTH

CONDITION_SEPARATOR = " AND "


class InvalidPriceRangeError(BadRequestError):
    """El precio mínimo pedido es mayor que el máximo."""


@dataclass(frozen=True, slots=True)
class CatalogFilter:
    """Lo que se pide además de la búsqueda; `None` es «sin filtro»."""

    department: str | None = None
    min_price: float | None = None
    max_price: float | None = None

    def __post_init__(self) -> None:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise InvalidPriceRangeError(
                f"el precio mínimo ({self.min_price:g}) es mayor que el máximo "
                f"({self.max_price:g})"
            )

    @property
    def is_empty(self) -> bool:
        return self.department is None and self.min_price is None and self.max_price is None

    def conditions(self, first_parameter: int) -> tuple[str, list[object]]:
        """Las condiciones `AND` del filtro, numeradas desde `$first_parameter`, y sus
        valores en el mismo orden. Sin filtro, `("", [])`."""
        fragments: list[str] = []
        values: list[object] = []
        for column_condition, value in (
            ("departamento = ", self.department),
            ("precio >= ", self.min_price),
            ("precio <= ", self.max_price),
        ):
            if value is None:
                continue
            fragments.append(f"{column_condition}${first_parameter + len(values)}")
            values.append(value)
        return CONDITION_SEPARATOR.join(fragments), values


def catalog_filter(
    departamento: Annotated[
        str | None, Query(min_length=1, max_length=DEPARTMENT_LENGTH)
    ] = None,
    precio_min: Annotated[float | None, Query(ge=0)] = None,
    precio_max: Annotated[float | None, Query(ge=0)] = None,
) -> CatalogFilter:
    """El filtro de una búsqueda, de sus parámetros de consulta.

    Raises:
        InvalidPriceRangeError: si `precio_min` es mayor que `precio_max`.
    """
    return CatalogFilter(departamento, precio_min, precio_max)


Filter = Annotated[CatalogFilter, Depends(catalog_filter)]
